from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from ...services import card_terminal
from ...services.common import Actor
from ..deps import get_actor, get_session
from ..schemas import BaseModel, MaterialLine

router = APIRouter(prefix="/api", tags=["card-terminal"])


class StartIn(BaseModel):
    appointment_id: int
    amount: int
    materials: list[MaterialLine] = []
    client_name: str | None = Field(default=None, max_length=128)


@router.get("/card-terminal/status")
def status(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return card_terminal.status(s, actor)


@router.post("/card-payments")
def start(body: StartIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return card_terminal.start_payment(s, actor, body.appointment_id, amount=body.amount,
                                       materials=[m.model_dump() for m in body.materials], client_name=body.client_name)


@router.post("/card-payments/{payment_id}/check")
def check(payment_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return card_terminal.check_payment(s, actor, payment_id)


@router.post("/card-payments/{payment_id}/cancel")
def cancel(payment_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return card_terminal.cancel_payment(s, actor, payment_id)


@router.get("/appointments/{appt_id}/card-payments")
def for_appointment(appt_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return card_terminal.list_for_appointment(s, actor, appt_id)


@router.post("/card-payments/{payment_id}/mock-complete")
def mock_complete(payment_id: int, ok: bool = True, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    from ...errors import Forbidden
    from ...services import card_terminal_providers as tp

    p = card_terminal._get(s, actor, payment_id)
    if p.mode != "mock" or p.status != "pending":
        raise Forbidden("Доступно лише для імітації")
    prov = tp.get_provider("mock")
    prov.complete(p.jwt, ok)
    return card_terminal.check_payment(s, actor, payment_id)
