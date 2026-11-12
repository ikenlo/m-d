"""Checkbox (ПРРО): налаштування директора, зміна, фіскалізація замовлень і дані для друку (docs/CHECKBOX.md)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy.orm import Session

from ...services import checkbox
from ...services.common import Actor
from ..deps import actor_short, get_actor, get_session
from ..schemas import BaseModel

router = APIRouter(prefix="/api", tags=["checkbox"])


class CheckboxSettingsIn(BaseModel):
    enabled: bool | None = None
    printer_type: str | None = None
    printer_ip: str | None = Field(default=None, max_length=64)
    printer_port: int | None = None
    printer_bt_name: str | None = Field(default=None, max_length=64)
    printer_width: int | None = None
    printer_codepage: str | None = None


class ShiftIn(BaseModel):
    action: str


@router.get("/settings/checkbox")
def get_settings(request: Request, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return checkbox.get_settings(s, actor, request.app.state.config)


@router.patch("/settings/checkbox")
def patch_settings(body: CheckboxSettingsIn, request: Request, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return checkbox.update_settings(s, actor, request.app.state.config, body.model_dump(exclude_unset=True))


@router.get("/checkbox/client-config")
def client_config(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return checkbox.client_config(s)


@router.post("/checkbox/check")
def check(request: Request, actor: Actor = Depends(actor_short)):
    actor.require("director")
    return checkbox.check_connection(request.app.state.config)


@router.post("/checkbox/shift")
def shift(body: ShiftIn, request: Request, actor: Actor = Depends(actor_short)):
    return checkbox.shift_action(request.app.state.config, actor, body.action)


@router.post("/checkbox/test-print")
def test_print(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return checkbox.test_print(s, actor)


@router.post("/orders/{order_id}/fiscalize")
def fiscalize(order_id: int, request: Request, actor: Actor = Depends(actor_short)):
    code, body = checkbox.fiscalize(request.app.state.db, request.app.state.config, actor, order_id)
    return JSONResponse(body, status_code=code)
