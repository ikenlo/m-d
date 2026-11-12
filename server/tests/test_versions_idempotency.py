from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from mds import clock
from mds.errors import ValidationError
from mds.models import Expense, IdempotencyKey, Order, Payout
from mds.services import bookings, expenses, idempotency, orders


def login(c, u, p):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]}


def _appt(c, hd, seed, h=10, phone="0679990001"):
    r = c.post("/api/appointments", headers=hd, json={"master_id": seed["m1"], "service_id": seed["haircut"],
                                                      "starts_at": f"2026-11-02T{h:02d}:00", "client_name": "Т",
                                                      "client_phone": phone})
    return r.json()


def test_close_same_key_same_response_other_payload_409(client_api):
    c, seed, hd = client_api
    a = _appt(c, hd, seed)
    body = {"amount": 50000, "payment_method": "cash", "idempotency_key": "tap-9"}
    r1 = c.post(f"/api/appointments/{a['id']}/close", headers=hd, json=body)
    r2 = c.post(f"/api/appointments/{a['id']}/close", headers=hd, json=body)
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    r3 = c.post(f"/api/appointments/{a['id']}/close", headers=hd, json={**body, "amount": 1})
    assert r3.status_code == 409 and r3.json()["error"] == "idempotency_conflict"
    r4 = c.post(f"/api/appointments/{a['id']}/close", headers=hd, json={**body, "idempotency_key": "tap-10"})
    assert r4.status_code == 409


def test_payout_and_expense_idempotency_headers(client_api):
    c, seed, hd = client_api
    h = {**hd, "Idempotency-Key": "pay-1"}
    body = {"master_id": seed["m1"], "amount": 10000, "tag": "advance"}
    r1, r2 = c.post("/api/payouts", headers=h, json=body), c.post("/api/payouts", headers=h, json=body)
    assert r1.status_code == 201 and r1.json() == r2.json() and r1.json()["tag"] == "advance"
    e = {"category": "other", "amount": 500, "spent_on": "2026-11-02"}
    he = {**hd, "Idempotency-Key": "exp-1"}
    assert c.post("/api/expenses", headers=he, json=e).json() == c.post("/api/expenses", headers=he, json=e).json()
    assert c.post("/api/expenses", headers=he, json={**e, "amount": 600}).status_code == 409


def test_payout_tags_validation(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        for bad in ("product_bonus", "weird"):
            with pytest.raises(ValidationError):
                orders.confirm_payout(s, d, master_id=seed["m1"], amount=100, tag=bad)
        p = orders.confirm_payout(s, d, master_id=seed["m1"], amount=100, tag="correction_overpayment")
        assert p.tag == "correction_overpayment" and p.shift_id is None
        assert orders.master_balance(s, seed["m1"])["balance"] == -100


def test_cleanup_after_30_days(db, seed):
    with db.session() as s:
        idempotency.run(s, scope="x", user_id=1, key="old", payload={}, fn=lambda: {"ok": 1})
    clock.set_now(clock.now() + timedelta(days=31))
    with db.session() as s:
        idempotency.run(s, scope="x", user_id=1, key="new", payload={}, fn=lambda: {"ok": 2})
        assert idempotency.cleanup(s) == 1
        assert [k.key for k in s.scalars(select(IdempotencyKey))] == ["x:1:new"]


def test_version_conflict_returns_current_state(client_api, db):
    c, seed, hd = client_api
    a = _appt(c, hd, seed)
    r = c.patch(f"/api/appointments/{a['id']}", headers=hd, json={"comment": "x", "version": a["version"]})
    assert r.status_code == 200 and r.json()["version"] == a["version"] + 1
    r = c.patch(f"/api/appointments/{a['id']}", headers=hd, json={"comment": "y", "version": a["version"]})
    assert r.status_code == 409 and r.json()["current"]["comment"] == "x" and r.json()["current_version"] == a["version"] + 1
    e = c.post("/api/expenses", headers=hd, json={"category": "other", "amount": 500, "spent_on": "2026-11-02"}).json()
    assert c.patch(f"/api/expenses/{e['id']}", headers=hd, json={"amount": 600, "version": e["version"]}).status_code == 200
    r = c.patch(f"/api/expenses/{e['id']}", headers=hd, json={"amount": 700, "version": e["version"]})
    assert r.status_code == 409 and r.json()["current"]["amount"] == 600
    assert c.delete(f"/api/expenses/{e['id']}?version={e['version']}", headers=hd).status_code == 409
    o = c.post(f"/api/appointments/{a['id']}/close", headers=hd, json={"amount": 50000, "payment_method": "cash"}).json()
    r = c.post(f"/api/orders/{o['id']}/refund", headers=hd, json={"reason": "x", "version": o["version"] - 1})
    assert r.status_code == 409 and r.json()["current"]["status"] == "completed"


def test_concurrent_orm_update_detected(db, seed):
    from sqlalchemy.orm.exc import StaleDataError
    d = seed["actors"]["director"]
    with db.session() as s:
        e = expenses.create_expense(s, d, category="other", amount=100, spent_on=datetime(2026, 11, 2).date())
        eid = e.id
    s1, s2 = db.session_factory(), db.session_factory()
    try:
        e1, e2 = s1.get(Expense, eid), s2.get(Expense, eid)
        e1.amount = 200
        s1.commit()
        e2.amount = 300
        with pytest.raises(StaleDataError):
            s2.commit()
    finally:
        s1.close()
        s2.close()


def test_master_views_hide_salon_and_revenue(client_api):
    c, seed, hd = client_api
    a = _appt(c, hd, seed)
    m = login(c, "olena", "Master123")
    o = c.post(f"/api/appointments/{a['id']}/close", headers=m, json={"amount": 50000, "payment_method": "cash"}).json()
    assert "salon_earning" not in o and o["master_earning"] == 25000
    assert "salon_earning" not in c.get(f"/api/orders/{o['id']}", headers=m).json()
    assert "salon_earning" in c.get(f"/api/orders/{o['id']}", headers=hd).json()
    sh = c.get("/api/shifts/current", headers=m).json()
    assert sh["view"] == "master" and "revenue" not in sh["report"] and "salon_earning" not in sh["report"]
    assert sh["report"]["master_earning"] == 25000 and "cancellations" in sh["report"]
    ds = c.get(f"/api/shifts/{sh['id']}", headers=hd).json()
    assert ds["view"] == "director" and ds["report"]["revenue"] == 50000


def test_order_card_history_last_five(db, seed, client_api):
    c, ids, hd = client_api
    a = _appt(c, hd, ids)
    o = c.post(f"/api/appointments/{a['id']}/close", headers=hd, json={"amount": 50000, "payment_method": "cash"}).json()
    for i in range(6):
        c.patch(f"/api/orders/{o['id']}", headers=hd, json={"reason": f"r{i}", "amount": 40000 + i})
    r = c.get(f"/api/orders/{o['id']}", headers=hd).json()
    assert len(r["history"]) == 5 and r["history_total"] == 7 and r["history"][0]["reason"] == "r5"


def test_double_payout_still_prevented_with_tags(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    from mds.services.common import Actor, set_setting
    with db.session() as s:
        set_setting(s, Actor.system(), "master_takes_cash", False)
        a = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="К", client_phone="0670000001")
        orders.close_order(s, m1, a.id, amount=50000, payment_method="cash")
        sh = orders.close_shift(s, m1, seed["m1"])
        with pytest.raises(ValidationError):
            orders.confirm_payout(s, d, master_id=seed["m1"], amount=25000, shift_id=sh.id, tag="bonus")
        orders.confirm_payout(s, d, master_id=seed["m1"], amount=25000, shift_id=sh.id)
        assert s.scalar(select(func.count(Payout.id))) == 1
        assert s.scalar(select(func.count(Order.id))) == 1
