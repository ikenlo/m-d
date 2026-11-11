from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from ...errors import ValidationError
from ...services import orders, products, stock
from ...services.common import Actor
from ..deps import get_actor, get_session
from ..schemas import BaseModel, ProductLine

router = APIRouter(prefix="/api", tags=["products"])


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    sku: str | None = Field(default=None, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)
    cost_price_cents: int = 0
    sale_price_cents: int | None = None
    markup_bp: int = 10000
    stock_qty: int = 0


class ProductPatch(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    sku: str | None = Field(default=None, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)
    cost_price_cents: int | None = None
    sale_price_cents: int | None = None
    markup_bp: int | None = None
    active: bool | None = None


class QtyIn(BaseModel):
    qty: int
    cost: int | None = None
    note: str | None = Field(default=None, max_length=500)


class ActualIn(BaseModel):
    actual: int
    note: str | None = Field(default=None, max_length=500)


class ProductsOnlyIn(BaseModel):
    master_id: int
    payment_method: str
    items: list[ProductLine]
    client_id: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=64)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class PayBonusIn(BaseModel):
    master_id: int
    until: date | None = None
    method: str = "card"


class InventoryItem(BaseModel):
    material_id: int | None = None
    product_id: int | None = None
    actual: float


class InventoryIn(BaseModel):
    items: list[InventoryItem]
    note: str | None = Field(default=None, max_length=500)


@router.get("/products")
def list_products(all: bool = False, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [products.product_public(p) for p in products.list_products(s, active_only=not all)]


@router.post("/products", status_code=201)
def create_product(body: ProductIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return products.product_public(products.create_product(s, actor, body.model_dump()))


@router.patch("/products/{product_id}")
def update_product(product_id: int, body: ProductPatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return products.product_public(products.update_product(s, actor, product_id, body.model_dump(exclude_unset=True)))


@router.post("/products/{product_id}/receive", status_code=201)
def receive(product_id: int, body: QtyIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    products.receive(s, actor, product_id, body.qty, cost=body.cost, note=body.note)
    return products.product_public(s.get(products.Product, product_id))


@router.post("/orders/{order_id}/products", status_code=201)
def add_product(order_id: int, body: ProductLine, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    products.sell(s, actor, order_id, body.product_id, body.qty)
    return orders.order_public(s.get(orders.Order, order_id))


@router.post("/orders/products-only", status_code=201)
def products_only(body: ProductsOnlyIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    o = products.sell_alone(s, actor, master_id=body.master_id, items=[x.model_dump() for x in body.items],
                            payment_method=body.payment_method, client_id=body.client_id, idempotency_key=body.idempotency_key)
    s.flush()
    s.refresh(o)
    return orders.order_public(o)


@router.post("/order-products/{line_id}/refund")
def refund_line(line_id: int, body: ReasonIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return products.line_public(products.refund_line(s, actor, line_id, body.reason))


@router.post("/bonuses/pay")
def pay_bonuses(body: PayBonusIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    p = products.pay_bonuses(s, actor, master_id=body.master_id, until=body.until, method=body.method)
    return {"paid": p.amount if p else 0, "payout_id": p.id if p else None}


@router.get("/bonuses/my")
def my_bonuses(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return products.bonus_summary(s, actor, actor.user_id)


@router.get("/bonuses")
def bonuses(master_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return products.bonus_summary(s, actor, master_id)


@router.post("/stock/inventory")
def stock_inventory(body: InventoryIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    out = []
    for it in body.items:
        if (it.material_id is None) == (it.product_id is None):
            raise ValidationError("Кожен рядок інвентаризації — або матеріал, або товар")
        if it.material_id is not None:
            mv = stock.adjust(s, actor, it.material_id, it.actual, body.note)
        else:
            mv = products.inventory(s, actor, it.product_id, int(it.actual), body.note)
        out.append({"material_id": it.material_id, "product_id": it.product_id, "diff": (mv.qty_milli / 1000) if mv else 0})
    return out
