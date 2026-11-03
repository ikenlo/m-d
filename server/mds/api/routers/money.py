from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...errors import Forbidden
from ...models import Order, Role
from ...services import analytics, expenses, journal, orders
from ...services.common import Actor, get_or_404
from ..deps import get_actor, get_session
from ..schemas import ExpenseIn, ExpensePatch, OrderCorrectionIn, PayoutIn

router = APIRouter(prefix="/api", tags=["finance"])


def _range(period: str | None, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    if date_from:
        return date_from, date_to or date_from
    return analytics.period_range(period or "month", date_to)


# ---------------------------------------------------------------- замовлення
@router.get("/orders")
def list_orders(period: str | None = None, date_from: date | None = None, date_to: date | None = None,
                master_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    a, b = _range(period, date_from, date_to)
    return [orders.order_public(o) for o in orders.list_orders(s, actor, date_from=a, date_to=b, master_id=master_id)]


@router.get("/orders/{order_id}")
def get_order(order_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    o = get_or_404(s, Order, order_id, "Замовлення")
    if not actor.is_director and o.master_id != actor.user_id:
        raise Forbidden("Немає доступу до цього замовлення")
    out = orders.order_public(o)
    if actor.is_director:
        out["history"] = journal.audit_list(s, actor, entity="order", entity_id=o.id)
    return out


@router.patch("/orders/{order_id}")
def correct_order(order_id: int, body: OrderCorrectionIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    data = body.model_dump(exclude_unset=True, exclude={"reason"})
    if "materials" in data and data["materials"] is not None:
        data["materials"] = [m.model_dump() for m in body.materials]
    return orders.order_public(orders.correct_order(s, actor, order_id, data, body.reason))


# -------------------------------------------------------------------- зміни
@router.get("/shifts/current")
def current_shift(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    actor.require(Role.MASTER)
    sh = orders.current_shift(s, actor.user_id)
    return orders.shift_public(s, sh) if sh else None


@router.post("/shifts/open")
def open_shift(master_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return orders.shift_public(s, orders.open_shift(s, actor, master_id or actor.user_id))


@router.post("/shifts/close")
def close_shift(master_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return orders.shift_public(s, orders.close_shift(s, actor, master_id or actor.user_id))


@router.get("/shifts")
def list_shifts(status: str | None = None, master_id: int | None = None,
                actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [orders.shift_public(s, sh) for sh in orders.list_shifts(s, actor, status=status, master_id=master_id)]


@router.get("/shifts/{shift_id}")
def get_shift(shift_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    sh = orders.get_shift(s, actor, shift_id)
    out = orders.shift_public(s, sh)
    out["current"] = orders.shift_report(s, sh)  # актуальні цифри з урахуванням виправлень
    return out


# ------------------------------------------------------------------ виплати
@router.post("/payouts", status_code=201)
def create_payout(body: PayoutIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return orders.payout_public(orders.confirm_payout(s, actor, **body.model_dump()))


@router.get("/payouts")
def list_payouts(master_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [orders.payout_public(p) for p in orders.list_payouts(s, actor, master_id=master_id)]


# --------------------------------------------------------------- аналітика
@router.get("/finance/dashboard")
def dashboard(period: str | None = None, date_from: date | None = None, date_to: date | None = None,
              actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    a, b = _range(period, date_from, date_to)
    return {
        "summary": analytics.dashboard(s, actor, a, b),
        "by_master": analytics.by_master(s, actor, a, b),
        "by_service": analytics.by_service(s, actor, a, b),
        "by_day": analytics.by_day(s, actor, a, b),
    }


@router.get("/finance/my")
def my_finance(master_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return analytics.my_earnings(s, actor, master_id)


# ------------------------------------------------------------------ витрати
@router.get("/expenses")
def list_expenses(period: str | None = None, date_from: date | None = None, date_to: date | None = None,
                  actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    a, b = _range(period, date_from, date_to)
    return [expenses.expense_public(e) for e in expenses.list_expenses(s, actor, a, b)]


@router.post("/expenses", status_code=201)
def create_expense(body: ExpenseIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return expenses.expense_public(expenses.create_expense(s, actor, **body.model_dump()))


@router.patch("/expenses/{expense_id}")
def update_expense(expense_id: int, body: ExpensePatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    data = body.model_dump(exclude_unset=True, exclude={"reason"})
    return expenses.expense_public(expenses.update_expense(s, actor, expense_id, data, body.reason))


@router.delete("/expenses/{expense_id}")
def delete_expense(expense_id: int, reason: str | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    expenses.delete_expense(s, actor, expense_id, reason)
    return {"ok": True}
