from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..errors import ValidationError
from ..models import Material, Order, Role, StockMove
from .common import Actor, audit, get_or_404


MAX_MILLI = 10**12
REASONS = {"receipt": "receipt", "order": "consumption", "order_return": "return", "adjust": "inventory",
           "writeoff": "correction", "sale": "sale", "return": "return", "inventory": "inventory"}


def to_milli(qty) -> int:
    try:
        v = round(float(str(qty).replace(",", ".").replace(" ", "")) * 1000)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError("Невірна кількість") from None
    if abs(v) > MAX_MILLI:
        raise ValidationError("Завелика кількість")
    return v


def average_pack_cost(stock_milli: int, pack_cost: int, recv_milli: int, recv_cost: int, pack_size: int) -> int:
    """Середня собівартість упаковки після приходу (ціле, округлення до копійки)."""
    pack_milli = max(pack_size, 1) * 1000
    if stock_milli <= 0:
        return (recv_cost * pack_milli + recv_milli // 2) // recv_milli
    value = stock_milli * pack_cost + recv_cost * pack_milli
    qty = stock_milli + recv_milli
    return (value + qty // 2) // qty


def line_cost(m: Material, qty_milli: int) -> int:
    return (m.unit_cost * qty_milli + (m.pack_size * 1000) // 2) // (m.pack_size * 1000)


def display_name(m: Material) -> str:
    return f"{m.name} {m.tone}" if m.tone else m.name


def material_public(m: Material) -> dict:
    stock = m.stock_milli / 1000
    return {
        "id": m.id,
        "name": m.name,
        "tone": m.tone,
        "category": m.category,
        "display_name": display_name(m),
        "unit": m.unit,
        "pack_name": m.pack_name,
        "pack_size": m.pack_size,
        "unit_cost": m.unit_cost,
        "markup_bp": m.markup_bp,
        "suggested_price": m.unit_cost + m.unit_cost * m.markup_bp // 10000,
        "stock": stock,
        "stock_packs": round(stock / m.pack_size, 2) if m.pack_size else stock,
        "min_stock": m.min_stock_milli / 1000 if m.min_stock_milli is not None else None,
        "low": m.min_stock_milli is not None and m.stock_milli <= m.min_stock_milli,
        "is_active": m.is_active,
    }


def _move(s: Session, actor: Actor, m: Material, kind: str, qty_milli: int, *, cost: int = 0,
          order_id: int | None = None, note: str | None = None) -> StockMove:
    s.execute(update(Material).where(Material.id == m.id).values(stock_milli=Material.stock_milli + qty_milli)
              .execution_options(synchronize_session=False))
    s.refresh(m)
    mv = StockMove(item_type="material", material_id=m.id, kind=kind, reason=REASONS.get(kind, "correction"),
                   qty_milli=qty_milli, unit_price=m.unit_cost, cost=cost, order_id=order_id,
                   user_id=actor.user_id, note=note)
    s.add(mv)
    s.flush()
    return mv


def receive(s: Session, actor: Actor, material_id: int, qty, *, cost: int | None = None,
            as_expense: bool = False, note: str | None = None) -> StockMove:
    actor.require(Role.DIRECTOR)
    m: Material = get_or_404(s, Material, material_id, "Матеріал")
    q = to_milli(qty)
    if q <= 0:
        raise ValidationError("Кількість приходу має бути більшою за 0")
    if cost is None:
        cost = line_cost(m, q)
    if cost < 0:
        raise ValidationError("Сума не може бути від'ємною")
    before_cost = m.unit_cost
    new_cost = average_pack_cost(m.stock_milli, m.unit_cost, q, cost, m.pack_size)
    mv = _move(s, actor, m, "receipt", q, cost=cost, note=note)
    if new_cost != before_cost:
        m.unit_cost = new_cost
        mv.unit_price = new_cost
    audit(s, actor, "material", m.id, "receipt", {"unit_cost": before_cost},
          {"qty": q / 1000, "cost": cost, "unit_cost": m.unit_cost}, note)
    if as_expense and cost > 0:
        from .. import clock
        from .expenses import create_expense

        actor.require(Role.DIRECTOR)
        create_expense(s, actor, category="materials", amount=cost, spent_on=clock.now().date(),
                       description=f"Закупівля: {display_name(m)}, {q / 1000:g} {m.unit}")
    return mv


def adjust(s: Session, actor: Actor, material_id: int, actual_qty, note: str | None = None) -> StockMove | None:
    actor.require(Role.DIRECTOR)
    m: Material = get_or_404(s, Material, material_id, "Матеріал")
    target = to_milli(actual_qty)
    if target < 0:
        raise ValidationError("Залишок не може бути від'ємним")
    diff = target - m.stock_milli
    if diff == 0:
        return None
    mv = _move(s, actor, m, "adjust", diff, note=note or "Інвентаризація")
    audit(s, actor, "material", m.id, "adjust", {"stock": (target - diff) / 1000}, {"stock": target / 1000}, note)
    return mv


def writeoff(s: Session, actor: Actor, material_id: int, qty, note: str | None = None) -> StockMove:
    actor.require(Role.DIRECTOR)
    m: Material = get_or_404(s, Material, material_id, "Матеріал")
    q = to_milli(qty)
    if q <= 0:
        raise ValidationError("Кількість має бути більшою за 0")
    if not (note or "").strip():
        raise ValidationError("Вкажіть причину списання")
    mv = _move(s, actor, m, "writeoff", -q, cost=line_cost(m, q), note=note)
    audit(s, actor, "material", m.id, "writeoff", None, {"qty": q / 1000}, note)
    _auto_writeoff(s, actor, m)
    return mv


def auto_writeoff_threshold(s: Session, m: Material) -> int:
    from .common import get_setting

    share = int(get_setting(s, "stock_auto_writeoff_milli") or 0)
    return m.pack_size * share


def _auto_writeoff(s: Session, actor: Actor, m: Material, order_id: int | None = None) -> StockMove | None:
    limit = auto_writeoff_threshold(s, m)
    if not limit or not (0 < m.stock_milli < limit):
        return None
    left = m.stock_milli
    mv = _move(s, actor, m, "writeoff", -left, cost=line_cost(m, left), order_id=order_id,
               note=f"Автоматичне списання залишку (менше {limit / m.pack_size / 1000:g} {m.pack_name})".replace(".", ","))
    audit(s, actor, "material", m.id, "auto_writeoff", {"stock": left / 1000}, {"stock": 0}, mv.note)
    return mv


def apply_order(s: Session, actor: Actor, o: Order) -> None:
    for line in o.materials:
        if line.material_id:
            m = s.get(Material, line.material_id)
            _move(s, actor, m, "order", -line.qty, cost=line.total, order_id=o.id)
            _auto_writeoff(s, actor, m, order_id=o.id)


AUTO_RETURN_NOTE = "Повернення автосписання залишку"


def reverse_order(s: Session, actor: Actor, o: Order, note: str = "Виправлення замовлення") -> None:
    for line in o.materials:
        if line.material_id:
            m = s.get(Material, line.material_id)
            _move(s, actor, m, "order_return", line.qty, cost=line.total, order_id=o.id, note=note)
    rows = s.execute(select(StockMove.material_id, StockMove.kind, StockMove.qty_milli, StockMove.note)
                     .where(StockMove.order_id == o.id, StockMove.kind.in_(("writeoff", "order_return")))).all()
    net: dict[int, int] = {}
    for mid, kind, q, n in rows:
        if kind == "writeoff" or (n or "") == AUTO_RETURN_NOTE:
            net[mid] = net.get(mid, 0) + q
    for mid, q in net.items():
        if q < 0:
            m = s.get(Material, mid)
            _move(s, actor, m, "order_return", -q, cost=line_cost(m, -q), order_id=o.id, note=AUTO_RETURN_NOTE)


def list_materials(s: Session, active_only: bool = True) -> list[Material]:
    q = select(Material)
    if active_only:
        q = q.where(Material.is_active.is_(True))
    return list(s.scalars(q.order_by(Material.category, Material.name, Material.tone)))


def moves(s: Session, actor: Actor, material_id: int | None = None, limit: int = 200,
          product_id: int | None = None) -> list[dict]:
    from ..models import Product

    actor.require(Role.DIRECTOR)
    q = select(StockMove)
    if material_id:
        q = q.where(StockMove.material_id == material_id)
    if product_id:
        q = q.where(StockMove.product_id == product_id)
    out = []
    for mv in s.scalars(q.order_by(StockMove.id.desc()).limit(limit)):
        if mv.item_type == "product":
            pr = s.get(Product, mv.product_id)
            name, unit, qty = (pr.name if pr else "?"), "шт", mv.qty_milli // 1000
        else:
            m = s.get(Material, mv.material_id)
            name, unit, qty = display_name(m), m.unit, mv.qty_milli / 1000
        out.append({
            "id": mv.id, "ts": mv.ts.isoformat(), "item_type": mv.item_type, "kind": mv.kind, "reason": mv.reason,
            "material_id": mv.material_id, "product_id": mv.product_id, "material": name, "name": name, "unit": unit,
            "qty": qty, "unit_price": mv.unit_price, "cost": mv.cost, "order_id": mv.order_id, "user_id": mv.user_id,
            "note": mv.note,
        })
    return out
