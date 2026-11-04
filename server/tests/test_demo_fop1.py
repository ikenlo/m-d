"""Демо «ФОП 1 група»: очищення лише демо-даних, дохід нижче ліміту 1 групи, без сповіщень, без дублів."""

from datetime import date, datetime

from sqlalchemy import func, select

from mds import clock
from mds.demo import seed_extra, seed_year
from mds.demo_fop1 import FOP_LOGIN, purge_demo, seed_fop1
from mds.models import Appointment, Client, Expense, LedgerEntry, Material, Notification, Order, Service, User
from mds.services import bookings, orders


def _real_order(db, seed):
    clock.set_now(datetime(2026, 11, 3, 8))
    with db.session() as s:
        aid = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=seed["haircut"],
                                          starts_at=datetime(2026, 11, 3, 10), client_name="Справжня Клієнтка",
                                          client_phone="0501112233").id
    clock.set_now(datetime(2026, 11, 3, 11))
    with db.session() as s:
        oid = orders.close_order(s, seed["actors"]["m1"], aid, amount=45000, payment_method="cash").id
    clock.set_now(None)
    return oid


def test_purge_removes_only_demo(db, seed):
    real = _real_order(db, seed)
    seed_year(db, date(2026, 1, 1), date(2026, 1, 31), clients=40)
    seed_extra(db, date(2026, 1, 1), date(2026, 1, 31), future_days=3)
    with db.session() as s:
        users_before = {u.username for u in s.scalars(select(User))}
        services = s.scalar(select(func.count(Service.id)))
    assert any(u.startswith("demo_") for u in users_before)
    st = purge_demo(db)
    assert st["users"] == 5 and st["orders"] > 20
    with db.session() as s:
        assert {u.username for u in s.scalars(select(User))} == {u for u in users_before if not u.startswith("demo_")}
        assert s.scalar(select(func.count(Service.id))) == services
        o = s.get(Order, real)
        assert o is not None and o.amount == 45000
        assert s.scalar(select(func.count(Order.id))) == 1 and s.scalar(select(func.count(Appointment.id))) == 1
        assert s.scalar(select(func.count(Expense.id)).where(Expense.description.like("%(демо)%"))) == 0
        assert [m.name for m in s.scalars(select(Material))] == ["Гель-лак"]  # справжній матеріал лишився
        assert s.scalar(select(func.count(Client.id)).where(Client.phone.like("+38067999%"))) == 0
        assert s.scalar(select(func.count(Client.id)).where(Client.phone == "+380501112233")) == 1
        # журнал: лишились лише проведення справжнього замовлення
        assert {e.ref_id for e in s.scalars(select(LedgerEntry).where(LedgerEntry.ref_type == "order"))} == {real}


def test_seed_fop1_income_under_limit_and_idempotent(db, seed):
    _real_order(db, seed)
    st = seed_fop1(db, date(2026, 1, 1), date(2026, 3, 31), year_target=110_000_000, future_days=5, clients=80)
    q1 = st["income_by_year"]["2026"]
    # квартал ≈ 1/4 річної цілі з сезонністю (0.85+0.9+1.1)/12 ≈ 26 тис. на 110 тис.… — у межах ±30 %
    expected = 1_100_000 * (0.85 + 0.9 + 1.1) / 12 / (sum([0.85, 0.9, 1.1, 1.0, 1.05, 0.95, 0.85, 0.85, 1.05, 1.1, 1.05, 1.25]) / 12)
    assert 0.7 * expected < q1 < 1.3 * expected
    with db.session() as s:
        fop = s.scalar(select(User).where(User.username == FOP_LOGIN))
        assert fop.share_bp == 0
        bad = s.scalar(select(func.count(Order.id)).where(Order.amount != Order.master_earning + Order.materials_cost + Order.salon_earning))
        assert bad == 0 and s.scalar(select(func.count(Notification.id))) == 0
        assert s.scalar(select(func.count(Material.id))) == 20  # 19 демо + справжній
    assert seed_fop1(db, date(2026, 1, 1), date(2026, 3, 31)) == {"skipped": True}
    assert clock._fixed is None
