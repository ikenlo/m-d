from datetime import datetime

import pytest
from sqlalchemy import func, select, text

from mds.errors import Conflict, Forbidden, ValidationError, VersionConflict
from mds.models import Appointment, ApptStatus, LedgerEntry, Material, Notification, Order, OrderStatus
from mds.services import analytics, bookings, orders


def mk(s, seed, master="m1", service="haircut", h=10, phone="0671112233"):
    return bookings.create_appointment(s, seed["actors"]["director"], master_id=seed[master], service_id=seed[service],
                                       starts_at=datetime(2026, 11, 2, h), client_name="Клієнт", client_phone=phone)


def _pays_all(db):
    from mds.services.common import Actor, set_setting
    with db.session() as s:
        set_setting(s, Actor.system(), "master_takes_cash", False)


def _ledger_matches_orders(s):
    kinds = ("order_closed", "order_reversal", "order_corrected", "order_refunded", "order_cancelled")
    lg = s.execute(select(func.sum(LedgerEntry.amount), func.sum(LedgerEntry.master_amount),
                          func.sum(LedgerEntry.salon_amount), func.sum(LedgerEntry.materials_amount))
                   .where(LedgerEntry.kind.in_(kinds))).one()
    live = s.execute(select(func.coalesce(func.sum(Order.amount), 0), func.coalesce(func.sum(Order.master_earning), 0),
                            func.coalesce(func.sum(Order.salon_earning), 0), func.coalesce(func.sum(Order.materials_cost), 0))
                     .where(Order.status.in_(OrderStatus.ACCRUING))).one()
    refunded_m = s.scalar(select(func.coalesce(func.sum(Order.materials_cost), 0)).where(Order.status == OrderStatus.REFUNDED))
    assert lg[0] == live[0] and lg[1] == live[1]
    assert lg[3] == live[3] + refunded_m and lg[2] == live[2] - refunded_m
    assert lg[0] == lg[1] + lg[2] + lg[3]


def test_unpaid_accrues_to_master_and_mark_paid(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    _pays_all(db)
    with db.session() as s:
        a = mk(s, seed)
        o = orders.close_order(s, m1, a.id, amount=50000, payment_method="cash", status="unpaid")
        assert o.status == "unpaid" and o.master_earning == 25000
        oid = o.id
    with db.session() as s:
        r = orders.shift_report(s, orders.current_shift(s, seed["m1"]))
        assert r["master_earning"] == 25000 and r["revenue"] == 0 and r["unpaid"] == {"count": 1, "amount": 50000}
        assert r["cash"]["from_clients"] == 0
        sh = orders.close_shift(s, m1, seed["m1"])
        assert sh.payout_due == 25000
        assert orders.list_orders(s, d, status="unpaid")[0].id == oid
        dash = analytics.dashboard(s, d, datetime(2026, 11, 1).date(), datetime(2026, 11, 30).date())
        assert dash["revenue"] == 0
    with db.session() as s, pytest.raises(Forbidden):
        orders.mark_paid(s, m1, oid, payment_method="card")
    with db.session() as s:
        o = orders.mark_paid(s, d, oid, payment_method="card", amount=45000, reason="знижка при оплаті")
        assert o.status == "completed" and o.amount == 45000 and o.master_earning == 25000 and o.salon_earning == 20000
        assert o.payment_method == "card"
    with db.session() as s, pytest.raises(Conflict):
        orders.mark_paid(s, d, oid, payment_method="card")
    with db.session() as s:
        _ledger_matches_orders(s)


def test_refund_after_payout_makes_negative_balance_and_alerts(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    _pays_all(db)
    with db.session() as s:
        s.get(__import__("mds.models", fromlist=["User"]).User, seed["director"]).telegram_chat_id = 9001
        a = mk(s, seed)
        o = orders.close_order(s, m1, a.id, amount=50000, payment_method="cash",
                               materials=[{"material_id": seed["gel"], "qty": 1}])
        sh = orders.close_shift(s, m1, seed["m1"])
        orders.confirm_payout(s, d, master_id=seed["m1"], amount=25000, shift_id=sh.id)
        oid = o.id
        stock_before = s.get(Material, seed["gel"]).stock_milli
    with db.session() as s, pytest.raises(ValidationError):
        orders.refund_order(s, d, oid, " ")
    with db.session() as s:
        o = orders.refund_order(s, d, oid, "клієнт незадоволений")
        assert o.status == "refunded"
        assert orders.master_balance(s, seed["m1"])["balance"] == -25000
        assert s.get(Material, seed["gel"]).stock_milli == stock_before
        texts = [n.text for n in s.scalars(select(Notification))]
        assert any("Від'ємний баланс" in t for t in texts)
    with db.session() as s, pytest.raises(Conflict):
        orders.refund_order(s, d, oid, "ще раз")
    with db.session() as s, pytest.raises(Conflict):
        orders.correct_order(s, d, oid, {"amount": 1}, "x")
    with db.session() as s:
        _ledger_matches_orders(s)


def test_cancel_returns_materials_and_cancels_appointment(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        a = mk(s, seed)
        before = s.get(Material, seed["gel"]).stock_milli
        o = orders.close_order(s, m1, a.id, amount=50000, payment_method="card",
                               materials=[{"material_id": seed["gel"], "qty": 2}])
        oid, aid = o.id, a.id
    with db.session() as s, pytest.raises(Forbidden):
        orders.cancel_order(s, m1, oid, "помилка")
    with db.session() as s:
        orders.cancel_order(s, d, oid, "закрито помилково")
        assert s.get(Appointment, aid).status == ApptStatus.CANCELLED
        assert s.get(Material, seed["gel"]).stock_milli == before
        assert orders.master_balance(s, seed["m1"])["accrued"] == 0
        _ledger_matches_orders(s)


def test_salon_negative_alerts_but_closes(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        a = mk(s, seed)
        o = orders.close_order(s, d, a.id, amount=10000, payment_method="cash",
                               materials=[{"name": "Фарба", "qty": 1, "unit_cost": 20000}])
        assert o.salon_earning == 10000 - 25000 - 20000
        assert s.get(Appointment, a.id).status == ApptStatus.COMPLETED


def test_salon_negative_notifies_both_directors(db, seed):
    from mds.models import Role, User
    from mds.services import users
    d = seed["actors"]["director"]
    with db.session() as s:
        d2 = users.create_user(s, d, username="director2", password="Director123", full_name="Дмитро", role=Role.DIRECTOR)
        d2.telegram_chat_id = 7002
        s.get(User, seed["director"]).telegram_chat_id = 7001
    with db.session() as s:
        a = mk(s, seed)
        orders.close_order(s, d, a.id, amount=0, payment_method="cash")
        chats = sorted(n.chat_id for n in s.scalars(select(Notification)) if "Салон у мінусі" in n.text)
        assert chats == [7001, 7002]


def test_status_validation_and_version(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        a = mk(s, seed)
        with pytest.raises(ValidationError):
            orders.close_order(s, m1, a.id, amount=50000, payment_method="cash", status="refunded")
    with db.session() as s:
        o = orders.close_order(s, m1, a.id, amount=50000, payment_method="cash")
        v = o.version
        oid = o.id
    with db.session() as s:
        orders.correct_order(s, d, oid, {"amount": 40000}, "знижка", expected_version=v)
    with db.session() as s, pytest.raises(VersionConflict):
        orders.correct_order(s, d, oid, {"amount": 30000}, "ще знижка", expected_version=v)
    with db.session() as s, pytest.raises(VersionConflict):
        orders.refund_order(s, d, oid, "x", expected_version=v)
    with db.session() as s, pytest.raises(ValidationError):
        orders.list_orders(s, d, status="weird")


def test_no_show_has_no_financial_effect(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        a = mk(s, seed)
        bookings.set_status(s, d, a.id, ApptStatus.NO_SHOW)
        assert s.scalar(select(func.count(Order.id))) == 0
        assert s.scalar(select(func.count(LedgerEntry.id))) == 0


def test_api_status_endpoints(client_api):
    c, seed, hd = client_api
    r = c.post("/api/appointments", headers=hd, json={"master_id": seed["m1"], "service_id": seed["haircut"],
                                                      "starts_at": "2026-11-02T10:00", "client_name": "Т",
                                                      "client_phone": "0679990001"})
    aid = r.json()["id"]
    o = c.post(f"/api/appointments/{aid}/close", headers=hd, json={"amount": 50000, "payment_method": "cash",
                                                                    "status": "unpaid"}).json()
    assert o["status"] == "unpaid" and o["discount"] == 0
    assert [x["id"] for x in c.get("/api/orders?status=unpaid", headers=hd).json()] == [o["id"]]
    r = c.patch(f"/api/orders/{o['id']}", headers=hd, json={"reason": "x", "amount": 1, "version": o["version"] + 5})
    assert r.status_code == 409 and r.json()["error"] == "version_conflict"
    r = c.post(f"/api/orders/{o['id']}/mark-paid", headers=hd, json={"payment_method": "card"})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    r = c.post(f"/api/orders/{o['id']}/refund", headers=hd, json={"reason": "повернення"})
    assert r.status_code == 200 and r.json()["status"] == "refunded"


def test_migration_keeps_past_orders_unchanged(tmp_path):
    from sqlalchemy import create_engine

    from mds.migrate import upgrade
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    upgrade(url, "0024")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO users (id, username, password_hash, full_name, role, is_active, share_bp, materials_mode, "
                       "token_version, failed_logins, booking_lock, created_at) VALUES "
                       "(1,'m','x','M','master',1,4000,'split',0,0,0,'2026-01-01'),"
                       "(2,'d','x','D','director',1,NULL,NULL,0,0,0,'2026-01-01')"))
        c.execute(text("INSERT INTO clients (id, name, created_at) VALUES (1,'C','2026-01-01')"))
        c.execute(text("INSERT INTO services (id, name, duration_min, price, is_active, sort_order, price_from, needs_materials) "
                       "VALUES (1,'S',60,100000,1,0,0,1)"))
        c.execute(text("INSERT INTO appointments (id, client_id, master_id, service_id, starts_at, ends_at, price, status, source, "
                       "awaiting_client, reminded_day, reminded_2h, close_reminded, created_at, updated_at) VALUES "
                       "(1,1,1,1,'2026-01-02 10:00','2026-01-02 11:00',100000,'completed','staff',0,0,0,0,'2026-01-01','2026-01-01')"))
        c.execute(text("INSERT INTO shifts (id, master_id, status, opened_at) VALUES (1,1,'open','2026-01-02 09:00')"))
        c.execute(text("INSERT INTO orders (id, appointment_id, master_id, client_id, service_id, shift_id, amount, "
                       "materials_cost, payment_method, share_bp, materials_mode, master_earning, salon_earning, closed_at, "
                       "closed_by, revision) VALUES (1,1,1,1,1,1,80000,6000,'cash',4000,'split',29600,44400,'2026-01-02 11:00',1,1)"))
    upgrade(url)
    with eng.connect() as c:
        row = c.execute(text("SELECT price_snapshot, amount, master_rate_bp, status, master_earning, salon_earning, "
                             "materials_not_entered, version, share_bp, materials_mode FROM orders")).one()
        assert tuple(row) == (80000, 80000, 4000, "completed", 29600, 44400, 1, 1, 4000, "split")
        assert c.execute(text("SELECT master_rate_bp FROM users ORDER BY id")).scalars().all() == [4000, 5000]
