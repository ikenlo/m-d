"""Оплата карткою через «Термінал» ПриватБанку: підпис за документацією, закриття замовлення лише після
перевірки в банку, без дублів, відмова/скасування, права, API. Мережі немає — емулятор банку."""

import json
import urllib.parse
from datetime import datetime

import pytest
from sqlalchemy import func, select

from mds import clock
from mds.errors import Conflict, Forbidden, ValidationError
from mds.models import ApptStatus, Appointment, CardPayment, Order
from mds.services import bookings, card_terminal
from mds.services import card_terminal_providers as tp
from mds.services.common import update_settings


def test_signature_matches_bank_example():
    # приклад з документації банку (app.privatbank.ua/nfcpos)
    data = tp.body_json({"operation": "pay", "amount": None, "purpose": "Test"}, 333)
    assert data == '{"operation":"pay","amount":3.33,"purpose":"Test"}'
    assert tp.sign("abcdef", "1624023225", data) == "896e7808ed56bde0e3966b46b30688e0715bb439"
    assert tp.body_json({"amount": None}, 100000) == '{"amount":1000.00}'


def test_live_provider_http_contract():
    calls = []

    def http(url, body, headers, timeout):
        calls.append((url, body))
        if "token.php" in url:
            return 200, json.dumps({"success": True, "rid": "R1", "jwt": "J.W.T", "status": 200}).encode()
        return 200, json.dumps({"success": True, "pay": {"response_code": "00", "rrn": "1"}}).encode()

    p = tp.PrivatTerminalProvider("clid1", "S3KR1T", "CLIENTTOK", http=http, now=lambda: 1700000000)
    r = p.create_pay(65050, "MDS запис 7")
    url, body = calls[0]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert url.startswith("https://dio.privatbank.ua/api/nfcpos/integrators/token.php?")
    assert q["clid"] == "clid1" and q["signed"] == "1700000000"
    assert q["signature"] == tp.sign("S3KR1T", "1700000000", body.decode())
    assert json.loads(body) == {"operation": "pay", "amount": 650.5, "purpose": "MDS запис 7", "client_token": "CLIENTTOK"}
    assert b'"amount":650.50' in body and r == {"jwt": "J.W.T", "rid": "R1"}
    assert "S3KR1T" not in repr(p) and "clid1" not in repr(p)
    assert tp.deeplink("a+b/c") == "nfcterminal://executor?token=a%2Bb%2Fc&callback=mdsconnect%3A%2F%2Fterminal-result"


@pytest.mark.parametrize("status,exc", [(418, tp.TerminalUnavailable), (503, tp.TerminalUnavailable),
                                        (401, tp.TerminalError), (400, tp.TerminalError)])
def test_live_provider_errors(status, exc):
    p = tp.PrivatTerminalProvider("c", "SECRET", "t", http=lambda *a: (status, b'{"success":false,"message":"X"}'))
    with pytest.raises(exc) as e:
        p.check("jwt")
    assert "SECRET" not in str(e.value)


@pytest.fixture
def term(db, seed):
    f = tp.FakeTerminalProvider()
    tp.set_provider(f)
    with db.session() as s:
        update_settings(s, seed["actors"]["director"], {"card_terminal_mode": "mock"})
    clock.set_now(datetime(2026, 11, 5, 9))
    with db.session() as s:
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 5, 10), client_name="Оля", client_phone="0671112233")
        aid = a.id
    clock.set_now(datetime(2026, 11, 5, 11))
    yield f, aid
    tp.set_provider(None)
    clock.set_now(None)


def test_pay_closes_order_only_after_bank_confirms(db, seed, term):
    f, aid = term
    m1 = seed["actors"]["m1"]
    with db.session() as s:
        p = card_terminal.start_payment(s, m1, aid, amount=50000, client_name="Оля Коваль")
        again = card_terminal.start_payment(s, m1, aid, amount=50000)  # подвійне натискання
    assert p["status"] == "pending" and p["deeplink"].startswith("nfcterminal://executor?token=MOCK-JWT-1")
    assert again["id"] == p["id"] and len(f.ops) == 1
    with db.session() as s:
        r = card_terminal.check_payment(s, m1, p["id"])  # клієнт ще не оплатив
        assert r["status"] == "pending" and s.scalar(select(func.count(Order.id))) == 0
    f.complete("MOCK-JWT-1")
    with db.session() as s:
        r = card_terminal.check_payment(s, m1, p["id"])
    assert r["status"] == "paid" and r["rrn"] == "014722766522" and r["order_id"]
    with db.session() as s:
        o = s.get(Order, r["order_id"])
        assert (o.amount, o.payment_method, o.appointment_id) == (50000, "card", aid)
        assert o.client.name == "Оля Коваль" and o.amount == o.master_earning + o.materials_cost + o.salon_earning
        assert s.get(Appointment, aid).status == ApptStatus.COMPLETED
        # повторна перевірка й повторний старт — без дублів
        assert card_terminal.check_payment(s, m1, p["id"])["order_id"] == o.id
        with pytest.raises(Conflict):
            card_terminal.start_payment(s, m1, aid, amount=50000)
        assert s.scalar(select(func.count(Order.id))) == 1


def test_declined_then_new_attempt(db, seed, term):
    f, aid = term
    d = seed["actors"]["director"]
    with db.session() as s:
        p = card_terminal.start_payment(s, d, aid, amount=30000)
    f.complete("MOCK-JWT-1", ok=False)
    with db.session() as s:
        r = card_terminal.check_payment(s, d, p["id"])
        assert r["status"] == "failed" and r["message"] == "Відмова банку-емітента"
        assert s.get(Appointment, aid).status in ApptStatus.CLOSABLE
        p2 = card_terminal.start_payment(s, d, aid, amount=30000)
    assert p2["id"] != p["id"] and p2["status"] == "pending"


def test_cancel_checks_bank_first_and_bank_unavailable(db, seed, term):
    f, aid = term
    d = seed["actors"]["director"]
    with db.session() as s:
        p = card_terminal.start_payment(s, d, aid, amount=30000)
    f.fail_next = "unavailable"
    with db.session() as s, pytest.raises(Conflict):
        card_terminal.check_payment(s, d, p["id"])
    f.complete("MOCK-JWT-1")  # клієнт таки оплатив — скасування має це виявити, а не загубити гроші
    with db.session() as s:
        r = card_terminal.cancel_payment(s, d, p["id"])
    assert r["status"] == "paid" and r["order_id"]
    with db.session() as s:
        p3 = s.scalar(select(CardPayment))
        assert p3.status == "paid"


def test_cancel_unpaid_and_different_amount(db, seed, term):
    f, aid = term
    d = seed["actors"]["director"]
    with db.session() as s:
        p = card_terminal.start_payment(s, d, aid, amount=30000)
        with pytest.raises(Conflict):
            card_terminal.start_payment(s, d, aid, amount=35000)
        assert card_terminal.cancel_payment(s, d, p["id"])["status"] == "failed"
        assert card_terminal.start_payment(s, d, aid, amount=35000)["status"] == "pending"


def test_validation_permissions_and_modes(db, seed, term, monkeypatch):
    f, aid = term
    with db.session() as s:
        with pytest.raises(ValidationError):
            card_terminal.start_payment(s, seed["actors"]["m1"], aid, amount=99)
        with pytest.raises(Forbidden):
            card_terminal.start_payment(s, seed["actors"]["m2"], aid, amount=30000)
        for k in tp.ENV:
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(ValidationError):
            update_settings(s, seed["actors"]["director"], {"card_terminal_mode": "live"})
        with pytest.raises(ValidationError):
            update_settings(s, seed["actors"]["director"], {"card_terminal_mode": "x"})
        update_settings(s, seed["actors"]["director"], {"card_terminal_mode": "off"})
    tp.set_provider(None)
    with db.session() as s, pytest.raises(ValidationError):
        card_terminal.start_payment(s, seed["actors"]["m1"], aid, amount=30000)


def test_api_flow_mock(client_api):
    c, seed, hd = client_api
    tp.set_provider(tp.FakeTerminalProvider())
    try:
        assert c.put("/api/settings", json={"card_terminal_mode": "mock"}, headers=hd).status_code == 200
        r = c.post("/api/appointments", headers=hd, json={"master_id": seed["m1"], "service_id": seed["haircut"],
                                                          "starts_at": "2030-01-10T10:00:00", "client_name": "Тест",
                                                          "client_phone": "0679990001"})
        aid = r.json()["id"]
        p = c.post("/api/card-payments", headers=hd, json={"appointment_id": aid, "amount": 45000}).json()
        assert p["status"] == "pending" and p["deeplink"].startswith("nfcterminal://")
        r = c.post(f"/api/card-payments/{p['id']}/mock-complete", headers=hd).json()
        assert r["status"] == "paid" and r["order_id"]
        assert c.get(f"/api/appointments/{aid}/card-payments", headers=hd).json()[0]["status"] == "paid"
        assert c.get("/api/card-terminal/status", headers=hd).json()["mode"] == "mock"
    finally:
        tp.set_provider(None)
