"""Податки ФОП (ставки з датами, нарахування, сплати) і офіційна зарплата найманих — шар над services/taxes.py."""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy.orm import Session

from ... import clock
from ...services import taxes
from ...services.common import Actor
from ..deps import get_actor, get_session
from ..schemas import BaseModel

router = APIRouter(prefix="/api", tags=["taxes"])


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


class EmployeeIn(BaseModel):
    tin: str | None = Field(default=None, max_length=10)
    position: str | None = Field(default=None, max_length=128)
    hired_on: date | None = None
    fired_on: date | None = None
    main_job: bool = True
    note: str | None = None


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
    return {"id": p.id, "amount": p.amount}


@router.get("/employees")
def employees(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.list_employees(s, actor)


@router.put("/employees/{user_id}")
def employee_set(user_id: int, body: EmployeeIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.set_employee(s, actor, user_id, body.model_dump())


@router.get("/payroll")
def payroll(month: date | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.payroll_summary(s, actor, month or clock.now().date())


@router.get("/payroll/calc")
def payroll_calc(gross: int, month: date, user_id: int | None = None, actor: Actor = Depends(get_actor),
                 s: Session = Depends(get_session)):
    from ...models import Role

    actor.require(Role.DIRECTOR)
    return taxes.calc_payroll(s, gross, month, user_id)


@router.post("/payroll", status_code=201)
def payroll_accrue(body: SalaryIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.payroll_public(taxes.accrue_salary(s, actor, **body.model_dump()))


@router.post("/payroll/{accrual_id}/correct")
def payroll_correct(accrual_id: int, body: SalaryCorrectIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.payroll_public(taxes.correct_salary(s, actor, accrual_id, gross=body.gross, reason=body.reason))


@router.post("/payroll/payments", status_code=201)
def payroll_pay(body: SalaryPayIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    p = taxes.pay_salary(s, actor, **body.model_dump())
    return {"id": p.id, "amount": p.amount}


@router.get("/payroll/history/{user_id}")
def payroll_history(user_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.payroll_history(s, actor, user_id)


@router.get("/payroll/quarter")
def payroll_quarter(year: int, quarter: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return taxes.payroll_quarter(s, actor, year, quarter)


@router.get("/payroll/quarter.csv")
def payroll_quarter_csv(year: int, quarter: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    body, name = taxes.payroll_quarter_csv(s, actor, year, quarter)
    return Response(body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
