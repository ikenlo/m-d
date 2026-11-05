from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from ... import clock
from ...services import utilities as u
from ...services.common import Actor
from ..deps import get_actor, get_session
from ..schemas import BaseModel

router = APIRouter(prefix="/api", tags=["utilities"])


class ReadingIn(BaseModel):
    month: date
    read_on: date
    curr: float = Field(ge=0)
    prev: float | None = Field(default=None, ge=0)
    tariff: int = Field(gt=0)
    paid_amount: int | None = Field(default=None, ge=0)
    paid_on: date | None = None
    note: str | None = None


class ReadingPatch(BaseModel):
    read_on: date | None = None
    curr: float | None = Field(default=None, ge=0)
    prev: float | None = Field(default=None, ge=0)
    tariff: int | None = Field(default=None, gt=0)
    paid_amount: int | None = Field(default=None, ge=0)
    paid_on: date | None = None
    note: str | None = None
    reason: str | None = None


class StartIn(BaseModel):
    power_outage: bool = True
    note: str | None = None


class RunIn(BaseModel):
    started_at: datetime
    hours: float = Field(gt=0)
    power_outage: bool = True
    note: str | None = None


class RunPatch(BaseModel):
    hours: float | None = Field(default=None, ge=0)
    fuel_lph: float | None = Field(default=None, ge=0)
    fuel_price: int | None = Field(default=None, ge=0)
    amort_per_hour: int | None = Field(default=None, ge=0)
    fuel_cost: int | None = Field(default=None, ge=0)
    amort_cost: int | None = Field(default=None, ge=0)
    power_outage: bool | None = None
    reason: str


class MotohoursIn(BaseModel):
    current: float = Field(ge=0)


class MonthIn(BaseModel):
    month: date
    reason: str | None = None


def _month(m: date | None) -> date:
    return u.month_start(m or clock.now().date())


@router.get("/utilities/readings")
def readings(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.list_readings(s, actor)


@router.post("/utilities/readings", status_code=201)
def create_reading(body: ReadingIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.reading_public(u.create_reading(s, actor, **body.model_dump()))


@router.patch("/utilities/readings/{reading_id}")
def update_reading(reading_id: int, body: ReadingPatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    data = body.model_dump(exclude_unset=True, exclude={"reason"})
    return u.reading_public(u.update_reading(s, actor, reading_id, data, body.reason))


@router.post("/utilities/readings/{reading_id}/confirm")
def confirm_reading(reading_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.reading_public(u.confirm_reading(s, actor, reading_id))


@router.get("/generator")
def generator_state(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.generator_state(s, actor)


@router.post("/generator/start")
def generator_start(body: StartIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    u.start_generator(s, actor, power_outage=body.power_outage, note=body.note)
    return u.generator_state(s, actor)


@router.post("/generator/stop")
def generator_stop(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    g = u.stop_generator(s, actor)
    return {**u.generator_state(s, actor), "stopped": u.run_public(g)}


@router.get("/generator/runs")
def generator_runs(month: date | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.list_runs(s, actor, _month(month))


@router.post("/generator/runs", status_code=201)
def generator_add_run(body: RunIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.run_public(u.add_run(s, actor, started_at=body.started_at.replace(tzinfo=None), hours=body.hours,
                                  power_outage=body.power_outage, note=body.note))


@router.patch("/generator/runs/{run_id}")
def generator_correct_run(run_id: int, body: RunPatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    data = body.model_dump(exclude_unset=True, exclude={"reason"})
    return u.run_public(u.correct_run(s, actor, run_id, data, body.reason))


class HoursIn(BaseModel):
    on: date
    current: str | float | None = None
    hours: str | float | None = None
    note: str | None = Field(default=None, max_length=300)


@router.post("/generator/hours", status_code=201)
def generator_hours(body: HoursIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    g = u.record_hours(s, actor, on=body.on, current=body.current, hours=body.hours, note=body.note)
    return {"run": u.run_public(g), "state": u.generator_state(s, actor)}


@router.get("/generator/history")
def generator_history(months: int = 6, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.generator_history(s, actor, months)


@router.post("/generator/motohours")
def generator_motohours(body: MotohoursIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.set_motohours(s, actor, body.current)


@router.get("/utilities/summary")
def summary(month: date | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.month_summary(s, actor, _month(month))


@router.get("/utilities/allocation")
def allocation_preview(month: date, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.allocation_preview(s, actor, month)


@router.post("/utilities/allocate")
def allocate(body: MonthIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.allocate_month(s, actor, body.month)


@router.post("/utilities/reverse")
def reverse(body: MonthIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return {"reversed": u.reverse_month(s, actor, body.month, body.reason or "")}


@router.get("/utilities/charges")
def charges(month: date | None = None, master_id: int | None = None,
            actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return u.charges_list(s, actor, m=month, master_id=master_id)
