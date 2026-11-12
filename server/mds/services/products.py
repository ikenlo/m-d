from __future__ import annotations

from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, Forbidden, ValidationError
from ..models import (
    BonusAccrual,
    Order,
    OrderProduct,
    OrderStatus,
    PaymentMethod,
    Payout,
    PayoutTag,
    Product,
    Role,
    ShiftStatus,
    StockMove,
    User,
)
from ..money import fmt
from .common import Actor, audit, get_or_404, ledger

BONUS_BP = 1000
PRODUCT_FIELDS = ("name", "sku", "barcode", "category", "cost_price_cents", "sale_price_cents", "markup_bp", "active")


def bonus_for(sale_total: int) -> int:
    return sale_total * BONUS_BP // 10000


def product_public(p: Product) -> dict:
    return {"id": p.id, "name": p.name, "sku": p.sku, "barcode": p.barcode, "category": p.category,
            "cost_price_cents": p.cost_price_cents, "sale_price_cents": p.sale_price_cents, "markup_bp": p.markup_bp,
            "suggested_price_cents": p.cost_price_cents + p.cost_price_cents * p.markup_bp // 10000,
            "stock_qty": p.stock_qty, "active": p.active, "low": p.stock_qty <= 0}


def line_public(op: OrderProduct) -> dict:
    return {"id": op.id, "product_id": op.product_id, "name": op.name, "qty": op.qty,
            "sale_price_snapshot_cents": op.sale_price_snapshot_cents, "total": op.total,
            "master_bonus_cents": op.master_bonus_cents, "refunded": op.refunded}


def _clean(data: dict, creating: bool) -> dict:
    out = {k: data[k] for k in PRODUCT_FIELDS if k in data}
    for k in ("name", "sku", "barcode", "category"):
        if k in out and isinstance(out[k], str):
            out[k] = out[k].strip() or None
    if (creating or "name" in out) and not out.get("name"):
        raise ValidationError("Вкажіть назву товару")
    for k in ("cost_price_cents", "sale_price_cents", "markup_bp"):
        if k in out:
            v = out[k]
            if v is None and k != "sale_price_cents":
                out[k] = 10000 if k == "markup_bp" else 0
                continue
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValidationError("Ціни й націнка — цілі невід'ємні числа (копійки, bp)")
    if creating and out.get("sale_price_cents") is None:
        cost = out.get("cost_price_cents", 0)
        out["sale_price_cents"] = cost + cost * out.get("markup_bp", 10000) // 10000
    return out


def create_product(s: Session, actor: Actor, data: dict) -> Product:
    actor.require(Role.DIRECTOR)
    vals = _clean(data, True)
    stock = int(data.get("stock_qty") or 0)
    p = Product(**vals)
    s.add(p)
    s.flush()
    audit(s, actor, "product", p.id, "create", None, product_public(p))
    if stock:
        receive(s, actor, p.id, stock, cost=stock * p.cost_price_cents, note="Початковий залишок")
    return p


def update_product(s: Session, actor: Actor, product_id: int, data: dict) -> Product:
    actor.require(Role.DIRECTOR)
    p: Product = get_or_404(s, Product, product_id, "Товар")
    before = product_public(p)
    for k, v in _clean(data, False).items():
        setattr(p, k, v)
    s.flush()
    audit(s, actor, "product", p.id, "update", before, product_public(p))
    return p


def list_products(s: Session, active_only: bool = True) -> list[Product]:
    q = select(Product)
    if active_only:
        q = q.where(Product.active.is_(True))
    return list(s.scalars(q.order_by(Product.category, Product.name)))


def _move(s: Session, actor: Actor, p: Product, kind: str, qty: int, *, cost: int = 0, order_id: int | None = None,
          note: str | None = None) -> StockMove:
    from .stock import REASONS

    s.execute(update(Product).where(Product.id == p.id).values(stock_qty=Product.stock_qty + qty)
              .execution_options(synchronize_session=False))
    s.refresh(p)
    mv = StockMove(item_type="product", product_id=p.id, kind=kind, reason=REASONS.get(kind, "correction"),
                   qty_milli=qty * 1000, unit_price=p.cost_price_cents, cost=cost, order_id=order_id,
                   user_id=actor.user_id, note=note)
    s.add(mv)
    s.flush()
    return mv


def _qty(v) -> int:
    if not isinstance(v, int) or isinstance(v, bool):
        try:
            f = float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            raise ValidationError("Кількість товару — ціле число") from None
        if f != int(f):
            raise ValidationError("Кількість товару — ціле число")
        v = int(f)
    return v


def receive(s: Session, actor: Actor, product_id: int, qty, *, cost: int | None = None, note: str | None = None) -> StockMove:
    actor.require(Role.DIRECTOR)
    p: Product = get_or_404(s, Product, product_id, "Товар")
    q = _qty(qty)
    if q <= 0:
        raise ValidationError("Кількість приходу має бути більшою за 0")
    cost = q * p.cost_price_cents if cost is None else int(cost)
    if cost < 0:
        raise ValidationError("Сума не може бути від'ємною")
    before = p.cost_price_cents
    if p.stock_qty > 0:
        p.cost_price_cents = (p.stock_qty * before + cost + (p.stock_qty + q) // 2) // (p.stock_qty + q)
    else:
        p.cost_price_cents = (cost + q // 2) // q
    mv = _move(s, actor, p, "receipt", q, cost=cost, note=note)
    audit(s, actor, "product", p.id, "receipt", {"cost_price_cents": before},
          {"qty": q, "cost": cost, "cost_price_cents": p.cost_price_cents}, note)
    return mv


def inventory(s: Session, actor: Actor, product_id: int, actual, note: str | None = None) -> StockMove | None:
    actor.require(Role.DIRECTOR)
    p: Product = get_or_404(s, Product, product_id, "Товар")
    target = _qty(actual)
    diff = target - p.stock_qty
    if diff == 0:
        return None
    before = p.stock_qty
    mv = _move(s, actor, p, "inventory", diff, cost=abs(diff) * p.cost_price_cents, note=note or "Інвентаризація")
    audit(s, actor, "product", p.id, "inventory", {"stock_qty": before}, {"stock_qty": target}, note)
    return mv


def _check_can_sell(actor: Actor, o: Order) -> None:
    if o.status not in OrderStatus.ACCRUING:
        raise Conflict("До скасованого або поверненого замовлення товар не додається")
    if actor.is_director:
        return
    if not actor.is_master or o.master_id != actor.user_id:
        raise Forbidden("Можна додавати товари лише у своє замовлення")
    if o.shift is None or o.shift.status != ShiftStatus.OPEN:
        raise Forbidden("Зміну закрито — товар додає директор")


def sell(s: Session, actor: Actor, order_id: int, product_id: int, qty) -> OrderProduct:
    o: Order = get_or_404(s, Order, order_id, "Замовлення")
    _check_can_sell(actor, o)
    p: Product = get_or_404(s, Product, product_id, "Товар")
    if not p.active:
        raise ValidationError("Товар не продається")
    q = _qty(qty)
    if q <= 0:
        raise ValidationError("Кількість має бути більшою за 0")
    total = p.sale_price_cents * q
    bonus = bonus_for(total)
    cost = p.cost_price_cents * q
    op = OrderProduct(order_id=o.id, product_id=p.id, name=p.name, qty=q, sale_price_snapshot_cents=p.sale_price_cents,
                      cost_price_snapshot_cents=p.cost_price_cents, master_bonus_cents=bonus, created_by=actor.user_id,
                      created_at=clock.now())
    s.add(op)
    s.flush()
    _move(s, actor, p, "sale", -q, cost=cost, order_id=o.id)
    s.add(BonusAccrual(master_id=o.master_id, order_product_id=op.id, amount=bonus, accrued_on=clock.now().date(),
                       created_by=actor.user_id, note=f"10% від продажу «{p.name}» × {q}"))
    ledger(s, actor, "product_sale", "order_product", op.id, master_id=o.master_id, amount=total, master_amount=bonus,
           materials_amount=cost, salon_amount=total - bonus - cost,
           details={"order_id": o.id, "product_id": p.id, "qty": q, "payment_method": o.payment_method})
    audit(s, actor, "order", o.id, "add_product", None, line_public(op))
    s.expire(o, ["products"])
    return op


def sell_alone(s: Session, actor: Actor, *, master_id: int, items: list[dict], payment_method: str,
               client_id: int | None = None, idempotency_key: str | None = None) -> Order:
    from .orders import open_shift

    if not (actor.is_director or actor.user_id == master_id):
        raise Forbidden("Майстер продає товари лише від свого імені")
    if payment_method not in PaymentMethod.ALL:
        raise ValidationError("Оберіть спосіб оплати")
    if not items:
        raise ValidationError("Додайте хоча б один товар")
    if idempotency_key:
        existing = s.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
        if existing:
            return existing
    master = get_or_404(s, User, master_id, "Майстра")
    if master.role != Role.MASTER:
        raise ValidationError("Продаж оформлюється на майстра")
    shift = open_shift(s, actor, master.id)
    o = Order(appointment_id=None, idempotency_key=idempotency_key, master_id=master.id, client_id=client_id,
              service_id=None, shift_id=shift.id, price_snapshot=0, amount=0, materials_cost=0,
              payment_method=payment_method, master_rate_bp=master.master_rate_bp, status=OrderStatus.COMPLETED,
              master_earning=0, salon_earning=0, closed_at=clock.now(), closed_by=actor.user_id or master.id)
    s.add(o)
    s.flush()
    audit(s, actor, "order", o.id, "close_products_only", None, {"items": items, "payment_method": payment_method})
    for it in items:
        sell(s, actor, o.id, int(it["product_id"]), it.get("qty", 1))
    return o


def refund_line(s: Session, actor: Actor, line_id: int, reason: str) -> OrderProduct:
    actor.require(Role.DIRECTOR)
    if not reason or not reason.strip():
        raise ValidationError("Вкажіть причину повернення")
    op: OrderProduct = get_or_404(s, OrderProduct, line_id, "Рядок товару")
    if op.refunded:
        raise Conflict("Товар уже повернено", code="already_refunded")
    _refund(s, actor, op, reason)
    return op


def _refund(s: Session, actor: Actor, op: OrderProduct, reason: str) -> None:
    o = s.get(Order, op.order_id)
    p = s.get(Product, op.product_id)
    op.refunded = True
    _move(s, actor, p, "return", op.qty, cost=op.cost_price_snapshot_cents * op.qty, order_id=o.id, note=reason)
    s.add(BonusAccrual(master_id=o.master_id, order_product_id=op.id, amount=-op.master_bonus_cents,
                       accrued_on=clock.now().date(), created_by=actor.user_id, note=f"Повернення: {reason}"))
    total, cost = op.total, op.cost_price_snapshot_cents * op.qty
    ledger(s, actor, "product_refund", "order_product", op.id, master_id=o.master_id, amount=-total,
           master_amount=-op.master_bonus_cents, materials_amount=-cost, salon_amount=-(total - op.master_bonus_cents - cost),
           details={"order_id": o.id, "reason": reason})
    audit(s, actor, "order", o.id, "refund_product", line_public(op), None, reason)


def refund_all_lines(s: Session, actor: Actor, o: Order, reason: str) -> None:
    for op in s.scalars(select(OrderProduct).where(OrderProduct.order_id == o.id, OrderProduct.refunded.is_(False))):
        _refund(s, actor, op, reason)


def unpaid_bonus(s: Session, master_id: int, until: date | None = None) -> int:
    q = select(func.coalesce(func.sum(BonusAccrual.amount), 0)).where(BonusAccrual.master_id == master_id,
                                                                       BonusAccrual.payout_id.is_(None))
    if until:
        q = q.where(BonusAccrual.accrued_on <= until)
    return int(s.scalar(q) or 0)


def pay_bonuses(s: Session, actor: Actor, *, master_id: int, until: date | None = None,
                method: str = PaymentMethod.CARD) -> Payout | None:
    from .orders import payout_public

    actor.require(Role.DIRECTOR)
    master = get_or_404(s, User, master_id, "Майстра")
    until = until or clock.now().date()
    rows = list(s.scalars(select(BonusAccrual).where(BonusAccrual.master_id == master.id, BonusAccrual.payout_id.is_(None),
                                                     BonusAccrual.accrued_on <= until)))
    total = sum(r.amount for r in rows)
    if total <= 0:
        return None
    p = Payout(master_id=master.id, amount=total, method=method, tag=PayoutTag.PRODUCT_BONUS,
               note=f"Бонус за товари до {until:%d.%m.%Y}", paid_at=clock.now(), confirmed_by=actor.user_id or 0)
    s.add(p)
    s.flush()
    for r in rows:
        r.payout_id = p.id
    ledger(s, actor, "bonus_payout", "payout", p.id, master_id=master.id, amount=total, master_amount=-total,
           details={"until": until.isoformat(), "accruals": [r.id for r in rows]})
    audit(s, actor, "payout", p.id, "pay_bonuses", None, payout_public(p))
    from . import notifications

    notifications.enqueue(s, master.telegram_chat_id, f"🎁 Виплачено бонус за товари: {fmt(total)}")
    return p


def bonus_summary(s: Session, actor: Actor, master_id: int | None = None) -> dict:
    if actor.is_master:
        master_id = actor.user_id
    else:
        actor.require(Role.DIRECTOR)
    if not master_id:
        raise ValidationError("Вкажіть майстра")
    rows = list(s.scalars(select(BonusAccrual).where(BonusAccrual.master_id == master_id)
                          .order_by(BonusAccrual.id.desc()).limit(50)))
    paid = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(
        Payout.master_id == master_id, Payout.tag == PayoutTag.PRODUCT_BONUS)) or 0)
    return {"master_id": master_id, "unpaid": unpaid_bonus(s, master_id), "paid_total": paid,
            "history": [{"id": r.id, "amount": r.amount, "accrued_on": r.accrued_on.isoformat(), "paid": r.payout_id is not None,
                         "note": r.note} for r in rows]}
