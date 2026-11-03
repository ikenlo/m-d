"""Аналітика: фінансова панель директора, розрізи по майстрах і послугах,
заробіток майстра.

Розрізняємо (див. docs/FINANCE.md):
  * Виручка          — сума фактичних оплат клієнтів (orders.amount).
  * Нараховано майстрам — їхня частка з закритих замовлень.
  * Виплачено майстрам  — фактичні підтверджені виплати (payouts).
  * Валовий прибуток — виручка − нараховано майстрам − матеріали (спожиті).
  * Чистий прибуток  — валовий − оренда − комунальні − реклама − інші витрати.
  Закупівля матеріалів (expense.category=materials) показується окремо як грошовий
  потік і НЕ віднімається вдруге від прибутку, бо собівартість уже врахована
  в замовленнях.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import ValidationError
from ..models import Appointment, ApptStatus, Expense, ExpenseCategory, Order, Payout, Role, Service, User
from .common import Actor
from .orders import master_balance


def period_range(period: str, ref: date | None = None) -> tuple[date, date]:
    """day | week | month | year -> (з, по) включно."""
    ref = ref or clock.now().date()
    if period == "day":
        return ref, ref
    if period == "week":
        start = ref - timedelta(days=ref.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        return ref.replace(day=1), ref.replace(day=calendar.monthrange(ref.year, ref.month)[1])
    if period == "year":
        return date(ref.year, 1, 1), date(ref.year, 12, 31)
    raise ValidationError("Період: day, week, month або year")


def _bounds(d_from: date, d_to: date) -> tuple[datetime, datetime]:
    if d_to < d_from:
        raise ValidationError("Дата 'по' раніша за 'з'")
    return datetime.combine(d_from, time.min), datetime.combine(d_to + timedelta(days=1), time.min)


def _order_totals(s: Session, start: datetime, end: datetime, master_id: int | None = None) -> dict:
    q = select(
        func.count(Order.id),
        func.coalesce(func.sum(Order.amount), 0),
        func.coalesce(func.sum(Order.master_earning), 0),
        func.coalesce(func.sum(Order.materials_cost), 0),
        func.coalesce(func.sum(Order.salon_earning), 0),
        func.count(func.distinct(Order.client_id)),
    ).where(Order.closed_at >= start, Order.closed_at < end)
    if master_id:
        q = q.where(Order.master_id == master_id)
    n, revenue, master, materials, salon, clients = s.execute(q).one()
    return {
        "orders_count": int(n),
        "clients_count": int(clients),
        "revenue": int(revenue),
        "masters_share": int(master),
        "materials": int(materials),
        "salon_net_from_orders": int(salon),
    }


def dashboard(s: Session, actor: Actor, d_from: date, d_to: date) -> dict:
    actor.require(Role.DIRECTOR)
    start, end = _bounds(d_from, d_to)
    t = _order_totals(s, start, end)

    exp = {c: 0 for c in ExpenseCategory.ALL}
    for cat, total in s.execute(
        select(Expense.category, func.sum(Expense.amount))
        .where(Expense.spent_on >= d_from, Expense.spent_on <= d_to, Expense.is_deleted.is_(False))
        .group_by(Expense.category)
    ):
        exp[cat] = int(total)

    paid = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(Payout.paid_at >= start, Payout.paid_at < end)) or 0)
    by_payment = {
        m: int(v)
        for m, v in s.execute(
            select(Order.payment_method, func.sum(Order.amount))
            .where(Order.closed_at >= start, Order.closed_at < end)
            .group_by(Order.payment_method)
        )
    }
    cancellations = int(
        s.scalar(
            select(func.count(Appointment.id)).where(
                Appointment.starts_at >= start, Appointment.starts_at < end,
                Appointment.status.in_((ApptStatus.CANCELLED, ApptStatus.NO_SHOW)),
            )
        ) or 0
    )
    gross = t["revenue"] - t["masters_share"] - t["materials"]
    operating = exp[ExpenseCategory.RENT] + exp[ExpenseCategory.UTILITIES] + exp[ExpenseCategory.ADVERTISING] + exp[ExpenseCategory.OTHER]
    # загальний борг по зарплаті на сьогодні (за весь час)
    total_accrued = int(s.scalar(select(func.coalesce(func.sum(Order.master_earning), 0))) or 0)
    total_paid = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0))) or 0)
    return {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        **t,
        "salon_share": t["revenue"] - t["masters_share"],
        "expenses": {
            "rent": exp[ExpenseCategory.RENT],
            "utilities": exp[ExpenseCategory.UTILITIES],
            "advertising": exp[ExpenseCategory.ADVERTISING],
            "other": exp[ExpenseCategory.OTHER],
            "materials_purchases": exp[ExpenseCategory.MATERIALS],
        },
        "operating_expenses": operating,
        "gross_profit": gross,
        "net_profit": gross - operating,
        "payouts": paid,
        "salary_debt_total": total_accrued - total_paid,
        "by_payment": by_payment,
        "cancellations": cancellations,
        "average_check": t["revenue"] // t["orders_count"] if t["orders_count"] else 0,
    }


def by_master(s: Session, actor: Actor, d_from: date, d_to: date) -> list[dict]:
    actor.require(Role.DIRECTOR)
    start, end = _bounds(d_from, d_to)
    out = []
    for m in s.scalars(select(User).where(User.role == Role.MASTER).order_by(User.full_name)):
        t = _order_totals(s, start, end, m.id)
        paid = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(
            Payout.master_id == m.id, Payout.paid_at >= start, Payout.paid_at < end)) or 0)
        if t["orders_count"] == 0 and paid == 0 and not m.is_active:
            continue
        out.append({"master_id": m.id, "master_name": m.full_name, **t, "paid": paid,
                    "balance": master_balance(s, m.id)["balance"]})
    return out


def by_service(s: Session, actor: Actor, d_from: date, d_to: date) -> list[dict]:
    actor.require(Role.DIRECTOR)
    start, end = _bounds(d_from, d_to)
    rows = s.execute(
        select(
            Service.id, Service.name,
            func.count(Order.id), func.sum(Order.amount), func.sum(Order.master_earning),
            func.sum(Order.materials_cost), func.sum(Order.salon_earning),
        )
        .join(Order, Order.service_id == Service.id)
        .where(Order.closed_at >= start, Order.closed_at < end)
        .group_by(Service.id, Service.name)
        .order_by(func.sum(Order.amount).desc())
    )
    return [
        {"service_id": sid, "service_name": name, "orders_count": int(n), "revenue": int(rev),
         "masters_share": int(ms), "materials": int(mat), "salon_net_from_orders": int(sal)}
        for sid, name, n, rev, ms, mat, sal in rows
    ]


def by_day(s: Session, actor: Actor, d_from: date, d_to: date) -> list[dict]:
    """Виручка по днях (для графіка)."""
    actor.require(Role.DIRECTOR)
    start, end = _bounds(d_from, d_to)
    day = func.date(Order.closed_at)
    rows = s.execute(
        select(day, func.sum(Order.amount), func.sum(Order.master_earning), func.sum(Order.salon_earning))
        .where(Order.closed_at >= start, Order.closed_at < end)
        .group_by(day).order_by(day)
    )
    return [{"date": str(d), "revenue": int(r), "masters_share": int(m), "salon_net_from_orders": int(sn)} for d, r, m, sn in rows]


def my_earnings(s: Session, actor: Actor, master_id: int | None = None) -> dict:
    """Заробіток майстра: сьогодні, тиждень, місяць + баланс до виплати."""
    if actor.is_master:
        master_id = actor.user_id
    else:
        actor.require(Role.DIRECTOR)
    if not master_id:
        raise ValidationError("Вкажіть майстра")
    out = {}
    for p in ("day", "week", "month"):
        a, b = period_range(p)
        t = _order_totals(s, *_bounds(a, b), master_id=master_id)
        out[p] = {"orders_count": t["orders_count"], "revenue": t["revenue"], "earning": t["masters_share"]}
    out["balance"] = master_balance(s, master_id)
    return out
