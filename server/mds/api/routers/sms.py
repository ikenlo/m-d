from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import Field
from sqlalchemy.orm import Session

from ...models import Role
from ...services import catalog, sms_center
from ...services.common import Actor
from ..deps import get_actor, get_session
from ..schemas import BaseModel

router = APIRouter(prefix="/api/sms", tags=["sms"])


@router.get("/summary")
def summary(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return sms_center.summary(s, actor)


@router.get("/insights")
def insights(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return sms_center.insights(s, actor)


@router.get("/campaigns")
def campaigns(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return sms_center.campaigns(s, actor)


class CampaignIn(BaseModel):
    title: str = Field(max_length=120)
    text: str = Field(max_length=670)
    audience: str


@router.post("/campaigns")
def create_campaign(body: CampaignIn, background: BackgroundTasks, request: Request, dry_run: bool = False,
                    actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    r = sms_center.create_campaign(s, actor, title=body.title, text=body.text, audience_key=body.audience, dry_run=dry_run)
    if not dry_run:
        background.add_task(sms_center.flush, request.app.state.db)
    return r


@router.get("/messages")
def messages(client_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return sms_center.messages(s, actor, client_id=client_id)


class TestIn(BaseModel):
    phone: str
    text: str = Field(default="Перевірка SMS від салону M&D ✓", max_length=300)


@router.post("/test")
def test_sms(body: TestIn, background: BackgroundTasks, request: Request, actor: Actor = Depends(get_actor),
             s: Session = Depends(get_session)):
    actor.require(Role.DIRECTOR)
    from ...errors import Conflict
    from ...services import sms

    if not sms.SENDING_ENABLED:
        raise Conflict(sms.DISABLED_REASON, code="sms_disabled")
    m = sms_center.enqueue(s, phone=catalog.normalize_phone(body.phone), text=body.text, kind="service", reason="test")
    s.flush()
    background.add_task(sms_center.flush, request.app.state.db)
    return {"id": m.id, "ready": sms_center.ready(s)}
