from __future__ import annotations

from sqlalchemy import delete, func, select

from . import models as m
from .db import Database
from .demo_fop1 import DEMO_PREFIX

_TABLES = [
    m.ExpenseFile, m.CardPayment, m.StockMove, m.OrderMaterial, m.LedgerEntry, m.Notification, m.CallbackRequest,
    m.PayrollPayment, m.PayrollAccrual, m.TaxPayment, m.TaxAccrual, m.TaxRate, m.EmployeeProfile,
    m.Order, m.Payout, m.MasterCharge, m.Shift, m.GeneratorRun, m.MeterReading, m.Expense,
    m.Appointment, m.Client, m.ScheduleDay, m.Material, m.AuditLog,
]

WIPE_MARK_KEY = "data_wipe_mark"


def wipe_data(db: Database, mark: str | None = None) -> dict:
    st: dict[str, int] = {}
    with db.session() as s:
        demo = list(s.scalars(select(m.User.id).where(m.User.username.like(DEMO_PREFIX + "%"))))
        for model in _TABLES:
            n = s.scalar(select(func.count()).select_from(model)) or 0
            if n:
                s.execute(delete(model))
            st[model.__tablename__] = int(n)
        if demo:
            s.execute(delete(m.master_services).where(m.master_services.c.master_id.in_(demo)))
            s.execute(delete(m.User).where(m.User.id.in_(demo)))
        st["demo_users"] = len(demo)
        if mark:
            row = s.get(m.Setting, WIPE_MARK_KEY)
            if row:
                row.value = mark
            else:
                s.add(m.Setting(key=WIPE_MARK_KEY, value=mark))
        s.add(m.AuditLog(actor="system", entity="database", entity_id=0, action="wipe",
                         after={k: v for k, v in st.items() if v}, reason="Очищення даних за рішенням директора"))
    return st


def already_wiped(db: Database, mark: str) -> bool:
    with db.session() as s:
        row = s.get(m.Setting, WIPE_MARK_KEY)
        return bool(row and row.value == mark)
