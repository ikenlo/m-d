from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from ...services import alerts, vacation
from ...services.common import Actor, get_setting
from ..deps import get_actor, get_session
from ..schemas import BaseModel

router = APIRouter(prefix="/api", tags=["alerts"])


class IdsIn(BaseModel):
    ids: list[int] | None = None


class DisabledIn(BaseModel):
    disabled: list[str]


class VacationIn(BaseModel):
    active: bool
    days: int = 14


class RequestIn(BaseModel):
    action: str
    payload: dict = {}


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.get("/alerts")
def my_alerts(unread: bool = False, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return alerts.my_alerts(s, actor, unread_only=unread)


@router.post("/alerts/read")
def read_alerts(body: IdsIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return {"marked": alerts.mark_read(s, actor, body.ids)}


@router.post("/alerts/{notification_id}/handled")
def handled(notification_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return alerts.mark_handled(s, actor, notification_id)


@router.get("/alerts/settings")
def alert_settings(actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    actor.require("director")
    return {"critical": alerts.CRITICAL, "important": alerts.IMPORTANT, "info": alerts.INFO, "labels": alerts.LABELS,
            "disabled": get_setting(s, "alerts_disabled") or []}


@router.put("/alerts/settings")
def set_alert_settings(body: DisabledIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return {"disabled": alerts.set_disabled(s, actor, body.disabled)}


@router.get("/settings/vacation-mode")
def vacation_status(actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return vacation.status(s)


@router.post("/settings/vacation-mode")
def vacation_toggle(body: VacationIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return vacation.set_mode(s, actor, body.active, body.days)


@router.get("/permission-requests")
def list_requests(status: str | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return [vacation.request_public(r) for r in vacation.list_requests(s, actor, status)]


@router.post("/permission-requests", status_code=201)
def create_request(body: RequestIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return vacation.request_public(vacation.create_request(s, actor, body.action, body.payload))


@router.post("/permission-requests/{rid}/approve")
def approve(rid: int, body: NoteIn | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return vacation.request_public(vacation.approve(s, actor, rid, body.note if body else None))


@router.post("/permission-requests/{rid}/reject")
def reject(rid: int, body: NoteIn | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return vacation.request_public(vacation.reject(s, actor, rid, body.note if body else None))


@router.post("/permission-requests/{rid}/withdraw")
def withdraw(rid: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return vacation.request_public(vacation.withdraw(s, actor, rid))
