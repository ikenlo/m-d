"""Фіскалізація через FakePRROProvider (без справжніх чеків) + контракт CheckboxProvider (імітація HTTP).

Ставки й номери чеків — тестові (TEST-…), не законодавчі.
"""

import json
import threading
import urllib.error
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from mds import clock
from mds.errors import Conflict, Forbidden, ValidationError
from mds.models import FiscalReceipt, FiscalReport, LedgerEntry, Notification, Order, User
from mds.services import bookings, fiscal, orders
from mds.services.common import update_settings
from mds.services.fiscal_providers import (
    CheckboxProvider,
    FakePRROProvider,
    ProviderLost,
    ProviderUnavailable,
    Resp,
    set_provider,
)

T0 = datetime(2026, 11, 2, 10, 0)


@pytest.fixture
def fp(db, seed):
    p = FakePRROProvider()
    set_provider(p)
    with db.session() as s:
        update_settings(s, seed["actors"]["director"], {"fiscal_enabled": True})
        s.get(User, seed["director"]).telegram_chat_id = 9001
    yield p
    set_provider(None)


def close(db, seed, *, master="m1", amount=60000, pay="cash", hour=10, phone=None, key=None):
    clock.set_now(T0.replace(hour=hour) - timedelta(hours=1))
    with db.session() as s:
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed[master], service_id=seed["haircut"],
                                        starts_at=T0.replace(hour=hour), client_name="Клієнт",
                                        client_phone=phone or f"067{hour:02d}{seed[master]:05d}")
        aid = a.id
    clock.set_now(T0.replace(hour=hour, minute=50))
    with db.session() as s:
        return orders.close_order(s, seed["actors"][master], aid, amount=amount, payment_method=pay, idempotency_key=key).id


def receipt(db, order_id, kind="sell"):
    with db.session() as s:
        r = s.scalar(select(FiscalReceipt).where(FiscalReceipt.order_id == order_id, FiscalReceipt.kind == kind)
                     .order_by(FiscalReceipt.id.desc()))
        return fiscal.receipt_public(r) if r else None


def open_shift(db, seed, fp):
    fiscal.open_shift(db, seed["actors"]["director"], provider=fp)


# ------------------------------------------------------------------ базові чеки
def test_cash_and_card_receipts_with_fiscal_number_and_qr(db, seed, fp):
    open_shift(db, seed, fp)
    o1 = close(db, seed, amount=60000, pay="cash", hour=10)
    o2 = close(db, seed, amount=45000, pay="card", hour=12)
    assert receipt(db, o1)["status"] == "queued"  # до підтвердження провайдера — НЕ фіскалізовано
    res = fiscal.process_pending(db, fp)
    assert res["by_status"] == {"done": 2}
    r1, r2 = receipt(db, o1), receipt(db, o2)
    assert r1["status"] == "done" and r1["fiscal_code"].startswith("TEST-") and r1["tax_url"].startswith("https://")
    assert (r1["payment_method"], r2["payment_method"]) == ("cash", "card")
    assert fp.state.shift["cash"] == 60000 and fp.state.shift["card"] == 45000
    sells = [c for c in fp.calls if c[0] == "sell"]
    assert len(sells) == 2


def test_disabled_by_default_changes_nothing(db, seed):
    o = close(db, seed)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(FiscalReceipt)) == 0
        assert s.get(Order, o).amount == 60000


def test_auto_open_shift_or_reject(db, seed, fp):
    o = close(db, seed)
    fiscal.process_pending(db, fp)  # немає зміни → відкрито автоматично, повтор
    assert fp.state.shift and fp.state.shift["status"] == "OPENED"
    fiscal.process_pending(db, fp)
    assert receipt(db, o)["status"] == "done"
    # без автовідкриття — відмова і сповіщення директору
    fiscal.close_shift(db, seed["actors"]["director"], provider=fp)
    with db.session() as s:
        update_settings(s, seed["actors"]["director"], {"fiscal_auto_open_shift": False})
    o2 = close(db, seed, hour=13)
    fiscal.process_pending(db, fp)
    assert receipt(db, o2)["status"] == "rejected"
    with db.session() as s:
        assert any("ПРРО відхилив" in n.text for n in s.scalars(select(Notification).where(Notification.chat_id == 9001)))


# ------------------------------------------------------------------ збої провайдера
def test_provider_rejects_then_retry(db, seed, fp):
    open_shift(db, seed, fp)
    o = close(db, seed)
    fp.fail_next = ["reject"]
    fiscal.process_pending(db, fp)
    r = receipt(db, o)
    assert r["status"] == "rejected" and "Невірні дані" in r["last_error"]
    fiscal.process_pending(db, fp)  # відхилені самі не повторюються
    assert receipt(db, o)["status"] == "rejected"
    with pytest.raises(Forbidden):
        fiscal.retry(db, seed["actors"]["m2"], r["id"], fp)  # чужий чек
    assert fiscal.retry(db, seed["actors"]["m1"], r["id"], fp) == "done"  # майстер — свій


def test_prro_unavailable_order_kept_and_backoff(db, seed, fp):
    open_shift(db, seed, fp)
    fp.down = True
    o = close(db, seed)
    for _i in range(5):
        fiscal.process_pending(db, fp)
        r = receipt(db, o)
        assert r["status"] == "error"
        clock.set_now(datetime.fromisoformat(r["next_attempt_at"]) + timedelta(seconds=1))
    with db.session() as s:
        assert s.get(Order, o).amount == 60000  # замовлення не загубилось
        assert any("ПРРО недоступний" in n.text for n in s.scalars(select(Notification).where(Notification.chat_id == 9001)))
    assert receipt(db, o)["attempts"] == 5
    fp.down = False
    fiscal.process_pending(db, fp)
    assert receipt(db, o)["status"] == "done"
    assert len(fp.state.receipts) == 1


def test_lost_response_does_not_duplicate(db, seed, fp):
    open_shift(db, seed, fp)
    o = close(db, seed)
    fp.fail_next = ["lost"]  # чек створено в ПРРО, але відповідь втрачено
    fiscal.process_pending(db, fp)
    r = receipt(db, o)
    assert r["status"] == "error" and len(fp.state.receipts) == 1
    clock.set_now(datetime.fromisoformat(r["next_attempt_at"]))
    fiscal.process_pending(db, fp)
    r = receipt(db, o)
    assert r["status"] == "done" and len(fp.state.receipts) == 1
    assert ("get", r["uuid"]) in fp.calls and sum(1 for c in fp.calls if c[0] == "sell") == 1


def test_pending_at_dps_then_done(db, seed, fp):
    open_shift(db, seed, fp)
    o = close(db, seed)
    fp.fail_next = ["pending"]
    fiscal.process_pending(db, fp)
    assert receipt(db, o)["status"] == "pending" and receipt(db, o)["fiscal_code"] is None
    fp.complete_pending()
    clock.set_now(clock.now() + timedelta(minutes=2))
    fiscal.process_pending(db, fp)
    assert receipt(db, o)["status"] == "done" and receipt(db, o)["fiscal_code"]


def test_restart_recovery_during_sending(db, seed, fp):
    """Сервер «впав» після надсилання в ПРРО, але до запису результату."""
    open_shift(db, seed, fp)
    o = close(db, seed)
    rid = receipt(db, o)["id"]
    with db.session() as s:  # імітуємо захоплення і відправку без запису результату
        r = s.get(FiscalReceipt, rid)
        r.status, r.attempts, r.sending_since = "sending", 1, clock.now()
        u, goods = r.uuid, r.goods
    fp.sell(u, goods, "cash", 60000)  # чек дійшов до ПРРО
    fiscal.process_pending(db, fp)  # ще не застаріло — не чіпаємо
    assert receipt(db, o)["status"] == "sending"
    clock.set_now(clock.now() + timedelta(seconds=fiscal.STALE_SECONDS + 1))
    fiscal.process_pending(db, fp)  # «новий процес» після перезапуску
    assert receipt(db, o)["status"] == "done" and len(fp.state.receipts) == 1


# ------------------------------------------------------------------ дублікати і паралельність
def test_double_click_close_order_one_receipt(db, seed, fp):
    open_shift(db, seed, fp)
    o1 = close(db, seed, key="tap-1")
    clock.set_now(T0.replace(minute=55))
    with db.session() as s:
        a_id = s.get(Order, o1).appointment_id
        assert orders.close_order(s, seed["actors"]["m1"], a_id, amount=60000, payment_method="cash", idempotency_key="tap-1").id == o1
    fiscal.process_pending(db, fp)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(FiscalReceipt)) == 1
    assert len(fp.state.receipts) == 1


def test_parallel_processing_sends_once(db, seed, fp):
    open_shift(db, seed, fp)
    fp.delay = 0.05
    o = close(db, seed)
    rid = receipt(db, o)["id"]
    barrier = threading.Barrier(4)
    out = []

    def w():
        barrier.wait()
        out.append(fiscal.process_receipt(db, rid, fp))

    ts = [threading.Thread(target=w) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out.count("done") == 1 and out.count(None) == 3
    assert sum(1 for c in fp.calls if c[0] == "sell") == 1


# ------------------------------------------------------------------ повернення
def test_refund_partial_and_full_with_internal_correction(db, seed, fp):
    d = seed["actors"]["director"]
    open_shift(db, seed, fp)
    o = close(db, seed, amount=60000, pay="card")
    with pytest.raises(Conflict):
        fiscal.refund(db, d, o, 10000, "до фіскалізації", provider=fp)
    fiscal.process_pending(db, fp)
    with pytest.raises(Forbidden):
        fiscal.refund(db, seed["actors"]["m1"], o, 10000, "x", provider=fp)
    with pytest.raises(ValidationError):
        fiscal.refund(db, d, o, 10000, "", provider=fp)
    res = fiscal.refund(db, d, o, 20000, "Клієнт незадоволений", provider=fp)
    assert res["status"] == "done"
    with db.session() as s:
        od = s.get(Order, o)
        assert od.amount == 40000 and od.master_earning == 20000  # управлінський поділ перераховано (50%)
        kinds = [e.kind for e in s.scalars(select(LedgerEntry).where(LedgerEntry.ref_type == "order"))]
        assert kinds == ["order_closed", "order_reversal", "order_corrected"]
    with pytest.raises(ValidationError):
        fiscal.refund(db, d, o, 40001, "забагато", provider=fp)
    fiscal.refund(db, d, o, 40000, "Повне повернення", provider=fp)
    with db.session() as s:
        assert s.get(Order, o).amount == 0
        rec = fiscal.reconcile(s, d, T0.date(), T0.date())
    assert rec["ok"] and rec["totals"]["fiscal_sales"] == 60000 and rec["totals"]["fiscal_returns"] == 60000
    rets = [r for r in fp.state.receipts.values() if r["_ret"]]
    assert len(rets) == 2 and all(r["_pm"] == "card" for r in rets)


# ------------------------------------------------------------------ зміни і звіти
def test_fiscal_shift_x_z_reports(db, seed, fp):
    d, adm = seed["actors"]["director"], seed["actors"]["admin"]
    with pytest.raises(Forbidden):
        fiscal.open_shift(db, seed["actors"]["m1"], provider=fp)
    fiscal.open_shift(db, adm, provider=fp)
    with pytest.raises(Conflict):
        fiscal.open_shift(db, d, provider=fp)
    close(db, seed, amount=60000, pay="cash", hour=10)
    o2 = close(db, seed, amount=40000, pay="card", hour=12)
    fiscal.process_pending(db, fp)
    x = fiscal.x_report(db, adm, provider=fp)
    assert x["sales"] == 100000 and x["cash"] == 60000 and not x["is_z_report"]
    fp.down = True
    close(db, seed, amount=1000, hour=14)
    fiscal.process_pending(db, fp)
    with pytest.raises(Conflict):
        fiscal.close_shift(db, adm, provider=fp)  # є нефіскалізований чек
    with pytest.raises(Forbidden):
        fiscal.close_shift(db, adm, force=True, provider=fp)
    fp.down = False
    clock.set_now(clock.now() + timedelta(minutes=5))
    fiscal.process_pending(db, fp)
    z = fiscal.close_shift(db, adm, provider=fp)
    assert z["status"] == "closed" and z["z_report"]["sales"] == 101000 and z["z_report"]["sell_count"] == 3
    with db.session() as s:
        assert [r.kind for r in s.scalars(select(FiscalReport).order_by(FiscalReport.id))] == ["X", "Z"]
        assert fiscal.current_shift(s) is None
    assert o2


# ------------------------------------------------------------------ звірка
def test_reconcile_finds_missing_not_done_and_mismatch(db, seed, fp):
    d = seed["actors"]["director"]
    with db.session() as s:
        update_settings(s, d, {"fiscal_enabled": False})
    o_missing = close(db, seed, hour=9)  # закрито, коли ПРРО було вимкнено
    with db.session() as s:
        update_settings(s, d, {"fiscal_enabled": True})
    open_shift(db, seed, fp)
    o_ok = close(db, seed, hour=10)
    o_mis = close(db, seed, hour=11)
    fiscal.process_pending(db, fp)
    fp.down = True
    o_wait = close(db, seed, hour=12)
    fiscal.process_pending(db, fp)
    with db.session() as s:
        orders.correct_order(s, d, o_mis, {"amount": 50000}, "знижка без повернення")
        rec = fiscal.reconcile(s, d, T0.date(), T0.date())
    types = {i["order_id"]: i["type"] for i in rec["issues"]}
    assert types == {o_missing: "missing", o_wait: "not_done", o_mis: "mismatch"} and not rec["ok"]
    assert o_ok not in types
    fp.down = False
    fp.state.receipts.pop(receipt(db, o_ok)["uuid"])  # чек зник у провайдера
    with db.session() as s:
        deep = fiscal.reconcile(s, d, T0.date(), T0.date(), provider=fp)
    assert any(i["receipt_id"] == receipt(db, o_ok)["id"] for i in deep["provider_issues"])


# ------------------------------------------------------------------ контракт Checkbox (без мережі)
class FakeHttp:
    def __init__(self):
        self.calls, self.responses = [], []

    def __call__(self, method, url, headers, data, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": json.loads(data) if data else None})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_checkbox_contract_and_errors():
    http = FakeHttp()
    cb = CheckboxProvider(base_url="https://api.test", license_key="LIC-SECRET", pin_code="1234", http=http)
    assert "LIC-SECRET" not in repr(cb) and "1234" not in repr(cb)
    http.responses = [Resp(200, b'{"access_token": "JWT"}'),
                      Resp(201, json.dumps({"id": "u1", "status": "DONE", "fiscal_code": "FC1", "tax_url": "https://cabinet.tax.gov.ua/x"}).encode())]
    res = cb.sell("u1", [{"code": "S1", "name": "Стрижка", "price": 60000, "quantity": 1000}], "card", 60000,
                  is_return=True, related_provider_id="orig")
    assert (res.status, res.fiscal_code, res.tax_url) == ("done", "FC1", "https://cabinet.tax.gov.ua/x")
    login, sell = http.calls
    assert login["url"].endswith("/api/v1/cashier/signinPinCode") and login["headers"]["X-License-Key"] == "LIC-SECRET"
    assert login["body"] == {"pin_code": "1234"}
    assert sell["url"].endswith("/api/v1/receipts/sell") and sell["headers"]["Authorization"] == "Bearer JWT"
    assert sell["body"]["id"] == "u1" and sell["body"]["related_receipt_id"] == "orig"
    assert sell["body"]["goods"][0] == {"good": {"code": "S1", "name": "Стрижка", "price": 60000}, "quantity": 1000, "is_return": True}
    assert sell["body"]["payments"] == [{"type": "CASHLESS", "value": 60000}]
    # 5xx на чек — відповідь могла бути втрачена; мережа — недоступно; таймаут — втрачено
    http.responses = [Resp(502, b"")]
    with pytest.raises(ProviderLost):
        cb.sell("u2", [{"code": "S1", "name": "x", "price": 1}], "cash", 1)
    http.responses = [urllib.error.URLError("dns")]
    with pytest.raises(ProviderUnavailable):
        cb.sell("u2", [{"code": "S1", "name": "x", "price": 1}], "cash", 1)
    http.responses = [TimeoutError()]
    with pytest.raises(ProviderLost):
        cb.sell("u2", [{"code": "S1", "name": "x", "price": 1}], "cash", 1)
    http.responses = [Resp(404, b'{"message":"not found"}')]
    assert cb.get_receipt("u2") is None
    http.responses = [Resp(200, b'{"id":"u3","status":"CREATED"}')]
    assert cb.get_receipt("u3").status == "pending"
    http.responses = [Resp(401, b"")]
    with pytest.raises(ProviderUnavailable):
        cb.get_receipt("u3")
    assert cb._token is None  # токен скинуто — наступний виклик увійде знову
    http.responses = [Resp(200, b'{"access_token":"JWT2"}'), Resp(201, b'{"id":"sh","status":"OPENING"}')]
    assert cb.open_shift("sh")["status"] == "OPENING"
    assert http.calls[-1]["body"] == {"id": "sh"} and http.calls[-1]["headers"]["X-License-Key"] == "LIC-SECRET"


def test_checkbox_not_configured():
    from mds.services.fiscal_providers import ProviderRejected

    cb = CheckboxProvider(license_key="", pin_code="", http=FakeHttp())
    with pytest.raises(ProviderRejected):
        cb.get_receipt("x")
