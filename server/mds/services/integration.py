"""Дані для MDs Manager (/api/ext/v1): лише читання, без персональних даних клієнтів (D-5)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Forbidden, ValidationError
from ..models import Client, Expense, ExpenseCategory, Order, OrderProduct, OrderStatus, Product, Service, StockMove
from .common import Actor

MAX_DAYS = 366
MAX_LIMIT = 1000


def _require(actor: Actor) -> None:
    if actor.role not in ("integration", "system"):
        raise Forbidden("Лише для інтеграції MDs Manager")


def _range(date_from: date | None, date_to: date | None) -> tuple[datetime, datetime, date, date]:
    d_to = date_to or clock.now().date()
    d_from = date_from or (d_to - timedelta(days=30))
    if d_from > d_to:
        raise ValidationError("date_from пізніше за date_to")
    if (d_to - d_from).days >= MAX_DAYS:
        raise ValidationError(f"Період — не довше {MAX_DAYS} днів")
    return datetime.combine(d_from, time.min), datetime.combine(d_to + timedelta(days=1), time.min), d_from, d_to


def _page(limit: int, offset: int) -> tuple[int, int]:
    if not (1 <= limit <= MAX_LIMIT) or offset < 0:
        raise ValidationError(f"limit — від 1 до {MAX_LIMIT}, offset ≥ 0")
    return limit, offset


def orders(s: Session, actor: Actor, *, date_from: date | None = None, date_to: date | None = None,
           status: str | None = None, limit: int = 500, offset: int = 0) -> dict:
    _require(actor)
    start, end, _, _ = _range(date_from, date_to)
    limit, offset = _page(limit, offset)
    q = select(Order).where(Order.closed_at >= start, Order.closed_at < end)
    if status:
        if status not in OrderStatus.ALL:
            raise ValidationError("Невідомий статус")
        q = q.where(Order.status == status)
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = s.scalars(q.order_by(Order.id).limit(limit).offset(offset))
    items = []
    for o in rows:
        prod = [x for x in o.products if not x.refunded]
        items.append({
            "id": o.id, "created_at": o.closed_at.isoformat(), "status": o.status,
            "master_id": o.master_id, "service_id": o.service_id, "service_name": o.service.name if o.service else None,
            "client_id": o.client_id, "payment_method": o.payment_method,
            "price_snapshot": o.price_snapshot, "amount": o.amount, "discount": o.price_snapshot - o.amount,
            "materials_cost": o.materials_cost, "master_earning": o.master_earning, "salon_earning": o.salon_earning,
            "products_total": sum(x.total for x in prod), "products_cost": sum(x.cost_price_snapshot_cents * x.qty for x in prod),
        })
    return {"total": int(total), "limit": limit, "offset": offset, "items": items}


def stock_moves(s: Session, actor: Actor, *, date_from: date | None = None, date_to: date | None = None,
                item_type: str | None = None, limit: int = 500, offset: int = 0) -> dict:
    from ..models import Material
    from .stock import display_name

    _require(actor)
    start, end, _, _ = _range(date_from, date_to)
    limit, offset = _page(limit, offset)
    q = select(StockMove).where(StockMove.ts >= start, StockMove.ts < end)
    if item_type:
        if item_type not in ("material", "product"):
            raise ValidationError("item_type: material або product")
        q = q.where(StockMove.item_type == item_type)
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    items = []
    for mv in s.scalars(q.order_by(StockMove.id).limit(limit).offset(offset)):
        if mv.item_type == "product":
            pr = s.get(Product, mv.product_id)
            name, unit, qty = (pr.name if pr else None), "шт", mv.qty_milli // 1000
        else:
            m = s.get(Material, mv.material_id)
            name, unit, qty = (display_name(m) if m else None), (m.unit if m else None), mv.qty_milli / 1000
        items.append({"id": mv.id, "ts": mv.ts.isoformat(), "item_type": mv.item_type, "material_id": mv.material_id,
                      "product_id": mv.product_id, "name": name, "unit": unit, "reason": mv.reason, "qty": qty,
                      "unit_price": mv.unit_price, "cost": mv.cost, "order_id": mv.order_id})
    return {"total": int(total), "limit": limit, "offset": offset, "items": items}


def products(s: Session, actor: Actor) -> list[dict]:
    _require(actor)
    return [{"id": p.id, "name": p.name, "sku": p.sku, "category": p.category, "cost_price_cents": p.cost_price_cents,
             "sale_price_cents": p.sale_price_cents, "markup_bp": p.markup_bp, "stock_qty": p.stock_qty, "active": p.active}
            for p in s.scalars(select(Product).order_by(Product.id))]


def clients(s: Session, actor: Actor, *, limit: int = 500, offset: int = 0) -> dict:
    """Без імен, телефонів, дат народження, Telegram/Instagram, нотаток — лише знеособлена статистика."""
    from .client_base import _cfg, _order_stats, segment

    _require(actor)
    limit, offset = _page(limit, offset)
    total = s.scalar(select(func.count(Client.id))) or 0
    rows = list(s.scalars(select(Client).order_by(Client.id).limit(limit).offset(offset)))
    stats = _order_stats(s, [c.id for c in rows])
    now, cfg = clock.now(), _cfg(s)
    items = []
    for c in rows:
        st = stats.get(c.id) or {"visits": 0, "spent": 0, "first_visit": None, "last_visit": None}
        items.append({
            "id": c.id, "created_at": c.created_at.isoformat(), "source": c.source,
            "marketing_consent": bool(c.marketing_consent), "blocked": bool(c.blocked),
            "segment": segment(st["visits"], st["spent"], st["last_visit"], c.created_at, now, cfg),
            "visits": st["visits"], "spent": st["spent"],
            "first_visit": st["first_visit"].isoformat() if st["first_visit"] else None,
            "last_visit": st["last_visit"].isoformat() if st["last_visit"] else None,
        })
    return {"total": int(total), "limit": limit, "offset": offset, "items": items}


def daily(s: Session, actor: Actor, *, date_from: date | None = None, date_to: date | None = None) -> list[dict]:
    _require(actor)
    start, end, d_from, d_to = _range(date_from, date_to)
    day = func.date(Order.closed_at)
    base = (Order.closed_at >= start, Order.closed_at < end, Order.status == OrderStatus.COMPLETED)
    out = {(d_from + timedelta(days=i)).isoformat(): {
        "date": (d_from + timedelta(days=i)).isoformat(), "orders": 0, "revenue": 0, "price_total": 0, "discounts": 0,
        "masters_share": 0, "materials": 0, "salon": 0, "cash": 0, "noncash": 0, "products_revenue": 0, "products_cost": 0,
        "products_bonus": 0, "unpaid_count": 0, "unpaid_amount": 0, "expenses": 0} for i in range((d_to - d_from).days + 1)}
    for d, n, a, p, m, mat, sal, cash in s.execute(select(
            day, func.count(Order.id), func.sum(Order.amount), func.sum(Order.price_snapshot), func.sum(Order.master_earning),
            func.sum(Order.materials_cost), func.sum(Order.salon_earning),
            func.sum(case((Order.payment_method == "cash", Order.amount), else_=0))).where(*base).group_by(day)):
        r = out[str(d)[:10]]
        r.update(orders=int(n), revenue=int(a or 0), price_total=int(p or 0), discounts=int(p or 0) - int(a or 0),
                 masters_share=int(m or 0), materials=int(mat or 0), salon=int(sal or 0), cash=int(cash or 0),
                 noncash=int(a or 0) - int(cash or 0))
    for d, rev, cost, bonus in s.execute(select(
            day, func.sum(OrderProduct.sale_price_snapshot_cents * OrderProduct.qty),
            func.sum(OrderProduct.cost_price_snapshot_cents * OrderProduct.qty), func.sum(OrderProduct.master_bonus_cents))
            .join(Order, Order.id == OrderProduct.order_id).where(*base, OrderProduct.refunded.is_(False)).group_by(day)):
        out[str(d)[:10]].update(products_revenue=int(rev or 0), products_cost=int(cost or 0), products_bonus=int(bonus or 0))
    for d, n, a in s.execute(select(day, func.count(Order.id), func.sum(Order.amount)).where(
            Order.closed_at >= start, Order.closed_at < end, Order.status == OrderStatus.UNPAID).group_by(day)):
        out[str(d)[:10]].update(unpaid_count=int(n), unpaid_amount=int(a or 0))
    for d, a in s.execute(select(Expense.spent_on, func.sum(Expense.amount)).where(
            Expense.spent_on >= d_from, Expense.spent_on <= d_to, Expense.is_deleted.is_(False),
            Expense.category.not_in(ExpenseCategory.CASH_FLOW_ONLY)).group_by(Expense.spent_on)):
        out[str(d)[:10]]["expenses"] = int(a or 0)
    return list(out.values())


def services_catalog(s: Session, actor: Actor) -> list[dict]:
    _require(actor)
    return [{"id": x.id, "name": x.name, "section": x.section, "category": x.category, "price": x.price,
             "price_from": bool(x.price_from), "duration_min": x.duration_min, "active": bool(x.is_active)}
            for x in s.scalars(select(Service).order_by(Service.id))]
