"""Checkbox (ПРРО): клієнт за OpenAPI 2.112.0, черга чеків, друк ESC/POS, API. HTTP — лише через respx."""
import base64
import json
from datetime import datetime

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from mds import clock
from mds.api.app import create_app
from mds.config import Config
from mds.errors import Conflict, DomainError, Forbidden, ValidationError
from mds.models import FiscalReceipt, Order, User
from mds.security import create_token
from mds.services import bookings, checkbox, orders, products
from mds.services.common import update_settings as update_common

API = "https://api.checkbox.ua"


def _cfg(tmp_path, db, keys=True):
    return Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="test-secret-key-for-unit-tests-only-0123456789",
                  checkbox_api_url=API, checkbox_login="cashier" if keys else "", checkbox_password="S3KR1T" if keys else "",
                  checkbox_license_key="LIC-KEY" if keys else "")


class Fake:
    """Імітація Checkbox на respx: зберігає чеки за id, вміє імітувати збої."""

    def __init__(self, mock):
        self.receipts: dict[str, dict] = {}
        self.shift = None
        self.fail_sell: str | None = None
        self.sell_calls = 0
        mock.post(f"{API}/api/v1/cashier/signin").mock(side_effect=self.signin)
        mock.get(f"{API}/api/v1/cashier/shift").mock(side_effect=lambda r: httpx.Response(200, json=self.shift))
        mock.post(f"{API}/api/v1/shifts").mock(side_effect=self.open_shift)
        mock.post(f"{API}/api/v1/shifts/close").mock(side_effect=self.close_shift)
        mock.post(f"{API}/api/v1/receipts/sell").mock(side_effect=self.sell)
        mock.get(url__regex=rf"{API}/api/v1/receipts/[^/]+/text").mock(side_effect=self.text)
        mock.get(url__regex=rf"{API}/api/v1/receipts/[^/]+$").mock(side_effect=self.get)

    def signin(self, req):
        body = json.loads(req.content)
        if body != {"login": "cashier", "password": "S3KR1T"}:
            return httpx.Response(403, json={"message": "Невірний логін або пароль"})
        assert req.headers["X-Client-Name"] == "MDS Connect"
        return httpx.Response(200, json={"access_token": "TOKEN", "token_type": "bearer"})

    def open_shift(self, req):
        assert req.headers["X-License-Key"] == "LIC-KEY" and req.headers["Authorization"] == "Bearer TOKEN"
        self.shift = {"id": json.loads(req.content)["id"], "status": "OPENING", "auto_close_at": json.loads(req.content).get("auto_close_at")}
        return httpx.Response(202, json=self.shift)

    def close_shift(self, req):
        self.shift = None
        return httpx.Response(202, json={"status": "CLOSING"})

    def sell(self, req):
        self.sell_calls += 1
        body = json.loads(req.content)
        if self.fail_sell == "reject":
            return httpx.Response(422, json={"detail": [{"msg": "Невірна ціна"}]})
        if self.fail_sell == "down":
            return httpx.Response(503, json={"message": "Service Unavailable"})
        if self.fail_sell == "timeout":
            raise httpx.ReadTimeout("timeout")
        if self.fail_sell == "lost":
            self.fail_sell = None
            self.receipts[body["id"]] = {"id": body["id"], "status": "DONE", "fiscal_code": "FC-LOST", "body": body}
            raise httpx.ReadTimeout("timeout after create")
        if self.shift is None:
            return httpx.Response(400, json={"message": "Зміну не відкрито"})
        if body["id"] in self.receipts:
            return httpx.Response(400, json={"message": "Чек з таким ID вже існує"})
        r = {"id": body["id"], "status": "DONE", "fiscal_code": f"FC-{len(self.receipts) + 1}", "fiscal_date": "2026-11-05T11:00:00",
             "tax_url": "https://cabinet.tax.gov.ua/cashregs/check", "body": body}
        self.receipts[body["id"]] = r
        return httpx.Response(201, json=r)

    def get(self, req):
        rid = req.url.path.rsplit("/", 1)[-1]
        r = self.receipts.get(rid)
        return httpx.Response(200, json=r) if r else httpx.Response(404, json={"message": "Not found"})

    def text(self, req):
        rid = req.url.path.split("/")[-2]
        assert req.url.params["width"] == "32"
        return httpx.Response(200, text=f"ФОП Тест\nЧек {self.receipts[rid]['fiscal_code']}\nСУМА 500.00\n")


@pytest.fixture
def fake():
    with respx.mock(assert_all_called=False) as mock:
        yield Fake(mock)
    checkbox.set_client_factory(None)


@pytest.fixture
def order(db, seed, fake):
    clock.set_now(datetime(2026, 11, 5, 9))
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        update_common(s, d, {})
        checkbox.update_settings(s, d, _cfg_obj(), {"enabled": True})
        a = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"], starts_at=datetime(2026, 11, 5, 10),
                                        client_name="Оля", client_phone="0671112233")
    clock.set_now(datetime(2026, 11, 5, 11))
    with db.session() as s:
        o = orders.close_order(s, m1, a.id, amount=50000, payment_method="card")
        oid = o.id
    yield oid
    clock.set_now(None)


_CFG = {}


def _cfg_obj():
    return _CFG["cfg"]


@pytest.fixture(autouse=True)
def _cfg_fixture(tmp_path, db):
    _CFG["cfg"] = _cfg(tmp_path, db)
    yield


def _fr(db, oid, kind="sell"):
    with db.session() as s:
        fr = s.scalar(select(FiscalReceipt).where(FiscalReceipt.order_id == oid, FiscalReceipt.kind == kind))
        return checkbox.public(fr) if fr else None


# Чистий контракт

def test_payload_payment_types_and_kopecks():
    items = [{"code": "S1", "name": "Стрижка", "price": 45000, "qty": 1}, {"code": "P2", "name": "Шампунь", "price": 2500, "qty": 2}]
    p = checkbox.receipt_payload("u1", 7, 50000, "cash", items)
    assert p["payments"] == [{"type": "CASH", "value": 50000}]
    assert p["goods"][1] == {"good": {"code": "P2", "name": "Шампунь", "price": 2500}, "quantity": 2000}
    assert p["id"] == "u1" and p["order_id"] == "7"
    c = checkbox.receipt_payload("u2", 7, 50000, "card", items, card={"rrn": "123", "auth_code": "777", "card_mask": None})
    assert c["payments"] == [{"type": "CASHLESS", "value": 50000, "label": "Картка", "rrn": "123", "auth_code": "777"}]
    assert checkbox.receipt_payload("u3", 7, 50000, "transfer", items)["payments"][0]["type"] == "CASHLESS"
    r = checkbox.receipt_payload("u4", 7, 50000, "cash", items, is_return=True)
    assert all(g["is_return"] for g in r["goods"])
    with pytest.raises(ValidationError):
        checkbox.receipt_payload("u5", 7, 49999, "cash", items)
    with pytest.raises(ValidationError):
        checkbox.receipt_payload("u6", 7, 50000, "bitcoin", items)


def test_escpos_cyrillic_and_cut():
    raw = checkbox.escpos("Чек №1\nСума", codepage="cp866")
    assert raw.startswith(b"\x1b@\x1bt\x11") and raw.endswith(b"\x1dVB\x00")
    assert "Чек".encode("cp866") in raw
    assert b"\x1bt\x2e" in checkbox.escpos("Ї", codepage="cp1251")
    assert base64.b64decode(checkbox.escpos_b64("A")) == checkbox.escpos("A")
    with pytest.raises(ValidationError):
        checkbox.escpos("x", codepage="utf-8")


def test_client_secrets_not_in_repr(fake):
    c = checkbox.make_client(_cfg_obj())
    assert "S3KR1T" not in repr(c) and "LIC-KEY" not in repr(c)
    with pytest.raises(checkbox.CheckboxError):
        checkbox.CheckboxClient(API, "", "x", "y")


# Фіскалізація

def test_fiscalize_success_card_with_auto_shift_and_print(db, seed, order, fake):
    code, out = checkbox.fiscalize(db, _cfg_obj(), seed["actors"]["m1"], order)
    assert code == 200 and out["status"] == "done" and out["fiscal_code"] == "FC-1" and out["payment_type"] == "CASHLESS"
    assert fake.shift["status"] == "OPENING" and fake.shift["auto_close_at"].startswith("2026-11-05T23:50")
    [r] = fake.receipts.values()
    assert r["body"]["payments"] == [{"type": "CASHLESS", "value": 50000, "label": "Картка"}]
    assert r["body"]["goods"][0]["good"]["price"] == 50000 and r["body"]["goods"][0]["quantity"] == 1000
    printed = base64.b64decode(out["print_base64"])
    assert "Чек FC-1".encode("cp866") in printed and printed.endswith(b"\x1dVB\x00")
    with db.session() as s:
        assert s.get(Order, order).fiscal_receipt_id == r["id"]


def test_fiscalize_idempotent(db, seed, order, fake):
    m1 = seed["actors"]["m1"]
    a = checkbox.fiscalize(db, _cfg_obj(), m1, order)[1]
    b = checkbox.fiscalize(db, _cfg_obj(), m1, order)[1]
    assert a["receipt_id"] == b["receipt_id"] and fake.sell_calls == 1 and len(fake.receipts) == 1
    assert b["print_base64"]


def test_cash_order_with_products(db, seed, fake):
    clock.set_now(datetime(2026, 11, 5, 9))
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        checkbox.update_settings(s, d, _cfg_obj(), {"enabled": True})
        pid = products.create_product(s, d, {"name": "Шампунь", "cost_price_cents": 20000, "sale_price_cents": 45000, "stock_qty": 5}).id
        a = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"], starts_at=datetime(2026, 11, 5, 10),
                                        client_name="Іра", client_phone="0671112299")
    with db.session() as s:
        oid = orders.close_order(s, m1, a.id, amount=30000, payment_method="cash", products=[{"product_id": pid, "qty": 2}]).id
    out = checkbox.fiscalize(db, _cfg_obj(), m1, oid)[1]
    body = next(iter(fake.receipts.values()))["body"]
    assert out["amount"] == 120000 and body["payments"] == [{"type": "CASH", "value": 120000}]
    assert [(g["good"]["code"], g["good"]["price"], g["quantity"]) for g in body["goods"]] == [(f"S{seed['haircut']}", 30000, 1000), (f"P{pid}", 45000, 2000)]
    clock.set_now(None)


def test_unavailable_returns_202_and_worker_retries(db, seed, order, fake):
    fake.fail_sell = "down"
    code, out = checkbox.fiscalize(db, _cfg_obj(), seed["actors"]["m1"], order)
    assert code == 202 and out["status"] == "pending" and out["message"] == "Чек буде створено пізніше"
    with db.session() as s:
        assert s.get(Order, order).status == "completed"
    fake.fail_sell = None
    assert checkbox.retry_pending(db, _cfg_obj()) == 1
    assert _fr(db, order)["status"] == "done" and len(fake.receipts) == 1


def test_timeout_after_create_does_not_duplicate(db, seed, order, fake):
    fake.fail_sell = "lost"
    code, out = checkbox.fiscalize(db, _cfg_obj(), seed["actors"]["m1"], order)
    assert code == 200 and out["status"] == "done" and out["fiscal_code"] == "FC-LOST" and len(fake.receipts) == 1


def test_timeout_retries_same_uuid(db, seed, order, fake):
    fake.fail_sell = "timeout"
    assert checkbox.fiscalize(db, _cfg_obj(), seed["actors"]["m1"], order)[0] == 202
    uuid1 = _fr(db, order)["receipt_id"]
    fake.fail_sell = None
    checkbox.retry_pending(db, _cfg_obj())
    assert list(fake.receipts) == [uuid1]


def test_rejected_marks_error_without_retry(db, seed, order, fake):
    fake.fail_sell = "reject"
    code, out = checkbox.fiscalize(db, _cfg_obj(), seed["actors"]["m1"], order)
    assert code == 200 and out["status"] == "error" and "Невірна ціна" in out["last_error"] and out["attempts"] == 1
    assert out["message"].startswith("Чек не створено")
    assert checkbox.retry_pending(db, _cfg_obj()) == 0 and fake.receipts == {} and fake.sell_calls == 1


def test_attempts_limit(db, seed, order, fake):
    fake.fail_sell = "down"
    checkbox.fiscalize(db, _cfg_obj(), seed["actors"]["m1"], order)
    for _ in range(checkbox.MAX_ATTEMPTS):
        checkbox.retry_pending(db, _cfg_obj())
    fr = _fr(db, order)
    assert fr["status"] == "error" and fr["attempts"] == checkbox.MAX_ATTEMPTS


def test_disabled_and_rights(db, seed, order, fake):
    with pytest.raises(Forbidden):
        checkbox.fiscalize(db, _cfg_obj(), seed["actors"]["m2"], order)
    with db.session() as s:
        checkbox.update_settings(s, seed["actors"]["director"], _cfg_obj(), {"enabled": False})
    with pytest.raises(DomainError) as e:
        checkbox.fiscalize(db, _cfg_obj(), seed["actors"]["m1"], order)
    assert e.value.status == 400 and e.value.code == "checkbox_disabled"


def test_unpaid_order_not_fiscalized(db, seed, fake):
    clock.set_now(datetime(2026, 11, 5, 9))
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        checkbox.update_settings(s, d, _cfg_obj(), {"enabled": True})
        a = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"], starts_at=datetime(2026, 11, 5, 10),
                                        client_name="Іра", client_phone="0671112299")
    with db.session() as s:
        oid = orders.close_order(s, m1, a.id, amount=30000, payment_method="cash", status="unpaid").id
    with pytest.raises(Conflict):
        checkbox.fiscalize(db, _cfg_obj(), m1, oid)
    clock.set_now(None)


def test_refund_creates_return_receipt(db, seed, order, fake, tmp_path):
    checkbox.fiscalize(db, _cfg_obj(), seed["actors"]["m1"], order)
    sell = _fr(db, order)
    c = TestClient(create_app(_cfg_obj(), db))
    with c, db.session() as s:
        u = s.scalar(select(User).where(User.username == "director"))
        h = {"Authorization": "Bearer " + create_token(_cfg_obj().secret_key, u.id, u.token_version, 12)}
    with c:
        r = c.post(f"/api/orders/{order}/refund", json={"reason": "Клієнтка повернула"}, headers=h)
        assert r.status_code == 200 and r.json()["status"] == "refunded"
    ret = _fr(db, order, "return")
    assert ret["status"] == "pending" and ret["amount"] == 50000
    checkbox.retry_pending(db, _cfg_obj())
    body = fake.receipts[ret["receipt_id"]]["body"]
    assert body["related_receipt_id"] == sell["receipt_id"] and all(g["is_return"] for g in body["goods"])
    assert body["payments"][0]["type"] == "CASHLESS" and _fr(db, order, "return")["status"] == "done"


def test_refund_without_receipt_no_return(db, seed, order, fake):
    with db.session() as s:
        orders.refund_order(s, seed["actors"]["director"], order, "тест")
        assert checkbox.on_refund(s, seed["actors"]["director"], order) is None


# Налаштування і зміна

def test_settings_director_only_and_validation(db, seed, tmp_path, monkeypatch):
    monkeypatch.setenv("MDS_ENV", "test")
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        with pytest.raises(Forbidden):
            checkbox.get_settings(s, m1, _cfg_obj())
        for bad in ({"printer_type": "usb"}, {"printer_type": "wifi"}, {"printer_ip": "8.8.8.8"}, {"printer_port": 0},
                    {"printer_width": 40}, {"printer_codepage": "utf8"}, {"enabled": "yes"}):
            with pytest.raises(ValidationError):
                checkbox.update_settings(s, d, _cfg_obj(), bad)
        out = checkbox.update_settings(s, d, _cfg_obj(), {"enabled": True, "printer_type": "wifi", "printer_ip": "192.168.1.60",
                                                          "printer_width": 48, "printer_codepage": "cp1251"})
        assert out["enabled"] and out["printer_ip"] == "192.168.1.60" and out["keys_configured"] is True
        assert out["queue"] == {"pending": 0, "error": 0}
        with pytest.raises(ValidationError):
            checkbox.update_settings(s, d, _cfg(tmp_path, db, keys=False), {"enabled": True})
        with pytest.raises(ValidationError):
            update_common(s, d, {"checkbox_enabled": False})
        assert "S3KR1T" not in json.dumps(out) and "LIC-KEY" not in json.dumps(out)


def test_check_connection_and_shift(db, seed, fake):
    cfg = _cfg_obj()
    assert checkbox.check_connection(cfg) == {"ok": True, "shift": "CLOSED"}
    d = seed["actors"]["director"]
    with pytest.raises(Forbidden):
        checkbox.shift_action(cfg, seed["actors"]["m1"], "open")
    assert checkbox.shift_action(cfg, d, "open")["message"] == "Зміну ПРРО відкрито"
    assert checkbox.shift_action(cfg, d, "open")["message"] == "Зміна ПРРО вже відкрита"
    assert checkbox.shift_action(cfg, d, "close")["message"] == "Зміну ПРРО закрито (Z-звіт)"
    bad = Config(**{**cfg.__dict__, "checkbox_password": "wrong"})
    r = checkbox.check_connection(bad)
    assert r["ok"] is False and "Невірний логін або пароль" in r["error"]


def test_test_print(db, seed):
    with db.session() as s:
        out = checkbox.test_print(s, seed["actors"]["director"])
    raw = base64.b64decode(out["print_base64"])
    assert "Тестовий друк".encode("cp866") in raw and out["printer"]["printer_type"] == "none"


def test_api(db, seed, order, fake):
    c = TestClient(create_app(_cfg_obj(), db))

    def hdr(name):
        with db.session() as s:
            u = s.scalar(select(User).where(User.username == name))
            return {"Authorization": "Bearer " + create_token(_cfg_obj().secret_key, u.id, u.token_version, 12)}

    with c:
        hd, hm = hdr("director"), hdr("olena")
        assert c.get("/api/settings/checkbox", headers=hm).status_code == 403
        assert c.get("/api/settings/checkbox", headers=hd).json()["keys_configured"] is True
        assert c.get("/api/checkbox/client-config", headers=hm).json() == {"enabled": True, "printer_type": "none", "printer_ip": "",
                                                                            "printer_port": 9100, "printer_bt_name": ""}
        assert c.post("/api/checkbox/check", headers=hm).status_code == 403
        assert c.post("/api/checkbox/check", headers=hd).json()["ok"] is True
        r = c.post(f"/api/orders/{order}/fiscalize", headers=hm)
        assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["print_base64"]
        assert c.post(f"/api/orders/{order}/fiscalize").status_code == 401
        assert "S3KR1T" not in c.get("/api/settings", headers=hm).text
