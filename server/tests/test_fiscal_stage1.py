"""Етап 1 Checkbox: ізольоване тестове середовище.

FakeCheckboxServer — емулятор HTTP-API Checkbox (за офіційною OpenAPI: signinPinCode, shifts, receipts/sell,
receipts/{id}, shifts/close, reports). Справжній CheckboxProvider працює з ним через підмінений HTTP-транспорт —
мережа не використовується, реальних фіскальних операцій немає. Усі чеки — НЕФІСКАЛЬНІ.
"""

import json
import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from mds import clock
from mds.errors import Forbidden, ValidationError
from mds.models import FiscalOperation, FiscalReceipt, Order, Setting
from mds.services import bookings, fiscal, orders
from mds.services.common import update_settings
from mds.services.fiscal_providers import CheckboxProvider, FakePRROProvider, Resp, get_provider, set_provider

LICENSE, PIN = "TEST-LICENSE-KEY-0001", "9876"
T0 = datetime(2026, 11, 2, 10, 0)


class FakeCheckboxServer:
    """Емулятор Checkbox: зберігає чеки за id (UUID клієнта), зміну, звіти. Режими збоїв — fail_next."""

    def __init__(self):
        self.receipts, self.shift, self.calls, self.fail_next = {}, None, [], []
        self.serial = 0

    def __call__(self, method, url, headers, data, timeout):
        path = url.split("api.test", 1)[1]
        body = json.loads(data) if data else None
        self.calls.append((method, path, body, dict(headers)))
        mode = self.fail_next.pop(0) if self.fail_next else None
        if mode == "timeout_before":
            raise TimeoutError()
        if path == "/api/v1/cashier/signinPinCode":
            if headers.get("X-License-Key") != LICENSE or body.get("pin_code") != PIN:
                return Resp(403, json.dumps({"message": "Невірний пін-код або ключ ліцензії"}).encode())
            return Resp(200, b'{"access_token": "JWT-TEST"}')
        if headers.get("Authorization") != "Bearer JWT-TEST":
            return Resp(401, b'{"message":"Unauthorized"}')
        if path == "/api/v1/shifts" and method == "POST":
            self.shift = {"id": body["id"], "status": "OPENED", "sales": 0, "returns": 0}
            return Resp(202, json.dumps(self.shift).encode())
        if path == "/api/v1/cashier/shift":
            return Resp(200, json.dumps(self.shift).encode() if self.shift else b"null")
        if path == "/api/v1/shifts/close":
            if not self.shift:
                return Resp(400, b'{"message":"no shift"}')
            z = {"is_z_report": True, "sales": self.shift["sales"], "returns": self.shift["returns"]}
            self.shift = None
            return Resp(202, json.dumps({"status": "CLOSING", "z_report": z}).encode())
        if path == "/api/v1/reports" and method == "POST":
            return Resp(201, json.dumps({"is_z_report": False, **(self.shift or {})}).encode())
        if path == "/api/v1/receipts/sell":
            if mode == "reject":
                return Resp(422, json.dumps({"message": "Validation error", "detail": [{"msg": "invalid"}]}).encode())
            if mode == "server_error":
                return Resp(502, b"")
            if not self.shift:
                return Resp(400, json.dumps({"message": "Зміну не відкрито"}).encode())
            rid = body["id"]
            if rid not in self.receipts:  # той самий id — без нового чека
                self.serial += 1
                ret = body["goods"][0]["is_return"]
                total = sum(g["good"]["price"] * g["quantity"] // 1000 for g in body["goods"])
                self.shift["returns" if ret else "sales"] += total
                self.receipts[rid] = {"id": rid, "status": "DONE", "fiscal_code": f"TEST-CB-{self.serial}",
                                      "tax_url": f"https://check.test/{rid}", "total_sum": total, "body": body}
            if mode == "timeout_after":  # чек створено, але відповідь втрачено
                raise TimeoutError()
            return Resp(201, json.dumps({k: v for k, v in self.receipts[rid].items() if k != "body"}).encode())
        if path.startswith("/api/v1/receipts/"):
            r = self.receipts.get(path.rsplit("/", 1)[1])
            return Resp(200, json.dumps({k: v for k, v in r.items() if k != "body"}).encode()) if r else Resp(404, b'{"message":"Not found"}')
        return Resp(404, b"{}")


@pytest.fixture
def cb(db, seed):
    srv = FakeCheckboxServer()
    prov = CheckboxProvider(mode="test", base_url="https://api.test", license_key=LICENSE, pin_code=PIN, http=srv)
    set_provider(prov)
    with db.session() as s:
        fiscal.update_config(s, seed["actors"]["director"], {"enabled": True, "mode": "test"})
    yield srv, prov
    set_provider(None)


def _close(db, seed, amount=60000, pay="cash", hour=10, key=None, phone=None):
    clock.set_now(T0.replace(hour=hour) - timedelta(hours=1))
    with db.session() as s:
        aid = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=seed["haircut"],
                                          starts_at=T0.replace(hour=hour), client_name="Клієнт",
                                          client_phone=phone or f"06{hour:02d}1112233"[:10]).id
    clock.set_now(T0.replace(hour=hour, minute=50))
    with db.session() as s:
        return orders.close_order(s, seed["actors"]["m1"], aid, amount=amount, payment_method=pay, idempotency_key=key).id


def _receipt(db, oid, kind="sell"):
    with db.session() as s:
        return fiscal.receipt_public(s.scalar(select(FiscalReceipt).where(FiscalReceipt.order_id == oid, FiscalReceipt.kind == kind)
                                              .order_by(FiscalReceipt.id.desc())))


# ------------------------------------------------------------------ режими і безпека
def test_production_mode_locked_without_server_permission(db, seed, monkeypatch):
    d = seed["actors"]["director"]
    monkeypatch.delenv("MDS_FISCAL_PRODUCTION_ALLOWED", raising=False)
    with db.session() as s:
        with pytest.raises(Forbidden):
            fiscal.update_config(s, d, {"mode": "production"})
        with pytest.raises(Forbidden):
            fiscal.update_config(s, seed["actors"]["admin"], {"mode": "test"})
        with pytest.raises(ValidationError):  # загальні налаштування не змінюють ПРРО
            update_settings(s, d, {"fiscal_mode": "production"})
        assert fiscal.mode(s) == "mock" and fiscal.config(s, d)["nonfiscal"]
        # навіть якщо значення потрапило в БД в обхід — бойовий провайдер не створюється
        s.add(Setting(key="fiscal_mode", value="production"))
    from mds.services.fiscal_providers import ProviderRejected
    with pytest.raises(ProviderRejected):
        get_provider("production")
    monkeypatch.setenv("MDS_FISCAL_PRODUCTION_ALLOWED", "yes")
    with db.session() as s:
        assert fiscal.config(s, d)["production_allowed"] and not fiscal.config(s, d)["nonfiscal"]


def test_test_and_production_keys_are_separate(monkeypatch):
    monkeypatch.setenv("MDS_CHECKBOX_TEST_LICENSE_KEY", "T-LIC")
    monkeypatch.setenv("MDS_CHECKBOX_TEST_PIN", "1111")
    monkeypatch.setenv("MDS_CHECKBOX_LICENSE_KEY", "PROD-LIC")
    monkeypatch.setenv("MDS_CHECKBOX_PIN", "2222")
    t, p = CheckboxProvider(mode="test"), CheckboxProvider(mode="production")
    assert t._license == "T-LIC" and t._pin == "1111" and p._license == "PROD-LIC"
    assert "T-LIC" not in repr(t) and "PROD-LIC" not in repr(p)


def test_secrets_never_in_journal_or_logs(db, seed, cb, caplog):
    srv, prov = cb
    caplog.set_level(logging.DEBUG)
    prov._pin = "0000-wrong"
    srv.fail_next = []
    with db.session() as s:
        st = fiscal.check_connection(db, seed["actors"]["director"])
    assert not st["ok"] and "авторизації" in st["error"]
    with db.session() as s:
        ops = fiscal.operations(s, seed["actors"]["director"])
    blob = json.dumps(ops, ensure_ascii=False) + caplog.text
    for secret in (LICENSE, "0000-wrong", "JWT-TEST"):
        assert secret not in blob
    assert ops[0]["op"] == "ping" and not ops[0]["ok"] and ops[0]["nonfiscal"]


# ------------------------------------------------------------------ сценарії через емулятор Checkbox
def test_shift_open_sell_status_link_close(db, seed, cb):
    srv, prov = cb
    d = seed["actors"]["director"]
    assert fiscal.check_connection(db, d)["ok"]
    fiscal.open_shift(db, d)
    oid = _close(db, seed, amount=60000, pay="card")
    fiscal.process_pending(db)
    r = _receipt(db, oid)
    assert r["status"] == "done" and r["fiscal_code"].startswith("TEST-CB-") and r["tax_url"].startswith("https://")
    assert r["nonfiscal"] and r["provider"] == "test"
    # що пішло в Checkbox: назва послуги, кількість, ціна, сума, оплата, номер замовлення
    body = srv.receipts[r["uuid"]]["body"]
    assert body["goods"] == [{"good": {"code": f"S{seed['haircut']}", "name": "Стрижка", "price": 60000},
                              "quantity": 1000, "is_return": False}]
    assert body["payments"] == [{"type": "CASHLESS", "value": 60000}] and body["order_id"] == f"MDS-{oid}"
    with db.session() as s:
        assert s.get(Order, oid).amount == srv.receipts[r["uuid"]]["total_sum"] == 60000
    z = fiscal.close_shift(db, d)
    assert z["status"] == "closed" and z["z_report"]["sales"] == 60000


def test_auth_error_marks_rejected_and_journals(db, seed, cb):
    srv, prov = cb
    prov._pin = "bad"
    oid = _close(db, seed)
    fiscal.process_pending(db)
    r = _receipt(db, oid)
    assert r["status"] == "rejected" and "авторизації" in r["last_error"]
    with db.session() as s:
        op = s.scalar(select(FiscalOperation).order_by(FiscalOperation.id.desc()))
        assert op.op == "sell" and not op.ok and op.receipt_id == r["id"]
    prov._pin = PIN  # виправили PIN → повтор
    fiscal.open_shift(db, seed["actors"]["director"])
    assert fiscal.retry(db, seed["actors"]["director"], r["id"]) == "done"


def test_rejected_receipt_validation_error(db, seed, cb):
    srv, _ = cb
    fiscal.open_shift(db, seed["actors"]["director"])
    oid = _close(db, seed)
    srv.fail_next = ["reject"]
    fiscal.process_pending(db)
    r = _receipt(db, oid)
    assert r["status"] == "rejected" and "422" in r["last_error"]
    assert not srv.receipts


def test_timeout_after_send_no_duplicate(db, seed, cb):
    srv, _ = cb
    fiscal.open_shift(db, seed["actors"]["director"])
    oid = _close(db, seed)
    srv.fail_next = ["timeout_after"]
    fiscal.process_pending(db)
    r = _receipt(db, oid)
    assert r["status"] == "error" and len(srv.receipts) == 1  # чек у Checkbox є, відповіді не було
    clock.set_now(datetime.fromisoformat(r["next_attempt_at"]))
    fiscal.process_pending(db)
    r = _receipt(db, oid)
    assert r["status"] == "done" and len(srv.receipts) == 1
    sells = [c for c in srv.calls if c[1] == "/api/v1/receipts/sell"]
    assert len(sells) == 1 and any(c[1] == f"/api/v1/receipts/{r['uuid']}" for c in srv.calls)


def test_unavailable_and_recovery(db, seed, cb):
    srv, _ = cb
    fiscal.open_shift(db, seed["actors"]["director"])
    oid = _close(db, seed)
    srv.fail_next = ["timeout_before"]
    fiscal.process_pending(db)  # мережа: тайм-аут до відправки
    r = _receipt(db, oid)
    assert r["status"] == "error"
    clock.set_now(datetime.fromisoformat(r["next_attempt_at"]))
    srv.fail_next = ["timeout_before"]  # друга спроба: недоступна навіть перевірка чека (GET)
    fiscal.process_pending(db)
    r = _receipt(db, oid)
    assert r["status"] == "error"
    clock.set_now(datetime.fromisoformat(r["next_attempt_at"]))
    fiscal.process_pending(db)
    assert _receipt(db, oid)["status"] == "done" and len(srv.receipts) == 1
    with db.session() as s:
        assert s.get(Order, oid).amount == 60000  # замовлення не постраждало


def test_repeat_processing_and_double_close_one_receipt(db, seed, cb):
    srv, _ = cb
    fiscal.open_shift(db, seed["actors"]["director"])
    oid = _close(db, seed, key="dbl")
    with db.session() as s:
        aid = s.get(Order, oid).appointment_id
    with db.session() as s:
        assert orders.close_order(s, seed["actors"]["m1"], aid, amount=60000, payment_method="cash", idempotency_key="dbl").id == oid
    rid = _receipt(db, oid)["id"]
    for _ in range(3):
        fiscal.process_pending(db)
        fiscal.process_receipt(db, rid)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(FiscalReceipt).where(FiscalReceipt.order_id == oid)) == 1
    assert len(srv.receipts) == 1


def test_refund_via_checkbox(db, seed, cb):
    srv, _ = cb
    d = seed["actors"]["director"]
    fiscal.open_shift(db, d)
    oid = _close(db, seed, amount=60000, pay="cash")
    fiscal.process_pending(db)
    sell = _receipt(db, oid)
    res = fiscal.refund(db, d, oid, 15000, "Часткове повернення")
    assert res["status"] == "done"
    ret = _receipt(db, oid, "return")
    body = srv.receipts[ret["uuid"]]["body"]
    assert body["goods"][0]["is_return"] and body["goods"][0]["good"]["price"] == 15000
    assert body["related_receipt_id"] == sell["uuid"] and body["payments"] == [{"type": "CASH", "value": 15000}]
    with db.session() as s:
        assert s.get(Order, oid).amount == 45000
        rec = fiscal.reconcile(s, d, T0.date(), T0.date())
    assert rec["ok"] and rec["totals"]["fiscal_net"] == 45000


# ------------------------------------------------------------------ тестова панель
def test_test_receipt_is_nonfiscal_and_forbidden_in_production(db, seed, monkeypatch):
    d = seed["actors"]["director"]
    fp = FakePRROProvider()  # зміни немає — тестовий чек сам відкриє її і пройде без фонового циклу
    set_provider(fp)
    try:
        r = fiscal.test_receipt(db, d, 12345, "card")
        assert r["status"] == "done" and r["nonfiscal"] and r["order_id"] is None
        assert r["goods"][0]["name"] == "ТЕСТОВИЙ ЧЕК — НЕФІСКАЛЬНИЙ"
        with pytest.raises(Forbidden):
            fiscal.test_receipt(db, seed["actors"]["m1"], 100)
        with pytest.raises(ValidationError):
            fiscal.test_receipt(db, d, 0)
        monkeypatch.setenv("MDS_FISCAL_PRODUCTION_ALLOWED", "yes")
        with db.session() as s:
            fiscal.update_config(s, d, {"mode": "production"})
        with pytest.raises(Forbidden):
            fiscal.test_receipt(db, d, 100)
        with db.session() as s:  # тестові чеки не впливають на звірку замовлень
            rec = fiscal.reconcile(s, d, clock.now().date(), clock.now().date())
            assert rec["totals"]["fiscal_sales"] == 0
    finally:
        set_provider(None)


def test_selftest_isolated_and_all_pass(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        before = (s.scalar(select(func.count()).select_from(Order)), s.scalar(select(func.count()).select_from(FiscalReceipt)))
    out = fiscal.selftest(db, d)
    assert out["failed"] == 0 and out["passed"] == len(out["results"]) >= 9, out["results"]
    assert out["isolated"]
    with db.session() as s:
        after = (s.scalar(select(func.count()).select_from(Order)), s.scalar(select(func.count()).select_from(FiscalReceipt)))
        assert before == after  # робочі дані не змінились
        assert fiscal.status_summary(s, d)["selftest"]["passed"] == out["passed"]
        assert s.scalar(select(FiscalOperation).where(FiscalOperation.op == "selftest")).ok


def test_panel_api_permissions(client_api):
    c, ids, d = client_api
    tok = lambda u, p: {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]}  # noqa: E731
    m, a = tok("olena", "Master123"), tok("admin", "Admin1234")
    for hdr in (m,):
        for method, url, body in (("get", "/api/fiscal/config", None), ("post", "/api/fiscal/check", None),
                                  ("post", "/api/fiscal/test-receipt", {"amount": 100}), ("post", "/api/fiscal/selftest", None),
                                  ("get", "/api/fiscal/operations", None)):
            assert getattr(c, method)(url, headers=hdr, **({"json": body} if body else {})).status_code == 403, url
    assert c.put("/api/fiscal/config", headers=a, json={"mode": "test"}).status_code == 403
    assert c.put("/api/fiscal/config", headers=d, json={"mode": "production"}).status_code == 403
    assert c.put("/api/settings", headers=d, json={"fiscal_mode": "test"}).status_code == 422
    r = c.put("/api/fiscal/config", headers=d, json={"mode": "mock", "enabled": True})
    assert r.status_code == 200 and r.json()["nonfiscal"]
    assert c.get("/api/fiscal/operations", headers=a).status_code == 200


def test_shift_desync_provider_closed_reopens(db, seed):
    """Зміна відкрита в MDS, але ПРРО її закрив (автозакриття) — чек не застрягає."""
    fp = FakePRROProvider()
    set_provider(fp)
    try:
        with db.session() as s:
            fiscal.update_config(s, seed["actors"]["director"], {"enabled": True})
        fiscal.open_shift(db, seed["actors"]["director"], provider=fp)
        fp.state.shift = None  # ПРРО закрив зміну без MDS
        oid = _close(db, seed)
        fiscal.process_pending(db)
        fiscal.process_pending(db)
        assert _receipt(db, oid)["status"] == "done"
        with db.session() as s:
            assert fiscal.current_shift(s) is not None and fiscal.current_shift(s).status == "opened"
    finally:
        set_provider(None)
