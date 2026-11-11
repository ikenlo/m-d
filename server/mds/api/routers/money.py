from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from ...errors import Forbidden
from ...models import Order, Role
from ...services import analytics, expenses, idempotency, orders
from ...services.common import Actor, get_or_404
from ..deps import get_actor, get_session, require_finance_pin
from ..schemas import ExpenseIn, ExpensePatch, MarkPaidIn, OrderCorrectionIn, OrderPhoneIn, PayoutIn, VoidOrderIn

router = APIRouter(prefix="/api", tags=["finance"])


def _range(period: str | None, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    if date_from:
        return date_from, date_to or date_from
    return analytics.period_range(period or "month", date_to)


@router.get("/orders")
def list_orders(period: str | None = None, date_from: date | None = None, date_to: date | None = None,
                master_id: int | None = None, status: str | None = None, materials_not_entered: bool | None = None,
                actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    if (status or materials_not_entered is not None) and not (period or date_from or date_to):
        a = b = None
    else:
        a, b = _range(period, date_from, date_to)
    out = [orders.order_public(o) for o in orders.list_orders(s, actor, date_from=a, date_to=b, master_id=master_id,
                                                              status=status, materials_not_entered=materials_not_entered)]
    return [orders.order_for_master(x) for x in out] if actor.is_master else out


@router.get("/orders/needs-materials")
def orders_needing_materials(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [orders.order_public(o) for o in orders.needing_materials(s, actor)]


@router.get("/orders/{order_id}")
def get_order(order_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    o = get_or_404(s, Order, order_id, "Замовлення")
    if not actor.is_director and o.master_id != actor.user_id:
        raise Forbidden("Немає доступу до цього замовлення")
    hist = orders.order_history(s, actor, o)
    out = orders.order_public(o) | {"history": hist[:5], "history_total": len(hist)}
    return orders.order_for_master(out) if actor.is_master else out


@router.put("/orders/{order_id}/phone")
def set_order_phone(order_id: int, body: OrderPhoneIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return orders.order_public(orders.set_order_phone(s, actor, order_id, body.phone))


@router.patch("/orders/{order_id}")
def correct_order(order_id: int, body: OrderCorrectionIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    data = body.model_dump(exclude_unset=True, exclude={"reason", "version"})
    if "materials" in data and data["materials"] is not None:
        data["materials"] = [m.model_dump() for m in body.materials]
    data = {k: v for k, v in data.items() if v is not None}
    return orders.order_public(orders.correct_order(s, actor, order_id, data, body.reason, expected_version=body.version))


@router.post("/orders/{order_id}/mark-paid")
def mark_paid(order_id: int, body: MarkPaidIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return orders.order_public(orders.mark_paid(s, actor, order_id, payment_method=body.payment_method, amount=body.amount,
                                                reason=body.reason, expected_version=body.version))


@router.post("/orders/{order_id}/refund")
def refund_order(order_id: int, body: VoidOrderIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return orders.order_public(orders.refund_order(s, actor, order_id, body.reason, expected_version=body.version))


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, body: VoidOrderIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return orders.order_public(orders.cancel_order(s, actor, order_id, body.reason, expected_version=body.version))


@router.get("/shifts/current")
def current_shift(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    actor.require(Role.MASTER)
    sh = orders.current_shift(s, actor.user_id)
    return orders.shift_public(s, sh, for_master=actor.is_master) if sh else None


@router.post("/shifts/open")
def open_shift(master_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return orders.shift_public(s, orders.open_shift(s, actor, master_id or actor.user_id), for_master=actor.is_master)


@router.post("/shifts/close")
def close_shift(master_id: int | None = None, force: bool = False, actor: Actor = Depends(get_actor),
                s: Session = Depends(get_session)):
    return orders.shift_public(s, orders.close_shift(s, actor, master_id or actor.user_id, force=force), for_master=actor.is_master)


@router.get("/shifts")
def list_shifts(status: str | None = None, master_id: int | None = None,
                actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    shifts = orders.list_shifts(s, actor, status=status, master_id=master_id)
    payouts = orders.shift_payouts(s, [sh.id for sh in shifts])
    return [orders.shift_public(s, sh, payouts, for_master=actor.is_master) for sh in shifts]


@router.get("/shifts/{shift_id}")
def get_shift(shift_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    sh = orders.get_shift(s, actor, shift_id)
    out = orders.shift_public(s, sh, for_master=actor.is_master)
    cur = orders.shift_report(s, sh)
    out["current"] = orders.report_for_master(cur) if actor.is_master else cur
    return out


@router.post("/payouts", status_code=201)
def create_payout(body: PayoutIn, idempotency_key: str | None = Header(default=None),
                  actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    data = body.model_dump()
    return idempotency.run(s, scope="payout", user_id=actor.user_id, key=idempotency_key, payload=data,
                           fn=lambda: orders.payout_public(orders.confirm_payout(s, actor, **data)))


@router.get("/payouts")
def list_payouts(master_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [orders.payout_public(p) for p in orders.list_payouts(s, actor, master_id=master_id)]


@router.get("/finance/dashboard", dependencies=[Depends(require_finance_pin)])
def dashboard(period: str | None = None, date_from: date | None = None, date_to: date | None = None,
              actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    a, b = _range(period, date_from, date_to)
    return {
        "summary": analytics.dashboard(s, actor, a, b),
        "by_master": analytics.by_master(s, actor, a, b),
        "by_service": analytics.by_service(s, actor, a, b),
        "by_day": analytics.by_day(s, actor, a, b),
    }


@router.get("/finance/insights", dependencies=[Depends(require_finance_pin)])
def finance_insights(period: str | None = None, date_from: date | None = None, date_to: date | None = None,
                     actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    a, b = _range(period, date_from, date_to)
    return analytics.insights(s, actor, a, b)


@router.get("/finance/my")
def my_finance(master_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return analytics.my_earnings(s, actor, master_id)


@router.get("/expenses")
def list_expenses(period: str | None = None, date_from: date | None = None, date_to: date | None = None,
                  actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    a, b = _range(period, date_from, date_to)
    return [expenses.expense_public(e) for e in expenses.list_expenses(s, actor, a, b)]


@router.post("/expenses", status_code=201)
def create_expense(body: ExpenseIn, idempotency_key: str | None = Header(default=None),
                   actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    data = body.model_dump()
    return idempotency.run(s, scope="expense", user_id=actor.user_id, key=idempotency_key, payload=data,
                           fn=lambda: expenses.expense_public(expenses.create_expense(s, actor, **data)))


@router.patch("/expenses/{expense_id}")
def update_expense(expense_id: int, body: ExpensePatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    data = body.model_dump(exclude_unset=True, exclude={"reason", "version"})
    return expenses.expense_public(expenses.update_expense(s, actor, expense_id, data, body.reason, expected_version=body.version))


@router.delete("/expenses/{expense_id}")
def delete_expense(expense_id: int, reason: str | None = None, version: int | None = None,
                   actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    expenses.delete_expense(s, actor, expense_id, reason, expected_version=version)
    return {"ok": True}
