from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import ValidationError
from ..models import Appointment, ApptStatus, Expense, ExpenseCategory, Order, OrderStatus, Payout, Role, Service, User
from .common import Actor
from .orders import master_balance


def period_range(period: str, ref: date | None = None) -> tuple[date, date]:
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


def _order_totals(s: Session, start: datetime, end: datetime, master_id: int | None = None,
                  statuses: tuple[str, ...] = (OrderStatus.COMPLETED,)) -> dict:
    q = select(
        func.count(Order.id),
        func.coalesce(func.sum(Order.amount), 0),
        func.coalesce(func.sum(Order.master_earning), 0),
        func.coalesce(func.sum(Order.materials_cost), 0),
        func.coalesce(func.sum(Order.salon_earning), 0),
        func.count(func.distinct(Order.client_id)),
        func.coalesce(func.sum(Order.price_snapshot), 0),
    ).where(Order.closed_at >= start, Order.closed_at < end, Order.status.in_(statuses))
    if master_id:
        q = q.where(Order.master_id == master_id)
    n, revenue, master, materials, salon, clients, price = s.execute(q).one()
    return {
        "orders_count": int(n),
        "clients_count": int(clients),
        "revenue": int(revenue),
        "price_total": int(price),
        "discounts": int(price) - int(revenue),
        "masters_share": int(master),
        "materials": int(materials),
        "salon_net_from_orders": int(salon),
    }


PROFIT_SCALE = ((2500, "excellent"), (1800, "good"), (1400, "fair"), (800, "low"))
REPORT_KEYS = ("revenue", "split", "gross", "expenses", "net", "profitability", "masters", "unpaid",
               "by_master", "by_service", "by_day", "insights", "recommendations", "pie", "average_check")


def profitability_level(bp: int | None) -> str | None:
    if bp is None:
        return None
    for limit, name in PROFIT_SCALE:
        if bp >= limit:
            return name
    return "critical"


def _products_totals(s: Session, start: datetime, end: datetime, master_id: int | None = None) -> dict:
    from ..models import OrderProduct

    q = (select(func.coalesce(func.sum(OrderProduct.sale_price_snapshot_cents * OrderProduct.qty), 0),
                func.coalesce(func.sum(OrderProduct.cost_price_snapshot_cents * OrderProduct.qty), 0),
                func.coalesce(func.sum(OrderProduct.master_bonus_cents), 0), func.count(OrderProduct.id))
         .join(Order, Order.id == OrderProduct.order_id)
         .where(Order.closed_at >= start, Order.closed_at < end, Order.status == OrderStatus.COMPLETED,
                OrderProduct.refunded.is_(False)))
    if master_id:
        q = q.where(Order.master_id == master_id)
    rev, cost, bonus, n = s.execute(q).one()
    return {"revenue": int(rev), "cost": int(cost), "bonus": int(bonus), "margin": int(rev) - int(cost) - int(bonus),
            "lines": int(n)}


def _materials_by_category(s: Session, start: datetime, end: datetime) -> list[dict]:
    from ..models import Material, OrderMaterial

    rows = s.execute(select(func.coalesce(Material.category, "Без категорії"), func.sum(OrderMaterial.total))
                     .join(Order, Order.id == OrderMaterial.order_id)
                     .outerjoin(Material, Material.id == OrderMaterial.material_id)
                     .where(Order.closed_at >= start, Order.closed_at < end,
                            Order.status.in_((OrderStatus.COMPLETED, OrderStatus.REFUNDED)))
                     .group_by(func.coalesce(Material.category, "Без категорії")))
    return sorted(({"category": c or "Без категорії", "amount": int(v or 0)} for c, v in rows), key=lambda x: -x["amount"])


def _purchase_benefit(s: Session, start: datetime, end: datetime) -> int:
    """Вигода від кращих закупівель: (середня ціна до приходу − ціна приходу) × кількість, за прихід у періоді."""
    from ..models import AuditLog, Material

    total = 0
    for a in s.scalars(select(AuditLog).where(AuditLog.entity == "material", AuditLog.action == "receipt",
                                              AuditLog.ts >= start, AuditLog.ts < end)):
        before = (a.before or {}).get("unit_cost")
        after = a.after or {}
        m = s.get(Material, a.entity_id)
        if before is None or m is None or not after.get("qty"):
            continue
        qty_milli = round(float(after["qty"]) * 1000)
        expected = before * qty_milli // (max(m.pack_size, 1) * 1000)
        total += expected - int(after.get("cost") or 0)
    return total


def _taxes(s: Session, d_from: date, d_to: date) -> int:
    from ..models import TaxAccrual

    return int(s.scalar(select(func.coalesce(func.sum(TaxAccrual.amount), 0)).where(
        TaxAccrual.period_start >= d_from, TaxAccrual.period_start <= d_to)) or 0)


def unpaid_orders(s: Session, now: datetime | None = None) -> dict:
    now = now or clock.now()
    rows = list(s.scalars(select(Order).where(Order.status == OrderStatus.UNPAID).order_by(Order.closed_at)))
    old = [o for o in rows if o.closed_at < now - timedelta(days=3)]
    return {"count": len(rows), "amount": sum(o.amount for o in rows), "overdue_count": len(old),
            "overdue_amount": sum(o.amount for o in old),
            "items": [{"id": o.id, "amount": o.amount, "closed_at": o.closed_at.isoformat(), "master_id": o.master_id,
                       "days": (now - o.closed_at).days, "overdue": o in old} for o in rows[:50]]}


def dashboard(s: Session, actor: Actor, d_from: date, d_to: date) -> dict:
    from ..models import PayoutTag
    from .common import get_setting
    from .products import unpaid_bonus
    from .utilities import generator_totals

    actor.require(Role.DIRECTOR)
    start, end = _bounds(d_from, d_to)
    t = _order_totals(s, start, end)
    refunded = _order_totals(s, start, end, statuses=(OrderStatus.REFUNDED,))
    prod = _products_totals(s, start, end)

    exp = {c: 0 for c in ExpenseCategory.ALL}
    for cat, total in s.execute(
        select(Expense.category, func.sum(Expense.amount))
        .where(Expense.spent_on >= d_from, Expense.spent_on <= d_to, Expense.is_deleted.is_(False))
        .group_by(Expense.category)
    ):
        exp[cat] = int(total)
    not_bonus = (Payout.tag.is_(None)) | (Payout.tag != PayoutTag.PRODUCT_BONUS)
    paid = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(
        Payout.paid_at >= start, Payout.paid_at < end, not_bonus)) or 0)
    bonus_paid = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(
        Payout.paid_at >= start, Payout.paid_at < end, Payout.tag == PayoutTag.PRODUCT_BONUS)) or 0)
    by_payment: dict[str, int] = {}
    for m, v in s.execute(select(Order.payment_method, func.sum(Order.amount))
                          .where(Order.closed_at >= start, Order.closed_at < end, Order.status == OrderStatus.COMPLETED)
                          .group_by(Order.payment_method)):
        by_payment[m] = int(v)
    from ..models import OrderProduct

    for m, v in s.execute(select(Order.payment_method, func.sum(OrderProduct.sale_price_snapshot_cents * OrderProduct.qty))
                          .join(Order, Order.id == OrderProduct.order_id)
                          .where(Order.closed_at >= start, Order.closed_at < end, Order.status == OrderStatus.COMPLETED,
                                 OrderProduct.refunded.is_(False)).group_by(Order.payment_method)):
        by_payment[m] = by_payment.get(m, 0) + int(v or 0)
    cancellations = int(s.scalar(select(func.count(Appointment.id)).where(
        Appointment.starts_at >= start, Appointment.starts_at < end,
        Appointment.status.in_((ApptStatus.CANCELLED, ApptStatus.NO_SHOW)))) or 0)

    services_gross = t["revenue"] - t["masters_share"] - t["materials"]
    refund_losses = refunded["materials"]
    gross = services_gross + prod["margin"] - refund_losses
    other = exp[ExpenseCategory.OTHER] + exp[ExpenseCategory.EQUIPMENT] + exp[ExpenseCategory.HOUSEHOLD]
    taxes = _taxes(s, d_from, d_to)
    generator = generator_totals(s, start, end)["total_cost"]
    operating = exp[ExpenseCategory.RENT] + exp[ExpenseCategory.UTILITIES] + exp[ExpenseCategory.ADVERTISING] + taxes + other
    net = gross - operating - generator
    total_accrued = int(s.scalar(select(func.coalesce(func.sum(Order.master_earning), 0)).where(
        Order.status.in_(OrderStatus.ACCRUING))) or 0)
    total_paid = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(not_bonus)) or 0)
    bonus_accrued = sum(unpaid_bonus(s, m) for m in s.scalars(select(User.id).where(User.role == Role.MASTER)))
    real_revenue = t["revenue"] + prod["revenue"]
    prof_bp = net * 10000 // real_revenue if real_revenue else None
    return {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        **t,
        "real_revenue": real_revenue,
        "salon_share": t["revenue"] - t["masters_share"],
        "products": prod,
        "expenses": {
            "rent": exp[ExpenseCategory.RENT],
            "utilities": exp[ExpenseCategory.UTILITIES],
            "advertising": exp[ExpenseCategory.ADVERTISING],
            "taxes": taxes,
            "generator": generator,
            "other": other,
            "other_parts": {"other": exp[ExpenseCategory.OTHER], "equipment": exp[ExpenseCategory.EQUIPMENT],
                            "household": exp[ExpenseCategory.HOUSEHOLD]},
            "materials_purchases": exp[ExpenseCategory.MATERIALS],
            "fuel_purchases": exp[ExpenseCategory.FUEL],
        },
        "operating_expenses": operating + generator,
        "gross_profit": gross,
        "gross_details": {"services": services_gross, "products": prod["margin"], "refund_losses": refund_losses,
                          "purchase_benefit": _purchase_benefit(s, start, end),
                          "materials_by_category": _materials_by_category(s, start, end)},
        "net_profit": net,
        "profitability_bp": prof_bp,
        "profitability_level": profitability_level(prof_bp),
        "payouts": paid,
        "product_bonus_paid": bonus_paid,
        "salary_debt_total": total_accrued - total_paid,
        "owed_to_masters": total_accrued - total_paid,
        "product_bonus_accrued": bonus_accrued,
        "unpaid_orders": unpaid_orders(s),
        "by_payment": by_payment,
        "cancellations": cancellations,
        "average_check": t["revenue"] // t["orders_count"] if t["orders_count"] else 0,
        "hidden_reports": list(get_setting(s, "reports_hidden") or []),
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
        .where(Order.closed_at >= start, Order.closed_at < end, Order.status == OrderStatus.COMPLETED)
        .group_by(Service.id, Service.name)
        .order_by(func.sum(Order.amount).desc())
    )
    return [
        {"service_id": sid, "service_name": name, "orders_count": int(n), "revenue": int(rev),
         "masters_share": int(ms), "materials": int(mat), "salon_net_from_orders": int(sal)}
        for sid, name, n, rev, ms, mat, sal in rows
    ]


def by_day(s: Session, actor: Actor, d_from: date, d_to: date) -> list[dict]:
    actor.require(Role.DIRECTOR)
    start, end = _bounds(d_from, d_to)
    day = func.date(Order.closed_at)
    rows = s.execute(
        select(day, func.sum(Order.amount), func.sum(Order.master_earning), func.sum(Order.salon_earning))
        .where(Order.closed_at >= start, Order.closed_at < end, Order.status == OrderStatus.COMPLETED)
        .group_by(day).order_by(day)
    )
    return [{"date": str(d), "revenue": int(r), "masters_share": int(m), "salon_net_from_orders": int(sn)} for d, r, m, sn in rows]


def my_earnings(s: Session, actor: Actor, master_id: int | None = None) -> dict:
    from ..models import Payout, PayoutTag
    from .orders import current_shift, shift_report
    from .products import unpaid_bonus

    if actor.is_master:
        master_id = actor.user_id
    else:
        actor.require(Role.DIRECTOR)
    if not master_id:
        raise ValidationError("Вкажіть майстра")
    out: dict = {}
    for p in ("day", "week", "month"):
        a, b = period_range(p)
        t = _order_totals(s, *_bounds(a, b), master_id=master_id, statuses=OrderStatus.ACCRUING)
        out[p] = {"orders_count": t["orders_count"], "earning": t["masters_share"]}
    bal = master_balance(s, master_id)
    out["balance"] = bal
    sh = current_shift(s, master_id)
    if sh:
        r = shift_report(s, sh)
        out["current_shift"] = {"id": sh.id, "orders_count": r["services_count"], "earnings": r["master_earning"],
                                "to_pay": r["payout_due"], "takes_from_till": r["cash"]["master_takes"]}
    else:
        out["current_shift"] = None
    m_from, m_to = period_range("month")
    ms, me = _bounds(m_from, m_to)
    not_bonus = (Payout.tag.is_(None)) | (Payout.tag != PayoutTag.PRODUCT_BONUS)
    month_paid = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(
        Payout.master_id == master_id, Payout.paid_at >= ms, Payout.paid_at < me, not_bonus)) or 0)
    out["month_summary"] = {"earnings": out["month"]["earning"], "paid": month_paid, "debt": bal["balance"],
                            "product_bonus": unpaid_bonus(s, master_id)}
    today = clock.now().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    day = func.date(Order.closed_at)
    per_day = {str(k)[:10]: v for k, v in s.execute(select(day, func.sum(Order.master_earning)).where(
        Order.master_id == master_id, Order.status.in_(OrderStatus.ACCRUING),
        Order.closed_at >= datetime.combine(days[0], time.min)).group_by(day)).all()}
    out["last_7_days"] = [{"date": d.isoformat(), "earning": int(per_day.get(d.isoformat(), 0) or 0)} for d in days]
    top = s.execute(select(Service.name, func.count(Order.id), func.sum(Order.master_earning))
                    .join(Order, Order.service_id == Service.id)
                    .where(Order.master_id == master_id, Order.status.in_(OrderStatus.ACCRUING),
                           Order.closed_at >= ms, Order.closed_at < me)
                    .group_by(Service.name).order_by(func.count(Order.id).desc(), Service.name).limit(3)).all()
    out["top_services"] = [{"service": n, "count": int(c), "earnings": int(e or 0)} for n, c, e in top]
    hist = []
    for o in s.scalars(select(Order).where(Order.master_id == master_id).order_by(Order.closed_at.desc()).limit(20)):
        hist.append({"date": o.closed_at.isoformat(), "type": "order" if o.status in OrderStatus.ACCRUING else o.status,
                     "title": o.service.name if o.service else "Продаж товарів", "amount": o.master_earning, "id": o.id})
    for pmt in s.scalars(select(Payout).where(Payout.master_id == master_id).order_by(Payout.paid_at.desc()).limit(20)):
        hist.append({"date": pmt.paid_at.isoformat(), "type": "payout", "title": pmt.tag or ("з каси" if pmt.cash_shift_id else "виплата"),
                     "amount": -pmt.amount, "id": pmt.id})
    out["history"] = sorted(hist, key=lambda x: x["date"], reverse=True)[:20]
    return out


def logic_stats(s: Session, actor: Actor, ref: date | None = None) -> dict:
    """Середні цифри поточного місяця для схеми «Логіка системи» (канонічна формула)."""
    from .finance import compute_split

    actor.require(Role.DIRECTOR)
    a, b = period_range("month", ref)
    t = _order_totals(s, *_bounds(a, b))
    n = t["orders_count"]
    if not n:
        return {"month": a.isoformat(), "orders": 0, "avg_price": 0, "avg_paid": 0, "avg_materials": 0,
                "avg_master": 0, "avg_salon": 0, "avg_discount": 0, "salon_negative": False}
    p, paid, m = t["price_total"] // n, t["revenue"] // n, t["materials"] // n
    sp = compute_split(p, paid, m, 5000)
    return {"month": a.isoformat(), "orders": n, "avg_price": p, "avg_paid": paid, "avg_materials": m,
            "avg_master": sp.master, "avg_salon": sp.salon, "avg_discount": sp.discount, "salon_negative": sp.salon < 0}


def _change_bp(cur: int, prev: int) -> int | None:
    if not prev:
        return None
    return (cur - prev) * 10000 // abs(prev)


def _previous_range(d_from: date, d_to: date) -> tuple[date, date]:
    if d_from.day == 1 and d_to == d_from.replace(day=calendar.monthrange(d_from.year, d_from.month)[1]):
        p_to = d_from - timedelta(days=1)
        return p_to.replace(day=1), p_to
    if (d_from.month, d_from.day, d_to.month, d_to.day) == (1, 1, 12, 31) and d_from.year == d_to.year:
        return date(d_from.year - 1, 1, 1), date(d_from.year - 1, 12, 31)
    days = (d_to - d_from).days + 1
    p_to = d_from - timedelta(days=1)
    return p_to - timedelta(days=days - 1), p_to


def forecast_month(s: Session, actor: Actor, ref: date | None = None) -> dict:
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
    elapsed_sec = max(int((cut - start).total_seconds()), 86400)
    run_rate = fact["revenue"] * total_sec // elapsed_sec if fact["revenue"] else 0
    forecast = max(run_rate, fact["revenue"] + booked)
    rev = fact["revenue"]
    gross_ratio_num = rev - fact["masters_share"] - fact["materials"]
    forecast_gross = forecast * gross_ratio_num // rev if rev else 0
    operating = int(s.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(
        Expense.spent_on >= m_from, Expense.spent_on <= m_to, Expense.is_deleted.is_(False),
        Expense.category.not_in(ExpenseCategory.CASH_FLOW_ONLY))) or 0)
    from .utilities import generator_totals

    operating += generator_totals(s, start, cut)["total_cost"]
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
    actor.require(Role.DIRECTOR)
    cur = dashboard(s, actor, d_from, d_to)
    p_from, p_to = _previous_range(d_from, d_to)
    prev = dashboard(s, actor, p_from, p_to)
    keys = ("revenue", "orders_count", "average_check", "clients_count", "salon_share", "net_profit")
    compare = {k: {"current": cur[k], "previous": prev[k], "change_bp": _change_bp(cur[k], prev[k])} for k in keys}

    start, end = _bounds(d_from, d_to)
    rows = s.execute(select(Order.client_id, Order.closed_at, Order.amount, Order.master_id)
                     .where(Order.closed_at >= start, Order.closed_at < end, Order.status == OrderStatus.COMPLETED)).all()
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
