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
    from .utilities import charges_total, generator_totals

    gross = t["revenue"] - t["masters_share"] - t["materials"]
    operating = exp[ExpenseCategory.RENT] + exp[ExpenseCategory.UTILITIES] + exp[ExpenseCategory.ADVERTISING] + exp[ExpenseCategory.OTHER]
    generator = generator_totals(s, start, end)["total_cost"]  # пальне + амортизація за мотогодинами
    charged = charges_total(s, d_from=d_from, d_to=d_to)  # компенсація майстрами (комунальні/генератор)
    # загальний борг по зарплаті на сьогодні (за весь час)
    total_accrued = int(s.scalar(select(func.coalesce(func.sum(Order.master_earning), 0))) or 0)
    total_paid = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0))) or 0)
    total_charged = charges_total(s)
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
            "generator": generator,
            "fuel_purchases": exp[ExpenseCategory.FUEL],
            "master_charges": charged,
        },
        "operating_expenses": operating + generator,
        "gross_profit": gross,
        "net_profit": gross - operating - generator + charged,
        "payouts": paid,
        "salary_debt_total": total_accrued - total_paid - total_charged,
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
        from .utilities import charges_total

        out[p] = {"orders_count": t["orders_count"], "revenue": t["revenue"], "earning": t["masters_share"],
                  "charges": charges_total(s, master_id=master_id, d_from=a, d_to=b)}
    out["balance"] = master_balance(s, master_id)
    return out


# ------------------------------------------------------------------ аналітика і прогноз
def _change_bp(cur: int, prev: int) -> int | None:
    """Зміна у bp (1000 = +10%). None — немає бази для порівняння."""
    if not prev:
        return None
    return (cur - prev) * 10000 // abs(prev)


def _previous_range(d_from: date, d_to: date) -> tuple[date, date]:
    """Попередній період тієї ж довжини; для календарного місяця/року — попередній місяць/рік."""
    if d_from.day == 1 and d_to == d_from.replace(day=calendar.monthrange(d_from.year, d_from.month)[1]):
        p_to = d_from - timedelta(days=1)
        return p_to.replace(day=1), p_to
    if (d_from.month, d_from.day, d_to.month, d_to.day) == (1, 1, 12, 31) and d_from.year == d_to.year:
        return date(d_from.year - 1, 1, 1), date(d_from.year - 1, 12, 31)
    days = (d_to - d_from).days + 1
    p_to = d_from - timedelta(days=1)
    return p_to - timedelta(days=days - 1), p_to


def forecast_month(s: Session, actor: Actor, ref: date | None = None) -> dict:
    """Прогноз виручки поточного місяця.

    - факт: закриті замовлення з 1-го числа до зараз;
    - записано наперед: планова ціна майбутніх записів цього місяця (очікують/підтверджені);
    - за темпом: факт ÷ минулий час × тривалість місяця;
    - прогноз = більше з «за темпом» і «факт + записано» (записи — мінімум, який уже є).
    Частка салону і прибуток — за структурою фактичних замовлень місяця.
    """
    actor.require(Role.DIRECTOR)
    now = clock.now()
    today = ref or now.date()
    m_from, m_to = period_range("month", today)
    start, end = _bounds(m_from, m_to)
    cut = min(max(now, start), end)
    fact = _order_totals(s, start, cut)
    booked, booked_n = s.execute(
        select(func.coalesce(func.sum(Appointment.price), 0), func.count(Appointment.id)).where(
            Appointment.starts_at >= cut, Appointment.starts_at < end,
            Appointment.status.in_((ApptStatus.PENDING, ApptStatus.CONFIRMED, ApptStatus.IN_PROGRESS)))
    ).one()
    booked, booked_n = int(booked), int(booked_n)
    total_sec = int((end - start).total_seconds())
    elapsed_sec = max(int((cut - start).total_seconds()), 86400)  # щонайменше доба — без перебільшення зранку 1-го числа
    run_rate = fact["revenue"] * total_sec // elapsed_sec if fact["revenue"] else 0
    forecast = max(run_rate, fact["revenue"] + booked)
    rev = fact["revenue"]
    gross_ratio_num = rev - fact["masters_share"] - fact["materials"]
    forecast_gross = forecast * gross_ratio_num // rev if rev else 0
    operating = int(s.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(
        Expense.spent_on >= m_from, Expense.spent_on <= m_to, Expense.is_deleted.is_(False),
        Expense.category.not_in(ExpenseCategory.CASH_FLOW_ONLY))) or 0)
    from .utilities import charges_total, generator_totals

    operating += generator_totals(s, start, cut)["total_cost"] - charges_total(s, d_from=m_from, d_to=m_to)
    days_total = (m_to - m_from).days + 1
    days_elapsed = max((today - m_from).days + 1, 1)
    return {
        "month_from": m_from.isoformat(), "month_to": m_to.isoformat(),
        "days_total": days_total, "days_elapsed": min(days_elapsed, days_total),
        "actual": rev, "actual_orders": fact["orders_count"],
        "booked": booked, "booked_count": booked_n,
        "run_rate": run_rate, "forecast": forecast,
        "daily_average": rev // min(days_elapsed, days_total) if rev else 0,
        "forecast_gross": forecast_gross, "operating_expenses": operating,
        "forecast_net": forecast_gross - operating,
    }


def insights(s: Session, actor: Actor, d_from: date, d_to: date) -> dict:
    """Аналітика періоду: порівняння з попереднім, середній чек, клієнти, дні тижня, прогноз місяця."""
    actor.require(Role.DIRECTOR)
    cur = dashboard(s, actor, d_from, d_to)
    p_from, p_to = _previous_range(d_from, d_to)
    prev = dashboard(s, actor, p_from, p_to)
    keys = ("revenue", "orders_count", "average_check", "clients_count", "salon_share", "net_profit")
    compare = {k: {"current": cur[k], "previous": prev[k], "change_bp": _change_bp(cur[k], prev[k])} for k in keys}

    start, end = _bounds(d_from, d_to)
    rows = s.execute(select(Order.client_id, Order.closed_at, Order.amount, Order.master_id)
                     .where(Order.closed_at >= start, Order.closed_at < end)).all()
    client_ids = {r.client_id for r in rows}
    first_visit = dict(s.execute(select(Order.client_id, func.min(Order.closed_at))
                                 .where(Order.client_id.in_(client_ids or {0})).group_by(Order.client_id)).all())
    new_clients = sum(1 for c in client_ids if first_visit.get(c) and first_visit[c] >= start)
    visits: dict[int, int] = {}
    for r in rows:
        visits[r.client_id] = visits.get(r.client_id, 0) + 1

    wd = [{"weekday": i, "revenue": 0, "orders": 0} for i in range(7)]
    for r in rows:
        w = wd[r.closed_at.weekday()]
        w["revenue"] += r.amount
        w["orders"] += 1
    # скільки разів кожен день тижня був у періоді — для середньої виручки за такий день
    n_days = (d_to - d_from).days + 1
    for i, w in enumerate(wd):
        occurrences = sum(1 for k in range(n_days) if (d_from + timedelta(days=k)).weekday() == i)
        w["average"] = w["revenue"] // occurrences if occurrences else 0

    masters = []
    for m in by_master(s, actor, d_from, d_to):
        masters.append({"master_id": m["master_id"], "master_name": m["master_name"], "orders": m["orders_count"],
                        "revenue": m["revenue"],
                        "average_check": m["revenue"] // m["orders_count"] if m["orders_count"] else 0})
    masters.sort(key=lambda x: -x["revenue"])
    return {
        "from": d_from.isoformat(), "to": d_to.isoformat(),
        "previous": {"from": p_from.isoformat(), "to": p_to.isoformat()},
        "compare": compare,
        "clients": {"total": len(client_ids), "new": new_clients, "returning": len(client_ids) - new_clients,
                    "repeat_in_period": sum(1 for v in visits.values() if v > 1)},
        "weekdays": wd,
        "masters": masters,
        "forecast": forecast_month(s, actor),
    }
