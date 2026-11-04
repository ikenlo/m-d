from datetime import date, datetime, timedelta

import pytest

from mds import clock
from mds.errors import Conflict, Forbidden, ValidationError
from mds.models import Appointment, ApptStatus, LedgerEntry, Order, Payout, Shift, ShiftStatus
from mds.services import analytics, bookings, expenses, orders, users

DAY = date(2026, 11, 2)


def at(h, m=0):
    return datetime(2026, 11, 2, h, m)


def mk(s, seed, master="m1", service="manicure", h=10, phone="0671112233"):
    return bookings.create_appointment(
        s, seed["actors"]["admin"], master_id=seed[master], service_id=seed[service],
        starts_at=at(h), client_name="Клієнт", client_phone=phone,
    )


def test_close_order_default_split_and_ledger(db, seed):
    m1 = seed["actors"]["m1"]
    with db.session() as s:
        a = mk(s, seed)
        o = orders.close_order(
            s, m1, a.id, amount=70000, payment_method="card",
            materials=[{"material_id": seed["gel"], "qty": 2}], idempotency_key="k1",
        )
        assert o.materials_cost == 6000
        assert o.master_earning == 35000  # 50% від 700 грн
        assert o.salon_earning == 70000 - 35000 - 6000
        assert o.shift.status == ShiftStatus.OPEN
    with db.session() as s:
        assert s.get(Appointment, a.id).status == ApptStatus.COMPLETED
        le = s.query(LedgerEntry).filter_by(kind="order_closed").one()
        assert (le.amount, le.master_amount, le.salon_amount, le.materials_amount) == (70000, 35000, 29000, 6000)


def test_individual_share_and_materials_mode(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        users.update_user(s, d, seed["m2"], {"materials_mode": "master"})
        a = mk(s, seed, master="m2")
        o = orders.close_order(s, d, a.id, amount=100000, payment_method="cash",
                               materials=[{"name": "Фольга", "qty": 1, "unit_cost": 2000}])
        # 40% від 1000 грн - 20 грн матеріалів
        assert o.master_earning == 38000
        assert o.salon_earning == 60000
        assert o.share_bp == 4000 and o.materials_mode == "master"


def test_double_close_is_prevented(db, seed):
    m1 = seed["actors"]["m1"]
    with db.session() as s:
        a = mk(s, seed)
        o1 = orders.close_order(s, m1, a.id, amount=60000, payment_method="cash", idempotency_key="abc")
    with db.session() as s:
        # повтор того самого запиту — повертає те саме замовлення
        o2 = orders.close_order(s, m1, a.id, amount=60000, payment_method="cash", idempotency_key="abc")
        assert o2.id == o1.id
    with db.session() as s, pytest.raises(Conflict):
        orders.close_order(s, m1, a.id, amount=60000, payment_method="cash", idempotency_key="other")
    with db.session() as s, pytest.raises(Conflict):
        orders.close_order(s, m1, a.id, amount=60000, payment_method="cash")
    with db.session() as s:
        assert s.query(Order).count() == 1
        assert s.query(LedgerEntry).filter_by(kind="order_closed").count() == 1


def test_cannot_close_cancelled_or_foreign(db, seed):
    adm, m2 = seed["actors"]["admin"], seed["actors"]["m2"]
    with db.session() as s:
        a = mk(s, seed)
        b = mk(s, seed, h=14, phone="0501231231")
        bookings.set_status(s, adm, b.id, ApptStatus.CANCELLED)
    with db.session() as s, pytest.raises(Forbidden):
        orders.close_order(s, m2, a.id, amount=1, payment_method="cash")
    with db.session() as s, pytest.raises(Conflict):
        orders.close_order(s, adm, b.id, amount=1, payment_method="cash")
    with db.session() as s, pytest.raises(ValidationError):
        orders.close_order(s, adm, a.id, amount=1, payment_method="bitcoin")
    with db.session() as s:
        # невдала спроба не змінила статус
        assert s.get(Appointment, a.id).status == ApptStatus.CONFIRMED


def _director_pays_all(db):
    """Режим «усю зарплату виплачує директор» (майстер не бере з каси)."""
    from mds.services.common import Actor, set_setting
    with db.session() as s:
        set_setting(s, Actor.system(), "master_takes_cash", False)


def test_shift_close_report_and_payout(db, seed):
    m1, d, adm = seed["actors"]["m1"], seed["actors"]["director"], seed["actors"]["admin"]
    _director_pays_all(db)
    with db.session() as s:
        a = mk(s, seed, h=9)
        b = mk(s, seed, service="haircut", h=11, phone="0502223344")
        c = mk(s, seed, service="haircut", h=13, phone="0503334455")
        bookings.set_status(s, adm, c.id, ApptStatus.NO_SHOW)
        orders.close_order(s, m1, a.id, amount=60000, payment_method="cash")
        orders.close_order(s, m1, b.id, amount=50000, payment_method="card")
    clock.set_now(datetime(2026, 11, 2, 20, 0))
    with db.session() as s:
        sh = orders.close_shift(s, m1, seed["m1"])
        r = sh.report
        assert r["clients_count"] == 2
        assert r["services_count"] == 2
        assert r["revenue"] == 110000
        assert r["master_earning"] == 55000
        assert r["payout_due"] == 55000
        assert r["cancellations"] == 1
        assert r["by_payment"] == {"cash": 60000, "card": 50000}
        shift_id = sh.id
    with db.session() as s, pytest.raises(Conflict):
        orders.close_shift(s, m1, seed["m1"])  # немає відкритої зміни
    with db.session() as s, pytest.raises(Forbidden):
        orders.confirm_payout(s, m1, master_id=seed["m1"], amount=55000, shift_id=shift_id)
    with db.session() as s:
        orders.confirm_payout(s, d, master_id=seed["m1"], amount=55000, shift_id=shift_id)
    with db.session() as s, pytest.raises(Conflict):
        orders.confirm_payout(s, d, master_id=seed["m1"], amount=55000, shift_id=shift_id)
    with db.session() as s:
        assert orders.master_balance(s, seed["m1"]) == {"accrued": 55000, "paid": 55000, "charged": 0, "balance": 0}


def test_director_correction_reverses_and_updates_balance(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    _director_pays_all(db)
    with db.session() as s:
        a = mk(s, seed)
        o = orders.close_order(s, m1, a.id, amount=60000, payment_method="cash")
        orders.close_shift(s, m1, seed["m1"])
        orders.confirm_payout(s, d, master_id=seed["m1"], amount=30000, shift_id=o.shift_id)
    with db.session() as s, pytest.raises(Forbidden):
        orders.correct_order(s, m1, o.id, {"amount": 80000}, "помилка")
    with db.session() as s, pytest.raises(ValidationError):
        orders.correct_order(s, d, o.id, {"amount": 80000}, "  ")
    with db.session() as s:
        o2 = orders.correct_order(s, d, o.id, {"amount": 80000, "payment_method": "card"}, "Майстер ввів не ту суму")
        assert o2.master_earning == 40000 and o2.revision == 2
    with db.session() as s:
        kinds = [e.kind for e in s.query(LedgerEntry).filter_by(ref_type="order").order_by(LedgerEntry.id)]
        assert kinds == ["order_closed", "order_reversal", "order_corrected"]
        net = sum(e.amount for e in s.query(LedgerEntry).filter_by(ref_type="order"))
        assert net == 80000
        # виплачено 300, нараховано тепер 400 -> борг 100 грн
        assert orders.master_balance(s, seed["m1"])["balance"] == 10000


def test_dashboard(db, seed):
    m1, m2, d = seed["actors"]["m1"], seed["actors"]["m2"], seed["actors"]["director"]
    with db.session() as s:
        a = mk(s, seed)
        b = mk(s, seed, master="m2", service="haircut", phone="0509998877")
        orders.close_order(s, m1, a.id, amount=60000, payment_method="cash",
                           materials=[{"material_id": seed["gel"], "qty": 1}])
        ob = orders.close_order(s, m2, b.id, amount=50000, payment_method="card")
        expenses.create_expense(s, d, category="rent", amount=1000000, spent_on=DAY)
        expenses.create_expense(s, d, category="utilities", amount=200000, spent_on=DAY)
        expenses.create_expense(s, d, category="advertising", amount=100000, spent_on=DAY)
        e = expenses.create_expense(s, d, category="other", amount=5000, spent_on=DAY)
        expenses.create_expense(s, d, category="materials", amount=300000, spent_on=DAY)
        expenses.delete_expense(s, d, e.id, "дубль")
        orders.close_shift(s, m2, seed["m2"])
        orders.confirm_payout(s, d, master_id=seed["m2"], amount=20000, shift_id=ob.shift_id)
    with db.session() as s:
        f, t = analytics.period_range("month")
        dash = analytics.dashboard(s, d, f, t)
        assert dash["revenue"] == 110000
        assert dash["masters_share"] == 30000 + 20000
        assert dash["salon_share"] == 60000
        assert dash["materials"] == 3000
        assert dash["gross_profit"] == 110000 - 50000 - 3000
        assert dash["expenses"]["rent"] == 1000000
        assert dash["expenses"]["other"] == 0
        assert dash["expenses"]["materials_purchases"] == 300000
        assert dash["operating_expenses"] == 1300000
        assert dash["net_profit"] == 57000 - 1300000
        assert dash["payouts"] == 20000
        assert dash["salary_debt_total"] == 30000
        assert dash["by_payment"] == {"cash": 60000, "card": 50000}
        bm = {r["master_name"]: r for r in analytics.by_master(s, d, f, t)}
        assert bm["Олена"]["masters_share"] == 30000 and bm["Олена"]["balance"] == 30000
        assert bm["Ірина"]["paid"] == 20000 and bm["Ірина"]["balance"] == 0
        bs = {r["service_name"]: r for r in analytics.by_service(s, d, f, t)}
        assert bs["Манікюр"]["revenue"] == 60000 and bs["Стрижка"]["orders_count"] == 1
        assert analytics.by_day(s, d, f, t)[0]["revenue"] == 110000
        # майстер бачить лише свій заробіток
        my = analytics.my_earnings(s, m1, master_id=seed["m2"])
        assert my["day"]["earning"] == 30000 and my["month"]["orders_count"] == 1
        with pytest.raises(Forbidden):
            analytics.dashboard(s, m1, f, t)
        with pytest.raises(Forbidden):
            analytics.dashboard(s, seed["actors"]["admin"], f, t)


def test_period_ranges():
    from mds.services.analytics import period_range
    ref = date(2026, 11, 4)
    assert period_range("day", ref) == (ref, ref)
    assert period_range("week", ref) == (date(2026, 11, 2), date(2026, 11, 8))
    assert period_range("month", ref) == (date(2026, 11, 1), date(2026, 11, 30))
    assert period_range("year", ref) == (date(2026, 1, 1), date(2026, 12, 31))


def test_login_lockout(db, seed):
    from datetime import timedelta
    with db.session() as s:
        u, err = users.authenticate(s, "Olena", "Master123")
        assert u.id == seed["m1"] and err is None
    for _ in range(5):
        with db.session() as s:
            u, err = users.authenticate(s, "olena", "wrong")
            assert u is None and err
    with db.session() as s:
        u, err = users.authenticate(s, "olena", "Master123")
        assert u is None and "заблоковано" in err
    clock.set_now(clock.now() + timedelta(minutes=16))
    with db.session() as s:
        u, err = users.authenticate(s, "olena", "Master123")
        assert u is not None


def test_master_takes_earning_from_till_rest_paid_by_director(db, seed):
    """12 стрижок по 500 грн: 7 готівкою, 5 карткою. Заробіток 50% = 3000 грн.
    Готівки 3500 → майстер бере 3000 з каси, 500 лишає; директору доплачувати 0."""
    m1 = seed["actors"]["m1"]
    with db.session() as s:
        for i in range(12):
            a = bookings.create_appointment(s, seed["actors"]["admin"], master_id=seed["m1"], service_id=seed["haircut"],
                                            starts_at=datetime(2026, 11, 2, 8) + timedelta(hours=i), client_name=f"К{i}",
                                            client_phone=f"05000000{i:02d}")
            orders.close_order(s, m1, a.id, amount=50000, payment_method="cash" if i < 7 else "card")
        sh = orders.close_shift(s, m1, seed["m1"])
        c = sh.report["cash"]
        assert sh.report["master_earning"] == 300000
        assert c == {"from_clients": 350000, "noncash": 250000, "master_takes": 300000, "leave_in_till": 50000,
                     "director_pays": 0, "mode": "take_from_till"}
        assert sh.payout_due == 0 and sh.status == ShiftStatus.PAID
    with db.session() as s:
        assert orders.master_balance(s, seed["m1"]) == {"accrued": 300000, "paid": 300000, "charged": 0, "balance": 0}
        p = s.query(Payout).one()
        assert p.cash_shift_id == sh.id and p.shift_id is None and p.method == "cash"


def test_not_enough_cash_director_pays_difference(db, seed):
    """Клієнти платили переважно карткою: готівки 500, заробіток 1500 → з каси 500, у касі 0, директор доплачує 1000."""
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        for i, method in enumerate(["cash", "card", "transfer", "card", "card", "card"]):
            a = bookings.create_appointment(s, seed["actors"]["admin"], master_id=seed["m1"], service_id=seed["haircut"],
                                            starts_at=datetime(2026, 11, 2, 9 + i), client_name=f"К{i}", client_phone=f"05000001{i:02d}")
            orders.close_order(s, m1, a.id, amount=50000, payment_method=method)
        sh = orders.close_shift(s, m1, seed["m1"])
        c = sh.report["cash"]
        assert (c["from_clients"], c["master_takes"], c["leave_in_till"], c["director_pays"]) == (50000, 50000, 0, 100000)
        assert sh.payout_due == 100000 and sh.status == ShiftStatus.CLOSED
        orders.confirm_payout(s, d, master_id=seed["m1"], amount=100000, shift_id=sh.id, method="card")
    with db.session() as s:
        assert orders.master_balance(s, seed["m1"])["balance"] == 0
        pub = orders.shift_public(s, s.get(Shift, sh.id))
        assert pub["cash_payout"]["amount"] == 50000 and pub["payout"]["amount"] == 100000


def test_close_order_saves_client_full_name(db, seed):
    from mds.models import AuditLog, Client
    m1 = seed["actors"]["m1"]
    with db.session() as s:
        a = mk(s, seed)
        with pytest.raises(ValidationError):
            orders.close_order(s, m1, a.id, amount=50000, payment_method="cash", client_name=" А ")
    with db.session() as s:
        orders.close_order(s, m1, a.id, amount=50000, payment_method="cash",
                           client_name="  Анна   Коваленко ")
    with db.session() as s:
        c = s.get(Client, s.get(Appointment, a.id).client_id)
        assert c.name == "Анна Коваленко"
        log = s.query(AuditLog).filter_by(entity="client", action="rename").one()
        assert log.before == {"name": "Клієнт"} and log.after == {"name": "Анна Коваленко"}
