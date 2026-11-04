"""Очищення робочих даних: лишаються акаунти, послуги, налаштування; демо-акаунти й усі операції — видалено."""

from datetime import date

from sqlalchemy import func, select

from mds import models as m
from mds.demo_fop1 import FOP_LOGIN, seed_fop1
from mds.wipe import already_wiped, wipe_data

from .test_demo_fop1 import _real_order


def test_wipe_keeps_accounts_services_settings(db, seed):
    _real_order(db, seed)
    seed_fop1(db, date(2026, 1, 1), date(2026, 1, 31), year_target=60_000_000, future_days=3, clients=40)
    with db.session() as s:
        users_before = s.scalar(select(func.count(m.User.id)))
        services_before = s.scalar(select(func.count(m.Service.id)))
        settings_before = s.scalar(select(func.count()).select_from(m.Setting))
        assert s.scalar(select(func.count(m.Order.id))) > 1
    st = wipe_data(db, "2026-11-04")
    assert st["orders"] > 1 and st["demo_users"] == 1
    with db.session() as s:
        for model in (m.Order, m.Appointment, m.Client, m.Expense, m.LedgerEntry, m.Shift, m.Payout, m.Material,
                      m.StockMove, m.Notification, m.ScheduleDay, m.TaxRate):
            assert s.scalar(select(func.count()).select_from(model)) == 0, model.__tablename__
        assert s.scalar(select(m.User.id).where(m.User.username == FOP_LOGIN)) is None
        assert s.scalar(select(func.count(m.User.id))) == users_before - 1
        assert s.scalar(select(func.count(m.Service.id))) == services_before
        assert s.scalar(select(func.count()).select_from(m.Setting)) == settings_before + 1
        assert s.scalar(select(func.count(m.AuditLog.id))) == 1  # запис про саме очищення
    assert already_wiped(db, "2026-11-04") and not already_wiped(db, "інша")
