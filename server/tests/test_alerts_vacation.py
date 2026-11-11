from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from mds import clock
from mds.errors import Conflict, Forbidden, ValidationError
from mds.models import ApptStatus, Notification, Order, OrderStatus, PermissionRequest, Role, User
from mds.services import alerts, bookings, jobs, orders, users, vacation
from mds.services.common import Actor, update_settings


def mk(s, seed, h=10, phone="0671112233"):
    return bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=seed["haircut"],
                                       starts_at=datetime(2026, 11, 2, h), client_name="К", client_phone=phone)


@pytest.fixture
def two_directors(db, seed):
    with db.session() as s:
        d2 = users.create_user(s, Actor.system(), username="director2", password="Director234", full_name="Дмитро",
                               role=Role.DIRECTOR)
        d2.telegram_chat_id = 7002
        s.get(User, seed["director"]).telegram_chat_id = 7001
        s.get(User, seed["m1"]).telegram_chat_id = 7100
        seed["director2"] = d2.id
        seed["actors"]["director2"] = Actor.of(d2)
    return seed


def test_critical_alert_both_directors_and_handled_marker(db, two_directors):
    seed = two_directors
    with db.session() as s:
        a = mk(s, seed)
        orders.close_order(s, seed["actors"]["director"], a.id, amount=0, payment_method="cash")
        rows = list(s.scalars(select(Notification).where(Notification.kind == "salon_negative")))
        assert sorted(r.user_id for r in rows) == sorted([seed["director"], seed["director2"]])
        assert all(r.chat_id for r in rows) and len({r.group_key for r in rows}) == 1
        mine = next(r for r in rows if r.user_id == seed["director2"])
        assert alerts.mark_handled(s, seed["actors"]["director2"], mine.id)["already"] is False
        s.expire_all()
        assert all(r.handled_by == seed["director2"] for r in s.scalars(select(Notification).where(Notification.kind == "salon_negative")))
        bell = alerts.my_alerts(s, seed["actors"]["director"])
        assert bell["items"][0]["handled"] is True


def test_critical_cannot_be_disabled_important_can(db, two_directors):
    seed = two_directors
    d = seed["actors"]["director"]
    with db.session() as s:
        with pytest.raises(ValidationError):
            alerts.set_disabled(s, d, ["salon_negative"])
        with pytest.raises(ValidationError):
            update_settings(s, d, {"alerts_disabled": ["master_negative_balance"]})
        alerts.set_disabled(s, d, ["earnings_change"])
        a = mk(s, seed)
        o = orders.close_order(s, seed["actors"]["m1"], a.id, amount=50000, payment_method="cash")
        orders.correct_order(s, d, o.id, {"price": 80000}, "ціна вища")
        assert not s.scalar(select(Notification.id).where(Notification.kind == "earnings_change"))
        assert any("Виправлено замовлення" in n.text for n in s.scalars(select(Notification).where(Notification.chat_id == 7100)))


def test_earnings_change_threshold(db, two_directors):
    seed = two_directors
    d = seed["actors"]["director"]
    with db.session() as s:
        a = mk(s, seed)
        o = orders.close_order(s, seed["actors"]["m1"], a.id, amount=50000, payment_method="cash")
        orders.correct_order(s, d, o.id, {"price": 58000}, "дрібниця")
        assert not s.scalar(select(Notification.id).where(Notification.kind == "earnings_change"))
        orders.correct_order(s, d, o.id, {"price": 80000}, "значно")
        recips = {n.user_id for n in s.scalars(select(Notification).where(Notification.kind == "earnings_change"))}
        assert recips == {seed["director"], seed["director2"], seed["m1"]}


def test_hourly_unpaid_and_open_shift_deduped(db, two_directors):
    seed = two_directors
    with db.session() as s:
        a = mk(s, seed)
        orders.close_order(s, seed["actors"]["m1"], a.id, amount=50000, payment_method="cash", status="unpaid")
    clock.set_now(datetime(2026, 11, 6, 10))
    with db.session() as s:
        r = alerts.hourly_checks(s)
        assert r["unpaid"] == 2 and r["shifts"] == 3
        r = alerts.hourly_checks(s)
        assert r["unpaid"] == 0 and r["shifts"] == 0


def test_info_alerts_go_to_daily_digest(db, two_directors):
    seed = two_directors
    with db.session() as s:
        a = mk(s, seed)
        bookings.set_status(s, seed["actors"]["director"], a.id, ApptStatus.NO_SHOW)
        n = s.scalar(select(Notification).where(Notification.kind == "no_show"))
        assert n.user_id == seed["m1"] and n.chat_id is None and n.digest_pending
        assert alerts.send_digests(s) == 1
        assert any("Щоденний дайджест" in x.text and x.chat_id == 7100 for x in s.scalars(select(Notification)))


def test_daily_negative_stock_and_jobs_once_a_day(db, two_directors):
    seed = two_directors
    from mds.models import Material
    with db.session() as s:
        s.get(Material, seed["gel"]).stock_milli = -1000
    clock.set_now(datetime(2026, 11, 2, 10))
    with db.session() as s:
        out = jobs.hourly(s)
        assert out["daily"]["stock"] == 2
    with db.session() as s:
        assert "daily" not in jobs.hourly(s)


def test_vacation_flow_first_director_wins(db, two_directors):
    seed = two_directors
    d, d2, m1 = seed["actors"]["director"], seed["actors"]["director2"], seed["actors"]["m1"]
    with db.session() as s:
        a = mk(s, seed)
        o = orders.close_order(s, m1, a.id, amount=50000, payment_method="cash")
        oid = o.id
    with db.session() as s, pytest.raises(Forbidden):
        vacation.create_request(s, m1, "refund", {"order_id": oid, "reason": "клієнт"})
    with db.session() as s, pytest.raises(Forbidden):
        vacation.set_mode(s, m1, True)
    with db.session() as s:
        st = vacation.set_mode(s, d, True, 14)
        assert st["active"] and len(st["actions"]) == 7
        with pytest.raises(ValidationError):
            vacation.create_request(s, m1, "pay_bonus", {})
        with pytest.raises(ValidationError):
            vacation.create_request(s, m1, "refund", {"order_id": oid})
        r = vacation.create_request(s, m1, "refund", {"order_id": oid, "reason": "клієнт незадоволений"})
        rid = r.id
        alerts_rows = list(s.scalars(select(Notification).where(Notification.kind == "permission_request")))
        assert {n.user_id for n in alerts_rows} == {seed["director"], seed["director2"]}
        assert alerts_rows[0].buttons[0][0][1] == f"s:pr:ok:{rid}"
    with db.session() as s:
        vacation.approve(s, d2, rid)
        assert s.get(Order, oid).status == OrderStatus.REFUNDED
    with db.session() as s, pytest.raises(Conflict):
        vacation.approve(s, d, rid)
    with db.session() as s:
        assert all(n.handled_by == seed["director2"] for n in s.scalars(select(Notification).where(Notification.kind == "permission_request")))
        assert s.get(PermissionRequest, rid).result_json == {"order_id": oid}


def test_vacation_withdraw_reject_and_auto_off(db, two_directors):
    seed = two_directors
    d, m1, m2 = seed["actors"]["director"], seed["actors"]["m1"], seed["actors"]["m2"]
    with db.session() as s:
        vacation.set_mode(s, d, True, 2)
        r1 = vacation.create_request(s, m1, "inventory", {"items": [{"material_id": seed["gel"], "actual": 5}]})
        r2 = vacation.create_request(s, m1, "material_receipt", {"material_id": seed["gel"], "qty": 2})
        r3 = vacation.create_request(s, m1, "force_close_shift", {})
        ids = (r1.id, r2.id, r3.id)
    with db.session() as s, pytest.raises(Forbidden):
        vacation.withdraw(s, m2, ids[0])
    with db.session() as s:
        assert vacation.withdraw(s, m1, ids[0]).status == "withdrawn"
        assert vacation.reject(s, d, ids[1], "не зараз").status == "rejected"
    with db.session() as s, pytest.raises(Conflict):
        vacation.approve(s, d, ids[0])
    clock.set_now(clock.now() + timedelta(days=3))
    with db.session() as s:
        assert vacation.auto_off(s) is True
        assert s.get(PermissionRequest, ids[2]).status == "expired"
        assert not vacation.status(s)["active"]
        with pytest.raises(ValidationError):
            update_settings(s, d, {"vacation_mode_active": True})


def test_api_bell_and_vacation(client_api, db):
    c, seed, hd = client_api
    r = c.post("/api/settings/vacation-mode", headers=hd, json={"active": True, "days": 30})
    assert r.status_code == 422
    assert c.post("/api/settings/vacation-mode", headers=hd, json={"active": True, "days": 3}).json()["active"]
    m = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]}
    assert c.get("/api/settings/vacation-mode", headers=m).json()["active"]
    r = c.post("/api/permission-requests", headers=m, json={"action": "force_close_shift", "payload": {}})
    assert r.status_code == 201
    bell = c.get("/api/alerts", headers=hd).json()
    assert bell["unread"] == 1 and bell["items"][0]["level"] == "critical"
    assert c.post("/api/alerts/read", headers=hd, json={}).json()["marked"] == 1
    assert c.get("/api/permission-requests", headers=m).json()[0]["status"] == "pending"
    assert c.post(f"/api/permission-requests/{r.json()['id']}/approve", headers=m).status_code == 403
    assert c.get("/api/alerts/settings", headers=m).status_code == 403
    assert c.put("/api/alerts/settings", headers=hd, json={"disabled": ["shift_not_closed"]}).json()["disabled"] == ["shift_not_closed"]
    _ = date


def test_bot_director_approves_permission(db, two_directors):
    from tests.test_bot import BotHarness
    seed = two_directors
    with db.session() as s:
        users.set_bot_pin(s, Actor.system(), seed["director"], "9999")
        vacation.set_mode(s, seed["actors"]["director"], True, 5)
        r = vacation.create_request(s, seed["actors"]["m1"], "inventory", {"items": [{"material_id": seed["gel"], "actual": 3}]})
        rid = r.id
    h = BotHarness(db, user_id=7001)
    h.send("👩‍🎨 Для майстрів")
    h.send("0000")
    h.click(f"s:who:{seed['director']}")
    h.send("9999")
    h.click(f"s:pr:ok:{rid}")
    assert "Дозволено" in h.session.texts()[-1]
    with db.session() as s:
        assert s.get(PermissionRequest, rid).status == "approved"
