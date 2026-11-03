"""Склад матеріалів: залишки, прихід, списання на замовлення, інвентаризація.

Облік у базових одиницях матеріалу (г / мл / шт), у тисячних (qty_milli), щоб списувати
дробові кількості (напр. 0,5 туби = 30 г). Кожен рух пишеться в stock_moves (лише додавання),
а поточний залишок — у materials.stock_milli (атомарний UPDATE ... + delta).
Від'ємний залишок дозволено: закриття замовлення не блокується, але склад показує «мінус».
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..errors import ValidationError
from ..models import Material, Order, Role, StockMove
from .common import Actor, audit, get_or_404


MAX_MILLI = 10**12  # 1 000 000 000 г / мл / шт — запобіжник від помилкового введення


def to_milli(qty) -> int:
    try:
        v = round(float(str(qty).replace(",", ".").replace(" ", "")) * 1000)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError("Невірна кількість") from None
    if abs(v) > MAX_MILLI:
        raise ValidationError("Завелика кількість")
    return v


def line_cost(m: Material, qty_milli: int) -> int:
    """Собівартість кількості матеріалу: ціна упаковки × частка упаковки."""
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
    mv = StockMove(material_id=m.id, kind=kind, qty_milli=qty_milli, cost=cost, order_id=order_id,
                   user_id=actor.user_id, note=note)
    s.add(mv)
    s.flush()
    return mv


def receive(s: Session, actor: Actor, material_id: int, qty, *, cost: int | None = None,
            as_expense: bool = False, note: str | None = None) -> StockMove:
    """Прихід на склад. qty — у базових одиницях. cost — сума закупівлі (копійки);
    as_expense=True — одразу записати витрату «Закупівля матеріалів»."""
    actor.require(Role.DIRECTOR, Role.ADMIN)
    m: Material = get_or_404(s, Material, material_id, "Матеріал")
    q = to_milli(qty)
    if q <= 0:
        raise ValidationError("Кількість приходу має бути більшою за 0")
    if cost is None:
        cost = line_cost(m, q)
    if cost < 0:
        raise ValidationError("Сума не може бути від'ємною")
    mv = _move(s, actor, m, "receipt", q, cost=cost, note=note)
    audit(s, actor, "material", m.id, "receipt", None, {"qty": q / 1000, "cost": cost}, note)
    if as_expense and cost > 0:
        from .. import clock
        from .expenses import create_expense

        actor.require(Role.DIRECTOR)
        create_expense(s, actor, category="materials", amount=cost, spent_on=clock.now().date(),
                       description=f"Закупівля: {display_name(m)}, {q / 1000:g} {m.unit}")
    return mv


def adjust(s: Session, actor: Actor, material_id: int, actual_qty, note: str | None = None) -> StockMove | None:
    """Інвентаризація: встановити фактичний залишок (різниця пишеться рухом)."""
    actor.require(Role.DIRECTOR, Role.ADMIN)
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
    """Списання без замовлення (зіпсовано, прострочено, на навчання тощо)."""
    actor.require(Role.DIRECTOR, Role.ADMIN)
    m: Material = get_or_404(s, Material, material_id, "Матеріал")
    q = to_milli(qty)
    if q <= 0:
        raise ValidationError("Кількість має бути більшою за 0")
    if not (note or "").strip():
        raise ValidationError("Вкажіть причину списання")
    mv = _move(s, actor, m, "writeoff", -q, cost=line_cost(m, q), note=note)
    audit(s, actor, "material", m.id, "writeoff", None, {"qty": q / 1000}, note)
    return mv


def apply_order(s: Session, actor: Actor, o: Order) -> None:
    """Списати зі складу матеріали закритого замовлення."""
    for line in o.materials:
        if line.material_id:
            m = s.get(Material, line.material_id)
            _move(s, actor, m, "order", -line.qty, cost=line.total, order_id=o.id)


def reverse_order(s: Session, actor: Actor, o: Order, note: str = "Виправлення замовлення") -> None:
    """Повернути на склад матеріали замовлення (перед заміною матеріалів при виправленні)."""
    for line in o.materials:
        if line.material_id:
            m = s.get(Material, line.material_id)
            _move(s, actor, m, "order_return", line.qty, cost=line.total, order_id=o.id, note=note)


def list_materials(s: Session, active_only: bool = True) -> list[Material]:
    q = select(Material)
    if active_only:
        q = q.where(Material.is_active.is_(True))
    return list(s.scalars(q.order_by(Material.category, Material.name, Material.tone)))


def moves(s: Session, actor: Actor, material_id: int | None = None, limit: int = 200) -> list[dict]:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    q = select(StockMove)
    if material_id:
        q = q.where(StockMove.material_id == material_id)
    out = []
    for mv in s.scalars(q.order_by(StockMove.id.desc()).limit(limit)):
        m = s.get(Material, mv.material_id)
        out.append({
            "id": mv.id, "ts": mv.ts.isoformat(), "kind": mv.kind, "material_id": mv.material_id,
            "material": display_name(m), "unit": m.unit, "qty": mv.qty_milli / 1000, "cost": mv.cost,
            "order_id": mv.order_id, "note": mv.note,
        })
    return out
