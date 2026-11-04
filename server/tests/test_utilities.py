"""Комунальні та генератор: розрахунки на конкретних сумах, історія, розподіл між майстрами, без подвійних списань."""

from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from mds import clock
from mds.errors import Conflict, Forbidden, ValidationError
from mds.models import Expense, LedgerEntry, MasterCharge
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


# ------------------------------------------------------------------ електроенергія
def test_electricity_cost_history_and_confirm(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        r = u.create_reading(s, d, month=date(2026, 10, 15), read_on=date(2026, 10, 31), prev="1000", curr="1350,5",
                             tariff=432, paid_amount=150000)
        assert (r.used_milli, r.cost) == (350500, 151416)  # 350,5 кВт·год × 4,32 ₴ = 1514,16 ₴
        assert u.reading_public(r)["difference"] == 150000 - 151416
        with pytest.raises(Conflict):
            u.create_reading(s, d, month=OCT, read_on=date(2026, 10, 31), prev=0, curr=1, tariff=100)
        with pytest.raises(ValidationError):
            u.create_reading(s, d, month=date(2026, 9, 1), read_on=date(2026, 9, 30), prev=10, curr=5, tariff=100)
        oct_id = r.id
        u.confirm_reading(s, d, oct_id)
        with pytest.raises(Conflict):
            u.confirm_reading(s, d, oct_id)  # двічі не підтверджується
        # листопад: попередній показник береться з жовтня, тариф новий — жовтень не змінюється
        n = u.create_reading(s, d, month=date(2026, 11, 1), read_on=date(2026, 11, 30), curr=1500, tariff=450)
        assert n.prev_milli == 1350500 and n.cost == (149500 * 450 + 500) // 1000 == 67275
    with db.session() as s:
        o = u.reading_public(s.get(type(r), oct_id))
        assert o["tariff"] == 432 and o["cost"] == 151416 and o["confirmed"]
        e = s.scalars(select(Expense)).one()
        assert (e.category, e.amount, e.spent_on) == ("utilities", 151416, date(2026, 10, 31))
        # виправлення підтвердженого місяця — лише з причиною; витрата виправляється сторно
        with pytest.raises(ValidationError):
            u.update_reading(s, d, oct_id, {"curr": "1360,5"})
        u.update_reading(s, d, oct_id, {"curr": "1360,5"}, "Помилка в показнику")
        assert s.get(Expense, e.id).amount == (360500 * 432 + 500) // 1000 == 155736
        kinds = [x.kind for x in s.scalars(select(LedgerEntry).where(LedgerEntry.ref_type == "expense"))]
        assert kinds == ["expense", "expense_reversal", "expense"]
        dash = analytics.dashboard(s, d, OCT, date(2026, 10, 31))
        assert dash["expenses"]["utilities"] == 155736 and dash["net_profit"] == -155736


# ------------------------------------------------------------------ генератор
def test_generator_costs_snapshot_and_corrections(db, seed):
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    _gen_settings(db, seed)
    with db.session() as s:
        assert u.generator_settings(s)["hour_cost"] == 10700  # 1,5 л × 58 ₴ + 20 ₴ = 107 ₴/год
        update_settings(s, d, {"generator_hours_initial_milli": 100000})  # 100 мотогодин на старті обліку
    clock.set_now(datetime(2026, 10, 10, 10, 0))
    with db.session() as s:
        u.start_generator(s, m1)  # світло зникло — увімкнув майстер
        with pytest.raises(Conflict):
            u.start_generator(s, d)
    clock.set_now(datetime(2026, 10, 10, 12, 30))
    with db.session() as s:
        live = u.generator_state(s, d)["running"]
        assert live["hours"] == 2.5 and live["total_cost"] == 26750
        g = u.stop_generator(s, m1)
        # 2,5 год: бензин 3,75 л × 58 ₴ = 217,50 ₴; амортизація 2,5 × 20 ₴ = 50 ₴; разом 267,50 ₴
        assert (g.hours_milli, g.fuel_milli, g.fuel_cost, g.amort_cost, g.total_cost) == (2500, 3750, 21750, 5000, 26750)
        with pytest.raises(Conflict):
            u.stop_generator(s, m1)
        run_id = g.id
    _gen_settings(db, seed, price=6000)  # бензин подорожчав — минулий запуск не змінюється
    with db.session() as s:
        assert u.run_public(s.get(type(g), run_id))["total_cost"] == 26750
        with pytest.raises(Forbidden):
            u.correct_run(s, m1, run_id, {"hours": 3}, "x")
        with pytest.raises(ValidationError):
            u.correct_run(s, d, run_id, {"hours": 3}, "")
        c = u.correct_run(s, d, run_id, {"hours": 3}, "Звірка з лічильником генератора")
        # знімок ціни 58 ₴ лишається: 4,5 л × 58 = 261 ₴ + 60 ₴ = 321 ₴
        assert (c.fuel_cost, c.amort_cost, c.total_cost, c.corrected) == (26100, 6000, 32100, True)
        c = u.correct_run(s, d, run_id, {"fuel_cost": 25000}, "За чеком АЗС")
        assert c.total_cost == 25000 + 6000
        m = u.add_run(s, d, started_at=datetime(2026, 10, 20, 9), hours=2, note="Забули увімкнути")
        assert m.total_cost == (3000 * 6000 + 500) // 1000 + 4000 == 22000  # нова ціна 60 ₴
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
        # 4 мотогодини за 2 дні → 60 год на 30 днів × 107 ₴ = 6 420 ₴
        assert (f["hours_so_far"], f["projected_hours"], f["projected_cost"]) == (4, 60, 642000)


# ------------------------------------------------------------------ розподіл між майстрами
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


def test_month_end_allocation_once_and_reversal(db, seed):
    d = seed["actors"]["director"]
    _october_costs(db, seed)
    with db.session() as s:
        with pytest.raises(Conflict):
            u.allocate_month(s, d, OCT)  # режим вимкнено
        update_settings(s, d, {"utilities_charge_mode": "month_end", "utilities_split": "revenue"})
        p = u.allocation_preview(s, d, OCT)
        assert p["costs"]["total"] == 151416 + 26750 == 178166
        # 60% і 40% від 1 781,66 ₴ без втрати копійки (найбільший залишок — Олені)
        assert {r["master_id"]: r["amount"] for r in p["rows"]} == {seed["m1"]: 106900, seed["m2"]: 71266}
        res = u.allocate_month(s, d, OCT)
        assert res["created"] == 2
        with pytest.raises(Conflict):
            u.allocate_month(s, d, OCT)  # повторно — заборонено
    with db.session() as s:
        b1 = orders.master_balance(s, seed["m1"])
        assert b1 == {"accrued": 30000, "paid": 0, "charged": 106900, "balance": 30000 - 106900}
        dash = analytics.dashboard(s, d, OCT, date(2026, 10, 31))
        # чистий прибуток: валовий 100000−30000−16000=54000 − електрика − генератор + компенсація майстрів
        assert dash["expenses"]["master_charges"] == 178166
        assert dash["net_profit"] == 54000 - 151416 - 26750 + 178166
        summ = u.month_summary(s, d, OCT)
        assert summ["charged_to_masters"] == 178166 and summ["net_impact"] == 0
        assert s.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind == "master_charge")) == 2
        # скасування — сторно, після нього можна розподілити заново (порівну)
        with pytest.raises(ValidationError):
            u.reverse_month(s, d, OCT, "")
        assert u.reverse_month(s, d, OCT, "Змінили принцип розподілу") == 2
        assert u.charges_total(s, d_from=OCT, d_to=date(2026, 10, 31)) == 0
        update_settings(s, d, {"utilities_split": "equal"})
        u.allocate_month(s, d, OCT)
        assert sorted(c["amount"] for c in u.charges_list(s, d, m=OCT) if not c["reversed"] and c["amount"] > 0) == [89083, 89083]
        assert u.charges_total(s) == 178166  # жодного подвійного списання


def test_allocation_not_before_month_end(db, seed):
    d = seed["actors"]["director"]
    _october_costs(db, seed)
    with db.session() as s:
        update_settings(s, d, {"utilities_charge_mode": "month_end"})
        with pytest.raises(Conflict):
            u.allocate_month(s, d, date(2026, 11, 1))


def _shift_day(db, seed, day, amount, pay="cash"):
    _visit(db, seed, datetime(2026, 11, day, 10), amount, "m1", pay=pay)
    clock.set_now(datetime(2026, 11, day, 19))
    with db.session() as s:
        return orders.close_shift(s, seed["actors"]["m1"], seed["m1"]).id


def test_fixed_per_shift_charge_and_cash(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        update_settings(s, d, {"utilities_charge_mode": "fixed", "utilities_fixed_amount": 5000, "utilities_fixed_per": "shift"})
    sid = _shift_day(db, seed, 3, 60000)
    with db.session() as s:
        sh = orders.get_shift(s, d, sid)
        r = sh.report
        # заробіток 300 ₴ − 50 ₴ комунальні = 250 ₴ бере з готівки 600 ₴, у касі лишається 350 ₴
        assert (r["master_earning"], r["utilities_charge"], r["net_earning"]) == (30000, 5000, 25000)
        assert (r["cash"]["master_takes"], r["cash"]["leave_in_till"], r["payout_due"]) == (25000, 35000, 0)
        assert orders.master_balance(s, seed["m1"])["balance"] == 0
        assert s.scalar(select(func.count()).select_from(MasterCharge)) == 1
        assert u.charge_on_shift_close(s, d, sh) == 0  # повторно для тієї ж зміни — ні
    _shift_day(db, seed, 4, 10000, pay="card")  # картка: 50 ₴ − 50 ₴ = 0 до виплати
    with db.session() as s:
        assert u.charges_total(s, master_id=seed["m1"]) == 10000
        assert orders.master_balance(s, seed["m1"])["balance"] == 0


def test_fixed_per_month_charged_once_and_blocks_month_end(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        update_settings(s, d, {"utilities_charge_mode": "fixed", "utilities_fixed_amount": 20000, "utilities_fixed_per": "month"})
    _shift_day(db, seed, 3, 60000)
    _shift_day(db, seed, 4, 60000)
    with db.session() as s:
        assert u.charges_total(s, master_id=seed["m1"]) == 20000  # лише раз за листопад
        update_settings(s, d, {"utilities_charge_mode": "month_end"})
    clock.set_now(datetime(2026, 12, 2, 9))
    with db.session() as s:
        with pytest.raises(Conflict):
            u.allocate_month(s, d, date(2026, 11, 1))  # у місяці вже є списання — без подвійного


def test_permissions(client_api):
    c, ids, d = client_api
    m = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]}
    a = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": "Admin1234"}).json()["token"]}
    body = {"month": "2026-10-01", "read_on": "2026-10-31", "prev": 0, "curr": 10, "tariff": 432}
    assert c.post("/api/utilities/readings", headers=m, json=body).status_code == 403
    assert c.post("/api/utilities/readings", headers=a, json=body).status_code == 403
    r = c.post("/api/utilities/readings", headers=d, json=body)
    assert r.status_code == 201 and r.json()["cost"] == 4320
    assert c.post(f"/api/utilities/readings/{r.json()['id']}/confirm", headers=a).status_code == 403
    for url in ("/api/utilities/summary", "/api/utilities/charges?master_id=999"):
        assert c.get(url, headers=a).status_code == 403
    assert c.post("/api/utilities/allocate", headers=m, json={"month": "2026-10-01"}).status_code == 403
    # перемикач генератора — будь-хто з персоналу, але без фінансового прогнозу для майстра
    assert c.post("/api/generator/start", headers=m, json={}).status_code == 200
    st = c.get("/api/generator", headers=m).json()
    assert st["running"] and "forecast" not in st
    assert c.post("/api/generator/stop", headers=a).status_code == 200
    assert c.patch("/api/generator/runs/1", headers=m, json={"hours": 1, "reason": "x"}).status_code == 403
    # майстер бачить лише свої списання
    assert c.get("/api/utilities/charges", headers=m).status_code == 200
