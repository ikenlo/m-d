"""ПриватБанк «Автоклієнт» (лише читання): контракт HTTP за специфікацією v3.0.0, звірка карткових оплат, права.
Мережі немає: справжній клієнт працює через емулятор, звірка — через FakeBankProvider."""

import json
import urllib.parse
from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from mds import clock
from mds.errors import Forbidden, ValidationError
from mds.models import AuditLog, LedgerEntry
from mds.services import bank, bookings, orders
from mds.services import bank_providers as bp
from mds.services.common import update_settings

TOKEN = "SECRET-TOKEN-abc123=="
IBAN = "UA633052990000026009050032237"
DAY = date(2026, 10, 20)


# ------------------------------------------------------------------ емулятор банку
class FakeAutoclient:
    """Емулятор ендпоінтів зі специфікації: перевіряє заголовки, формат дат, пагінацію."""

    def __init__(self):
        self.requests = []
        self.fail = None  # ("status", code) | ("timeout",)
        self.txn_pages = []  # список сторінок transactions
        self.acq_pages = []
        self.settings = {"phase": "WRK", "work_balance": "N", "today": "20.10.2026 00:00:00"}
        self.charset = "utf-8"

    def __call__(self, method, url, headers, body, timeout):
        u = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        self.requests.append({"method": method, "path": u.path, "query": q, "headers": headers,
                              "body": json.loads(body) if body else None})
        if self.fail:
            kind = self.fail[0]
            if kind == "timeout":
                raise TimeoutError("timed out")
            code = self.fail[1]
            return code, {"Content-Type": "application/json"}, json.dumps(
                {"status": "ERROR", "code": str(code), "message": "помилка", "serviceCode": "X1"}).encode()
        if headers.get("token") != TOKEN:
            return 401, {}, b'{"status":"ERROR","code":"401","message":"bad token"}'
        assert headers.get("User-Agent")
        if u.path == "/api/statements/settings":
            return self._ok({"status": "SUCCESS", "type": "settings", "settings": self.settings})
        if u.path == "/api/statements/transactions":
            assert len(q["startDate"]) == 10 and q["startDate"][2] == "-"  # ДД-ММ-РРРР
            i = int(q.get("followId") or 0)
            page = self.txn_pages[i]
            last = i == len(self.txn_pages) - 1
            return self._ok({"status": "SUCCESS", "type": "transactions", "exist_next_page": not last,
                             "next_page_id": None if last else str(i + 1), "transactions": page})
        if u.path == "/api/equiring/statements":
            b = self.requests[-1]["body"]
            assert set(b) >= {"account", "dateFrom", "dateTo"}
            i = b["page"] - 1
            last = i >= len(self.acq_pages) - 1
            return self._ok({"dataCollections": self.acq_pages[i] if self.acq_pages else [],
                             "existNextPage": not last, "nextPageId": None})
        return 404, {}, b"{}"

    def _ok(self, payload):
        return 200, {"Content-Type": f"application/json;charset={self.charset}"}, \
            json.dumps(payload, ensure_ascii=False).encode(self.charset)


def _client(fake, **kw):
    t = [0.0]
    sleeps = []

    def mono():
        return t[0]

    def sleep(x):
        sleeps.append(x)
        t[0] += x

    c = bp.PrivatAutoclientProvider(TOKEN, http=fake, sleep=sleep, monotonic=mono, **kw)
    c._sleeps = sleeps
    return c


def _txn(ref, amount, trantype="C", refn="1", status="r", day="20.10.2026"):
    return {"AUT_MY_ACC": IBAN, "AUT_CNTR_NAM": "ПриватБанк еквайринг", "CCY": "UAH", "FL_REAL": "r",
            "PR_PR": status, "DAT_OD": day, "DATE_TIME_DAT_OD_TIM_P": f"{day} 10:00:00", "OSND": "Зарахування",
            "SUM": amount, "SUM_E": amount, "REF": ref, "REFN": refn, "ID": ref, "TRANTYPE": trantype}


# Приклад відповіді еквайрингу зі специфікації (рр. 2665–2710), Ref_Retailer узято рядком
SPEC_ACQ = [
    {"Page_Num": 1, "Ref_Retailer": "651*760", "Ident_Ter": "M13*0ZR3", "Term_St": "A",
     "Date": "2025-10-20T00:00:00", "TranPost": "2025-10-20", "Tran_Invoice": "04*146", "Pan": "545708****224923",
     "Tran_Approval": "03*307", "Extr_Rrn": "010362*464390", "Curr": "980", "Tran_Amt": -10.0, "Acq_Amt": -0.13,
     "Ret_Amt": -9.87, "Tran_Fee": 0.0, "Summ": -10.0, "TranRetPos": "2025-10-21"},
    {"Page_Num": 1, "Ref_Retailer": "651*760", "Ident_Ter": "M13*0ZR3", "Term_St": "A",
     "Date": "2025-10-20T10:09:11", "TranPost": "2025-10-20", "Tran_Invoice": None, "Pan": "545708****224923",
     "Tran_Approval": "033*07", "Extr_Rrn": "010362*464390", "Curr": "980", "Tran_Amt": 10.0, "Acq_Amt": 0.13,
     "Ret_Amt": 9.87, "Tran_Fee": 0.0, "Summ": 10.0, "TranRetPos": "2025-10-21"},
]


# ------------------------------------------------------------------ контракт HTTP
def test_read_only_whitelist_blocks_payments_before_network():
    fake = FakeAutoclient()
    c = _client(fake)
    for method, path in [("POST", "/api/proxy/payment/create"), ("POST", "/api/proxy/payment/create_pred"),
                         ("POST", "/api/proxy/payment/add-sign"), ("POST", "/api/proxy/payment/delete"),
                         ("POST", "/api/pay/maspay/create"), ("POST", "/api/proxy/zed/swift/save_with_forecast"),
                         ("GET", "/api/proxy/payment/get"), ("POST", "/api/paysheets/print_receipt")]:
        with pytest.raises(bp.BankForbiddenOperation):
            c._request(method, path, body={})
    assert fake.requests == []  # жодного звернення до банку
    assert all("payment" not in p and "maspay" not in p for _, p in bp.ALLOWED)


def test_headers_dates_pagination_and_parsing():
    fake = FakeAutoclient()
    fake.txn_pages = [[_txn("REF1", "100.50"), _txn("REF2", "20.00", "D")],
                      [_txn("REF3", "1 234,56"), _txn("REF1", "100.50")]]  # REF1/1 — дубль на межі сторінок
    c = _client(fake)
    rows = c.transactions(IBAN, date(2026, 10, 1), date(2026, 10, 20))
    r0 = fake.requests[0]
    assert r0["headers"]["token"] == TOKEN and r0["headers"]["User-Agent"].startswith("MDS-Connect")
    assert r0["headers"]["Content-Type"] == "application/json;charset=utf8"
    assert r0["query"]["startDate"] == "01-10-2026" and r0["query"]["endDate"] == "20-10-2026"
    assert r0["query"]["acc"] == IBAN and r0["query"]["limit"] == "100" and "followId" not in r0["query"]
    assert fake.requests[1]["query"]["followId"] == "1"
    assert [(t.key, t.direction, t.amount) for t in rows] == [
        ("REF1/1", "in", 10050), ("REF2/1", "out", 2000), ("REF3/1", "in", 123456)]
    assert c._sleeps == [1.0]  # не частіше 1 запиту на секунду


def test_acquiring_spec_example_amounts_and_request_body():
    fake = FakeAutoclient()
    fake.acq_pages = [SPEC_ACQ[:1], SPEC_ACQ[1:]]
    c = _client(fake)
    rows = c.acquiring(IBAN, date(2025, 10, 20))
    bodies = [r["body"] for r in fake.requests]
    assert bodies[0] == {"account": IBAN, "dateFrom": "2025-10-20", "dateTo": "2025-10-20", "page": 1,
                         "numberRecords": 100, "statementsDateType": "TRANSACTION_DATE"}
    assert bodies[1]["page"] == 2
    refund, sale = rows
    assert (sale.amount, sale.fee, sale.net, sale.settle_date, sale.rrn) == (1000, 13, 987, "2025-10-21", "010362*464390")
    assert sale.amount == sale.fee + sale.net  # 10.00 = 0.13 + 9.87
    assert refund.is_refund and (refund.amount, refund.fee, refund.net) == (-1000, -13, -987)
    assert refund.key != sale.key  # та сама RRN, але різні операції


def test_cp1251_response_decoded():
    fake = FakeAutoclient()
    fake.charset = "cp1251"
    fake.txn_pages = [[_txn("R1", "5.00")]]
    rows = _client(fake).transactions(IBAN, DAY, DAY)
    assert rows[0].counterparty == "ПриватБанк еквайринг"


@pytest.mark.parametrize("fail,exc", [
    (("status", 401), bp.BankAuthError), (("status", 403), bp.BankAuthError), (("status", 400), bp.BankRejected),
    (("status", 500), bp.BankUnavailable), (("status", 503), bp.BankUnavailable), (("timeout",), bp.BankUnavailable),
])
def test_errors_mapped_and_token_never_leaks(fail, exc):
    fake = FakeAutoclient()
    fake.fail = fail
    c = _client(fake)
    with pytest.raises(exc) as ei:
        c.server_settings()
    assert TOKEN not in str(ei.value) and TOKEN not in repr(c)


def test_wrong_token_is_auth_error():
    fake = FakeAutoclient()
    c = bp.PrivatAutoclientProvider("wrong", http=fake, sleep=lambda x: None)
    with pytest.raises(bp.BankAuthError):
        c.server_settings()
    with pytest.raises(bp.BankAuthError):
        bp.PrivatAutoclientProvider("", http=fake)


def test_maintenance_phase_is_unavailable():
    fake = FakeAutoclient()
    fake.settings = {"phase": "CLS", "work_balance": "Y"}
    with pytest.raises(bp.BankUnavailable):
        _client(fake).ensure_working()


# ------------------------------------------------------------------ режими і налаштування
def test_live_mode_blocked_without_server_permission(db, seed, monkeypatch):
    d = seed["actors"]["director"]
    monkeypatch.delenv(bp.LIVE_ALLOWED_ENV, raising=False)
    with db.session() as s:
        with pytest.raises(Forbidden):
            update_settings(s, d, {"privat_mode": "live"})
    with pytest.raises(bp.BankForbiddenOperation):
        bp.get_provider("live")
    with pytest.raises(bp.BankRejected):
        bp.get_provider("off")
    # навіть із дозволом без токена реальний клієнт не створюється
    monkeypatch.setenv(bp.LIVE_ALLOWED_ENV, "yes")
    monkeypatch.delenv(bp.TOKEN_ENV, raising=False)
    with pytest.raises(bp.BankAuthError):
        bp.get_provider("live")


def test_account_validation_and_status_hides_token(db, seed, monkeypatch):
    d = seed["actors"]["director"]
    monkeypatch.setenv(bp.TOKEN_ENV, TOKEN)
    with db.session() as s:
        with pytest.raises(ValidationError):
            update_settings(s, d, {"privat_account": "12345"})
        update_settings(s, d, {"privat_account": "ua63 3052 9900 0002 6009 0500 3223 7", "privat_mode": "mock"})
        st = bank.status(s, d)
    assert st["account"] == IBAN and st["mode"] == "mock" and st["token_configured"] is True
    assert TOKEN not in json.dumps(st)


# ------------------------------------------------------------------ звірка
def _close(db, seed, at, amount, pay="card", master="m1", phone="0670000001"):
    clock.set_now(at.replace(hour=7, minute=0))
    with db.session() as s:
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed[master], service_id=seed["haircut"],
                                        starts_at=at.replace(minute=0), client_name="К", client_phone=phone)
        aid = a.id
    clock.set_now(at)
    with db.session() as s:
        return orders.close_order(s, seed["actors"][master], aid, amount=amount, payment_method=pay).id


@pytest.fixture
def fake_bank(db, seed):
    f = bp.FakeBankProvider()
    bp.set_provider(f)
    with db.session() as s:
        update_settings(s, seed["actors"]["director"], {"privat_mode": "mock", "privat_account": IBAN})
    yield f
    bp.set_provider(None)
    clock.set_now(None)


def test_reconcile_day_exact_sums(db, seed, fake_bank):
    o1 = _close(db, seed, datetime(2026, 10, 20, 10, 50), 50000)
    o2 = _close(db, seed, datetime(2026, 10, 20, 12, 55), 60000, master="m2", phone="0670000002")
    o3 = _close(db, seed, datetime(2026, 10, 20, 15, 40), 50000, phone="0670000003")  # у терміналі не пройшла
    _close(db, seed, datetime(2026, 10, 20, 17, 30), 45000, pay="cash", phone="0670000004")  # готівка — не звіряється
    f = fake_bank
    f.add_card(datetime(2026, 10, 20, 10, 48), 50000, 650, rrn="R1")  # → o1
    f.add_card(datetime(2026, 10, 20, 12, 51), 60000, 780, rrn="R2")  # → o2
    f.add_card(datetime(2026, 10, 20, 19, 5), 30000, 390, rrn="R3")  # оплата без замовлення
    # зарахування 21.10: (50000−650)+(60000−780)+(30000−390) = 138180
    f.add_txn(date(2026, 10, 21), 138180, ref="SETTLE1", purpose="Відшкодування еквайринг")
    f.add_txn(date(2026, 10, 21), 999, direction="out", ref="FEE1")

    with db.session() as s:
        before = (s.scalar(select(func.count(LedgerEntry.id))), s.scalar(select(func.count(AuditLog.id))))
        r = bank.reconcile_day(s, seed["actors"]["director"], DAY)
        after = (s.scalar(select(func.count(LedgerEntry.id))), s.scalar(select(func.count(AuditLog.id))))
    assert before == after  # звірка нічого не записує

    assert (r["orders_card_count"], r["orders_card_total"]) == (3, 160000)
    assert r["bank"] == {"sales": 140000, "refunds": 0, "fee": 1820, "net": 138180, "count": 3}
    assert r["difference"] == -20000
    assert sorted(m["order_id"] for m in r["matched"]) == sorted([o1, o2])
    kinds = {(p["type"], p.get("order_id") or p.get("rrn")) for p in r["problems"]}
    assert kinds == {("no_bank_operation", o3), ("no_order", "R3")}
    assert r["settlement"] == [{"date": "2026-10-21", "expected_net": 138180, "found": True,
                                "candidates": [{"key": "SETTLE1/1", "amount": 138180,
                                                "purpose": "Відшкодування еквайринг"}]}]
    assert r["ok"] is False


def test_reconcile_equal_amounts_matched_by_time_and_refund_flagged(db, seed, fake_bank):
    a = _close(db, seed, datetime(2026, 10, 20, 10, 0), 50000)
    b = _close(db, seed, datetime(2026, 10, 20, 16, 0), 50000, phone="0670000002")
    f = fake_bank
    f.add_card(datetime(2026, 10, 20, 15, 58), 50000, 650, rrn="B")
    f.add_card(datetime(2026, 10, 20, 9, 59), 50000, 650, rrn="A")
    f.add_card(datetime(2026, 10, 20, 18, 0), -50000, -650, rrn="A")  # повернення
    with db.session() as s:
        r = bank.reconcile_day(s, seed["actors"]["director"], DAY, check_settlement=False)
    pairs = {m["rrn"]: m["order_id"] for m in r["matched"]}
    assert pairs == {"A": a, "B": b}
    assert [p["type"] for p in r["problems"]] == ["refund"]
    assert r["bank"]["refunds"] == 50000 and r["difference"] == -50000


def test_reconcile_all_ok_and_settlement_missing(db, seed, fake_bank):
    _close(db, seed, datetime(2026, 10, 20, 11, 0), 70000)
    fake_bank.add_card(datetime(2026, 10, 20, 11, 2), 70000, 910, rrn="X")
    with db.session() as s:
        r = bank.reconcile_day(s, seed["actors"]["director"], DAY)
    assert r["difference"] == 0 and not r["matched"][0]["gap_min"] > 5
    assert [p["type"] for p in r["problems"]] == ["settlement_not_found"]
    fake_bank.add_txn(date(2026, 10, 21), 69090, ref="S")
    with db.session() as s:
        assert bank.reconcile_day(s, seed["actors"]["director"], DAY)["ok"] is True


def test_bank_unavailable_is_clear_error(db, seed, fake_bank):
    from mds.errors import Conflict

    fake_bank.fail = bp.BankUnavailable("HTTP 503")
    with db.session() as s, pytest.raises(Conflict):
        bank.reconcile_day(s, seed["actors"]["director"], DAY)


def test_limits_and_off_mode(db, seed, fake_bank):
    d = seed["actors"]["director"]
    with db.session() as s:
        with pytest.raises(ValidationError):
            bank.statement(s, d, date(2026, 9, 1), date(2026, 10, 20))
        with pytest.raises(ValidationError):
            bank.acquiring(s, d, date(2026, 10, 1), date(2026, 10, 20))
        res = bank.acquiring(s, d, date(2026, 10, 19), date(2026, 10, 21))
    assert [c[2] for c in fake_bank.calls] == [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    assert res["count"] == 0
    bp.set_provider(None)
    with db.session() as s:
        update_settings(s, d, {"privat_mode": "off"})
        with pytest.raises(ValidationError):
            bank.check_connection(s, d)


# ------------------------------------------------------------------ права (прямі API-запити)
def test_api_permissions_and_director_access(client_api, db):
    c, seed, hd = client_api
    f = bp.FakeBankProvider()
    bp.set_provider(f)
    try:
        assert c.put("/api/settings", json={"privat_mode": "mock", "privat_account": IBAN}, headers=hd).status_code == 200
        assert c.post("/api/bank/check", headers=hd).json()["ok"] is True
        assert c.get("/api/bank/reconcile", params={"day": "2026-10-20"}, headers=hd).status_code == 200
        for user, pw in (("admin", "Admin1234"), ("olena", "Master123")):
            tok = c.post("/api/auth/login", json={"username": user, "password": pw}).json()["token"]
            h = {"Authorization": f"Bearer {tok}"}
            for method, url, params in [("get", "/api/bank/status", None), ("post", "/api/bank/check", None),
                                        ("get", "/api/bank/statement", {"start": "2026-10-01", "end": "2026-10-02"}),
                                        ("get", "/api/bank/acquiring", {"start": "2026-10-01", "end": "2026-10-01"}),
                                        ("get", "/api/bank/balance", {"on": "2026-10-01"}),
                                        ("get", "/api/bank/reconcile", {"day": "2026-10-20"})]:
                assert getattr(c, method)(url, params=params, headers=h).status_code == 403, (user, url)
    finally:
        bp.set_provider(None)
