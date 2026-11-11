from datetime import date, datetime

import pytest
from sqlalchemy import select

from mds import clock
from mds.errors import Conflict, Forbidden, ValidationError
from mds.models import Expense, LedgerEntry
from mds.services import analytics, bookings, orders, utilities as u
from mds.services.common import update_settings

OCT = date(2026, 10, 1)


def _gen_settings(db, seed, lph=1500, price=5800, amort=2000):
    with db.session() as s:
        update_settings(s, seed["actors"]["director"], {"generator_fuel_lph_milli": lph, "generator_fuel_price": price,
                                                       "generator_amort_per_hour": amort})


def _visit(db, seed, at, amount, master="m1", phone="0670000001", pay="cash"):
    clock.set_now(at.replace(hour=7))
    with db.session() as s:
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed[master], service_id=seed["haircut"],
                                        starts_at=at, client_name="К", client_phone=phone)
        aid = a.id
    clock.set_now(at.replace(hour=at.hour + 1))
    with db.session() as s:
        orders.close_order(s, seed["actors"][master], aid, amount=amount, payment_method=pay)


def test_electricity_cost_history_and_confirm(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        r = u.create_reading(s, d, month=date(2026, 10, 15), read_on=date(2026, 10, 31), prev="1000", curr="1350,5",
                             tariff=432, paid_amount=150000)
        assert (r.used_milli, r.cost) == (350500, 151416)
        assert u.reading_public(r)["difference"] == 150000 - 151416
        with pytest.raises(Conflict):
            u.create_reading(s, d, month=OCT, read_on=date(2026, 10, 31), prev=0, curr=1, tariff=100)
        with pytest.raises(ValidationError):
            u.create_reading(s, d, month=date(2026, 9, 1), read_on=date(2026, 9, 30), prev=10, curr=5, tariff=100)
        oct_id = r.id
        u.confirm_reading(s, d, oct_id)
        with pytest.raises(Conflict):
            u.confirm_reading(s, d, oct_id)
        n = u.create_reading(s, d, month=date(2026, 11, 1), read_on=date(2026, 11, 30), curr=1500, tariff=450)
        assert n.prev_milli == 1350500 and n.cost == (149500 * 450 + 500) // 1000 == 67275
    with db.session() as s:
        o = u.reading_public(s.get(type(r), oct_id))
        assert o["tariff"] == 432 and o["cost"] == 151416 and o["confirmed"]
        e = s.scalars(select(Expense)).one()
        assert (e.category, e.amount, e.spent_on) == ("utilities", 151416, date(2026, 10, 31))
        with pytest.raises(ValidationError):
            u.update_reading(s, d, oct_id, {"curr": "1360,5"})
        u.update_reading(s, d, oct_id, {"curr": "1360,5"}, "Помилка в показнику")
        assert s.get(Expense, e.id).amount == (360500 * 432 + 500) // 1000 == 155736
        kinds = [x.kind for x in s.scalars(select(LedgerEntry).where(LedgerEntry.ref_type == "expense"))]
        assert kinds == ["expense", "expense_reversal", "expense"]
        dash = analytics.dashboard(s, d, OCT, date(2026, 10, 31))
        assert dash["expenses"]["utilities"] == 155736 and dash["net_profit"] == -155736


def test_generator_costs_snapshot_and_corrections(db, seed):
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    _gen_settings(db, seed)
    with db.session() as s:
        assert u.generator_settings(s)["hour_cost"] == 10700
        update_settings(s, d, {"generator_hours_initial_milli": 100000})
    clock.set_now(datetime(2026, 10, 10, 10, 0))
    with db.session() as s:
        u.start_generator(s, m1)
        with pytest.raises(Conflict):
            u.start_generator(s, d)
    clock.set_now(datetime(2026, 10, 10, 12, 30))
    with db.session() as s:
        live = u.generator_state(s, d)["running"]
        assert live["hours"] == 2.5 and live["total_cost"] == 26750
        g = u.stop_generator(s, m1)
        assert (g.hours_milli, g.fuel_milli, g.fuel_cost, g.amort_cost, g.total_cost) == (2500, 3750, 21750, 5000, 26750)
        with pytest.raises(Conflict):
            u.stop_generator(s, m1)
        run_id = g.id
    _gen_settings(db, seed, price=6000)
    with db.session() as s:
        assert u.run_public(s.get(type(g), run_id))["total_cost"] == 26750
        with pytest.raises(Forbidden):
            u.correct_run(s, m1, run_id, {"hours": 3}, "x")
        with pytest.raises(ValidationError):
            u.correct_run(s, d, run_id, {"hours": 3}, "")
        c = u.correct_run(s, d, run_id, {"hours": 3}, "Звірка з лічильником генератора")
        assert (c.fuel_cost, c.amort_cost, c.total_cost, c.corrected) == (26100, 6000, 32100, True)
        c = u.correct_run(s, d, run_id, {"fuel_cost": 25000}, "За чеком АЗС")
        assert c.total_cost == 25000 + 6000
        m = u.add_run(s, d, started_at=datetime(2026, 10, 20, 9), hours=2, note="Забули увімкнути")
        assert m.total_cost == (3000 * 6000 + 500) // 1000 + 4000 == 22000
        st = u.generator_state(s, d)
        assert st["motohours"] == 100 + 3 + 2
        u.set_motohours(s, d, 110)
        assert u.generator_state(s, d)["motohours"] == 110
        dash = analytics.dashboard(s, d, OCT, date(2026, 10, 31))
        assert dash["expenses"]["generator"] == 31000 + 22000 and dash["net_profit"] == -53000
        summ = u.month_summary(s, d, OCT)
        assert summ["generator"] == {"runs": 2, "hours": 5.0, "fuel_l": 7.5, "fuel_cost": 25000 + 18000,
                                     "amort_cost": 10000, "total": 53000}


def test_generator_forecast(db, seed):
    d = seed["actors"]["director"]
    _gen_settings(db, seed)
    with db.session() as s:
        u.add_run(s, d, started_at=datetime(2026, 11, 1, 10), hours=4)
    clock.set_now(datetime(2026, 11, 2, 18, 0))
    with db.session() as s:
        f = u.generator_forecast(s, d, date(2026, 11, 2))
        assert (f["hours_so_far"], f["projected_hours"], f["projected_cost"]) == (4, 60, 642000)


def _october_costs(db, seed):
    d = seed["actors"]["director"]
    _visit(db, seed, datetime(2026, 10, 5, 10), 60000, "m1")
    _visit(db, seed, datetime(2026, 10, 6, 10), 40000, "m2", phone="0670000002")
    _gen_settings(db, seed)
    with db.session() as s:
        r = u.create_reading(s, d, month=OCT, read_on=date(2026, 10, 31), prev=1000, curr="1350,5", tariff=432)
        u.confirm_reading(s, d, r.id)
        u.add_run(s, d, started_at=datetime(2026, 10, 10, 10), hours="2,5")
    clock.set_now(datetime(2026, 11, 2, 8))


def test_permissions(client_api):
    c, ids, d = client_api
    m = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]}
    body = {"month": "2026-10-01", "read_on": "2026-10-31", "prev": 0, "curr": 10, "tariff": 432}
    assert c.post("/api/utilities/readings", headers=m, json=body).status_code == 403
    r = c.post("/api/utilities/readings", headers=d, json=body)
    assert r.status_code == 201 and r.json()["cost"] == 4320
    assert c.post(f"/api/utilities/readings/{r.json()['id']}/confirm", headers=m).status_code == 403
    assert c.get("/api/utilities/summary", headers=m).status_code == 403
    assert c.post("/api/generator/start", headers=m, json={}).status_code == 200
    st = c.get("/api/generator", headers=m).json()
    assert st["running"] and "forecast" not in st
    assert c.post("/api/generator/stop", headers=m).status_code == 200
    assert c.patch("/api/generator/runs/1", headers=m, json={"hours": 1, "reason": "x"}).status_code == 403


def test_no_utility_deductions_from_masters(client_api):
    c, ids, d = client_api
    for method, url in (("get", "/api/utilities/allocation?month=2026-10-01"), ("post", "/api/utilities/allocate"),
                        ("post", "/api/utilities/reverse"), ("get", "/api/utilities/charges")):
        assert getattr(c, method)(url, headers=d).status_code in (404, 405)
    assert c.put("/api/settings", headers=d, json={"utilities_charge_mode": "fixed"}).status_code == 422


def test_generator_hours_by_meter_and_by_hours(db, seed):
    d, adm, m1 = seed["actors"]["director"], seed["actors"]["director"], seed["actors"]["m1"]
    _gen_settings(db, seed)
    clock.set_now(datetime(2026, 10, 20, 18, 0))
    with db.session() as s:
        update_settings(s, d, {"generator_hours_initial_milli": 100000})
        g = u.record_hours(s, adm, on=date(2026, 10, 5), current="104,5")
        assert (g.hours_milli, g.fuel_milli, g.fuel_cost, g.amort_cost, g.total_cost) == (4500, 6750, 39150, 9000, 48150)
        assert g.power_outage is False and "100 → 104,5" in g.note
        g2 = u.record_hours(s, d, on=date(2026, 10, 12), hours="2")
        assert g2.total_cost == 21400
        assert u.generator_state(s, d)["motohours"] == 106.5
        with pytest.raises(ValidationError):
            u.record_hours(s, d, on=date(2026, 10, 13), current="106")
        with pytest.raises(ValidationError):
            u.record_hours(s, d, on=date(2026, 10, 13))
        with pytest.raises(ValidationError):
            u.record_hours(s, d, on=date(2026, 10, 21), hours="1")
        with pytest.raises(Forbidden):
            u.record_hours(s, m1, on=date(2026, 10, 13), hours="1")
        hist = u.generator_history(s, d, 3)
    clock.set_now(None)
    assert [x["month"] for x in hist] == ["2026-08-01", "2026-09-01", "2026-10-01"]
    assert hist[-1] == {"month": "2026-10-01", "runs": 2, "hours": 6.5, "fuel_l": 9.75, "fuel_cost": 56550,
                        "amort_cost": 13000, "total": 69550}
