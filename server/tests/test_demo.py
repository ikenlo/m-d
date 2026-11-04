"""Демо-рік: лише додає дані, нічого не видаляє, без сповіщень, без дублів, фінансові інваріанти виконуються."""

from datetime import date, datetime

from sqlalchemy import func, select

from mds import clock
from mds.demo import seed_year
from mds.models import Expense, Notification, Order, User
from mds.services import bookings, orders


def test_demo_year_adds_only_and_is_idempotent(db, seed):
    # наявне замовлення салону — має лишитися без змін
    clock.set_now(datetime(2026, 11, 3, 8))
    with db.session() as s:
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 3, 10), client_name="Справжній", client_phone="0501112233")
        aid = a.id
    clock.set_now(datetime(2026, 11, 3, 11))
    with db.session() as s:
        real = orders.close_order(s, seed["actors"]["m1"], aid, amount=45000, payment_method="cash")
        real_id, real_snapshot = real.id, (real.amount, real.master_earning, real.salon_earning, real.closed_at)
        notif_before = s.scalar(select(func.count(Notification.id)))
    clock.set_now(None)

    st = seed_year(db, date(2026, 1, 1), date(2026, 1, 31), clients=60)
    assert st["orders"] > 50 and st["shifts"] > 10 and st["expenses"] == 5

    with db.session() as s:
        r = s.get(Order, real_id)
        assert (r.amount, r.master_earning, r.salon_earning, r.closed_at) == real_snapshot
        assert s.scalar(select(func.count(Notification.id))) == notif_before  # жодних сповіщень
        demo = list(s.scalars(select(User).where(User.username.like("demo_%"))))
        assert len(demo) == 4 and all("(демо)" in u.full_name for u in demo)
        bad = s.scalar(select(func.count(Order.id)).where(Order.amount != Order.master_earning + Order.materials_cost + Order.salon_earning))
        assert bad == 0
        demo_orders = list(s.scalars(select(Order).where(Order.master_id.in_([u.id for u in demo]))))
        assert all(date(2026, 1, 1) <= o.closed_at.date() <= date(2026, 1, 31) for o in demo_orders)
        assert all("(демо)" in e.description for e in s.scalars(select(Expense)))
        n_orders = s.scalar(select(func.count(Order.id)))

    assert seed_year(db, date(2026, 1, 1), date(2026, 1, 31)) == {"skipped": True}
    with db.session() as s:
        assert s.scalar(select(func.count(Order.id))) == n_orders
    assert clock._fixed is None  # годинник повернуто


def test_demo_extra_fills_stock_clients_future(db, seed):
    from mds.demo import seed_extra
    from mds.models import Appointment, Client, Material, MeterReading, StockMove

    seed_year(db, date(2026, 1, 1), date(2026, 2, 28), clients=60)
    st = seed_extra(db, date(2026, 1, 1), date(2026, 2, 28), future_days=7)
    assert st["materials"] == 19 and st["stock_moves"] > 100 and st["readings"] == 2
    assert st["client_notes"] > 10 and st["future"] > 5 and st["corrections"] == 10
    with db.session() as s:
        assert all(m.stock_milli >= 0 for m in s.scalars(select(Material)))
        assert s.scalar(select(func.count(StockMove.id))) > 100
        assert s.scalar(select(func.count(MeterReading.id))) == 2
        assert s.scalar(select(func.count(Client.id)).where(Client.notes.like("%(демо)%"))) > 10
        assert s.scalar(select(func.count(Appointment.id)).where(Appointment.starts_at > datetime.now())) > 5
        bad = s.scalar(select(func.count(Order.id)).where(Order.amount != Order.master_earning + Order.materials_cost + Order.salon_earning))
        assert bad == 0
        assert s.scalar(select(func.count(Notification.id))) == 0
    assert seed_extra(db) == {"skipped": True}
    assert clock._fixed is None
