from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.orm import Session

from ...services import card_terminal, terminal
from ...services.common import Actor
from ..deps import actor_short, get_actor, get_session
from ..schemas import BaseModel, MaterialLine, ProductLine

router = APIRouter(prefix="/api", tags=["card-terminal"])


class StartIn(BaseModel):
    appointment_id: int
    amount: int
    materials: list[MaterialLine] = []
    client_name: str | None = Field(default=None, max_length=128)
    products: list[ProductLine] = []
    price: int | None = Field(default=None, ge=0)


class TerminalSettingsIn(BaseModel):
    enabled: bool | None = None
    ip: str | None = Field(default=None, max_length=64)
    port: int | None = None


@router.get("/card-terminal/status")
def status(actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return card_terminal.status(s, actor)


@router.post("/card-payments")
def start(body: StartIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return card_terminal.start_payment(s, actor, body.appointment_id, amount=body.amount,
                                       materials=[m.model_dump() for m in body.materials], client_name=body.client_name,
                                       products=[x.model_dump() for x in body.products], price=body.price)


@router.post("/card-payments/{payment_id}/pay")
def pay_on_terminal(payment_id: int, request: Request, actor: Actor = Depends(actor_short)):
    return card_terminal.ecr_pay(request.app.state.db, actor, payment_id)


@router.get("/settings/terminal")
def get_terminal_settings(actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return terminal.get_settings(s, actor)


@router.patch("/settings/terminal")
def patch_terminal_settings(body: TerminalSettingsIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return terminal.update_settings(s, actor, **body.model_dump(exclude_unset=True))


@router.post("/settings/terminal/check")
def check_terminal(request: Request, actor: Actor = Depends(actor_short)):
    actor.require("director")
    with request.app.state.db.session() as s:
        cfg = terminal.settings_public(s)
    if not cfg["ip"]:
        from ...errors import ValidationError

        raise ValidationError("Вкажіть IP-адресу термінала і збережіть налаштування")
    return terminal.check_connection(cfg["ip"], cfg["port"]) | {"ip": cfg["ip"], "port": cfg["port"]}


@router.post("/card-payments/{payment_id}/check")
def check(payment_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return card_terminal.check_payment(s, actor, payment_id)


@router.post("/card-payments/{payment_id}/cancel")
def cancel(payment_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return card_terminal.cancel_payment(s, actor, payment_id)


@router.get("/appointments/{appt_id}/card-payments")
def for_appointment(appt_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return card_terminal.list_for_appointment(s, actor, appt_id)


@router.post("/card-payments/{payment_id}/mock-complete")
def mock_complete(payment_id: int, ok: bool = True, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    from ...config import is_production
    from ...errors import Forbidden
    from ...services import card_terminal_providers as tp

    if is_production():
        raise Forbidden("Імітація оплати недоступна в робочому середовищі")
    p = card_terminal._get(s, actor, payment_id)
    if p.mode != "mock" or p.status != "pending":
        raise Forbidden("Доступно лише для імітації")
    prov = tp.get_provider("mock")
    prov.complete(p.jwt, ok)
    return card_terminal.check_payment(s, actor, payment_id)
