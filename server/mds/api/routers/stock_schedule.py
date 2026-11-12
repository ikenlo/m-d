from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...models import Material
from ...services import schedule, stock
from ...services.common import Actor, get_or_404
from ..schemas import BaseModel
from ..deps import get_actor, get_session

router = APIRouter(prefix="/api", tags=["stock", "schedule"])


class ReceiveIn(BaseModel):
    qty: float
    cost: int | None = None
    as_expense: bool = False
    note: str | None = None


class AdjustIn(BaseModel):
    actual: float
    note: str | None = None


class WriteoffIn(BaseModel):
    qty: float
    note: str


class DayIn(BaseModel):
    off: bool = False
    start: str | None = None
    end: str | None = None


class CopyWeekIn(BaseModel):
    from_monday: date
    weeks: int = 1


@router.get("/stock")
def stock_list(all: bool = False, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return [stock.material_public(m) for m in stock.list_materials(s, active_only=not all)]


@router.post("/stock/{material_id}/receive", status_code=201)
def stock_receive(material_id: int, body: ReceiveIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    stock.receive(s, actor, material_id, body.qty, cost=body.cost, as_expense=body.as_expense, note=body.note)
    return stock.material_public(get_or_404(s, Material, material_id, "Матеріал"))


@router.post("/stock/{material_id}/adjust")
def stock_adjust(material_id: int, body: AdjustIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    stock.adjust(s, actor, material_id, body.actual, body.note)
    return stock.material_public(get_or_404(s, Material, material_id, "Матеріал"))


@router.post("/stock/{material_id}/writeoff")
def stock_writeoff(material_id: int, body: WriteoffIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    stock.writeoff(s, actor, material_id, body.qty, body.note)
    return stock.material_public(get_or_404(s, Material, material_id, "Матеріал"))


@router.get("/stock/moves")
def stock_moves(material_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return stock.moves(s, actor, material_id)


@router.get("/schedule")
def schedule_grid(date_from: date, date_to: date, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return schedule.grid(s, actor, date_from, date_to)


@router.put("/schedule/{master_id}/{day}")
def schedule_set(master_id: int, day: date, body: DayIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    schedule.set_day(s, actor, master_id, day, off=body.off, start=body.start, end=body.end)
    return schedule.grid(s, actor, day, day)["cells"].get(master_id, {}).get(day.isoformat())


@router.delete("/schedule/{master_id}/{day}")
def schedule_reset(master_id: int, day: date, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    schedule.reset_day(s, actor, master_id, day)
    return {"ok": True}


class ParityIn(BaseModel):
    parity: str
    date_from: date
    date_to: date
    start: str | None = None
    end: str | None = None


@router.post("/schedule/{master_id}/parity")
def schedule_parity(master_id: int, body: ParityIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return {"days": schedule.parity_pattern(s, actor, master_id, body.parity, body.date_from, body.date_to, body.start, body.end)}


@router.post("/schedule/{master_id}/copy-week")
def schedule_copy(master_id: int, body: CopyWeekIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return {"days": schedule.copy_week(s, actor, master_id, body.from_monday, body.weeks)}
