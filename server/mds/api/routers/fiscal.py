"""Фіскалізація, податки ФОП, офіційна зарплата — тонкий шар над services/fiscal.py і services/taxes.py."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import Field
from sqlalchemy.orm import Session

from ... import clock
from ...services import fiscal, taxes
from ...services.common import Actor
from ..deps import get_actor, get_session
from ..schemas import BaseModel

router = APIRouter(prefix="/api", tags=["fiscal"])


class RefundIn(BaseModel):
    amount: int = Field(gt=0)
    reason: str


class CloseShiftIn(BaseModel):
    force: bool = False


class RateIn(BaseModel):
    key: str
    valid_from: date
    rate_bp: int | None = Field(default=None, ge=0, le=10000)
    amount: int | None = Field(default=None, ge=0)
    base: str = "fixed"
    period: str = "month"
    due_day: int | None = Field(default=None, ge=1, le=31)
    due_from: str = "end"
    due_offset_months: int = Field(default=0, ge=0, le=12)
    valid_to: date | None = None
    note: str | None = None


class AccrueIn(BaseModel):
    key: str
    on: date


class CorrectTaxIn(BaseModel):
    reason: str
    amount: int | None = Field(default=None, ge=0)


class TaxPayIn(BaseModel):
    key: str
    period_start: date
    amount: int = Field(gt=0)
    paid_on: date
    note: str | None = None


class SalaryIn(BaseModel):
    user_id: int
    month: date
    gross: int = Field(ge=0)
    note: str | None = None


class SalaryCorrectIn(BaseModel):
    gross: int = Field(ge=0)
    reason: str


class SalaryPayIn(BaseModel):
    user_id: int
    month: date
    amount: int = Field(gt=0)
    paid_on: date
    note: str | None = None


# ------------------------------------------------------------ фіскалізація
@router.get("/fiscal/status")
def fiscal_status(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return fiscal.status_summary(s, actor)


@router.get("/fiscal/receipts")
def fiscal_receipts(date_from: date | None = None, date_to: date | None = None, status: str | None = None,
                    order_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return fiscal.receipts_list(s, actor, d_from=date_from, d_to=date_to, status=status, order_id=order_id)


@router.post("/fiscal/receipts/{receipt_id}/retry")
def fiscal_retry(receipt_id: int, request: Request, actor: Actor = Depends(get_actor)):
    st = fiscal.retry(request.app.state.db, actor, receipt_id)
    return {"status": st}


@router.post("/fiscal/orders/{order_id}/refund")
def fiscal_refund(order_id: int, body: RefundIn, request: Request, actor: Actor = Depends(get_actor)):
    return fiscal.refund(request.app.state.db, actor, order_id, body.amount, body.reason)


@router.post("/fiscal/shift/open")
def fiscal_open(request: Request, actor: Actor = Depends(get_actor)):
    return fiscal.open_shift(request.app.state.db, actor)


@router.post("/fiscal/shift/close")
def fiscal_close(body: CloseShiftIn, request: Request, actor: Actor = Depends(get_actor)):
    return fiscal.close_shift(request.app.state.db, actor, force=body.force)


@router.post("/fiscal/x-report")
def fiscal_x(request: Request, actor: Actor = Depends(get_actor)):
    return fiscal.x_report(request.app.state.db, actor)


@router.post("/fiscal/process")
def fiscal_process(request: Request, actor: Actor = Depends(get_actor)):
    """Надіслати чергу зараз (директор/адміністратор) — те саме робить фоновий цикл."""
    actor.require("director", "admin")
    return fiscal.process_pending(request.app.state.db)


@router.get("/fiscal/reconcile")
def fiscal_reconcile(date_from: date, date_to: date | None = None, deep: bool = False,
                     actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return fiscal.reconcile(s, actor, date_from, date_to or date_from, provider=fiscal.provider_for(s) if deep else None)


def kick_fiscal(background: BackgroundTasks, request: Request) -> None:
    """Після закриття замовлення — спробувати фіскалізувати одразу (після відповіді; фоновий цикл підстрахує)."""
    background.add_task(fiscal.process_pending, request.app.state.db)


# ------------------------------------------------------------ податки ФОП
@router.get("/taxes/rates")
def tax_rates(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.list_rates(s, actor)


@router.post("/taxes/rates", status_code=201)
def tax_add_rate(body: RateIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.rate_public(taxes.add_rate(s, actor, **body.model_dump()))


@router.get("/taxes/fop")
def tax_fop(year: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.fop_summary(s, actor, year or clock.now().year)


@router.post("/taxes/fop/accrue", status_code=201)
def tax_accrue(body: AccrueIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.accrual_public(taxes.accrue_fop(s, actor, body.key, body.on))


@router.post("/taxes/fop/{accrual_id}/correct")
def tax_correct(accrual_id: int, body: CorrectTaxIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.accrual_public(taxes.correct_fop(s, actor, accrual_id, body.reason, body.amount))


@router.post("/taxes/payments", status_code=201)
def tax_pay(body: TaxPayIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    p = taxes.pay_tax(s, actor, **body.model_dump())
    return {"id": p.id}


# ------------------------------------------------------------ офіційна зарплата
@router.get("/payroll")
def payroll(month: date | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.payroll_summary(s, actor, month or clock.now().date())


@router.get("/payroll/calc")
def payroll_calc(gross: int, month: date, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    actor.require("director")
    return taxes.calc_payroll(s, gross, month)


@router.post("/payroll", status_code=201)
def payroll_accrue(body: SalaryIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.payroll_public(taxes.accrue_salary(s, actor, **body.model_dump()))


@router.post("/payroll/{accrual_id}/correct")
def payroll_correct(accrual_id: int, body: SalaryCorrectIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.payroll_public(taxes.correct_salary(s, actor, accrual_id, gross=body.gross, reason=body.reason))


@router.post("/payroll/payments", status_code=201)
def payroll_pay(body: SalaryPayIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    p = taxes.pay_salary(s, actor, **body.model_dump())
    return {"id": p.id}


@router.get("/payroll/history/{user_id}")
def payroll_history(user_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.payroll_history(s, actor, user_id)
