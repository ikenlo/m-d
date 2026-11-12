"""Зовнішній API для MDs Manager: ключ у заголовку X-API-Key. Лише GET, крім ack черги вебхуків (D-5)."""
from __future__ import annotations

import hmac
from datetime import date

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...errors import AuthError, NotFound
from ...services import integration, webhooks
from ...services.common import Actor
from ..deps import get_session

router = APIRouter(prefix="/api/ext/v1", tags=["ext"])


def ext_actor(request: Request, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Actor:
    key = request.app.state.config.manager_api_key
    if not key:
        raise NotFound("Інтеграцію вимкнено")
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), key.encode()):
        raise AuthError("Невірний ключ API")
    return Actor.integration()


@router.get("/orders")
def orders(date_from: date | None = None, date_to: date | None = None, status: str | None = None,
           limit: int = 500, offset: int = 0, a: Actor = Depends(ext_actor), s: Session = Depends(get_session)):
    return integration.orders(s, a, date_from=date_from, date_to=date_to, status=status, limit=limit, offset=offset)


@router.get("/stock-moves")
def stock_moves(date_from: date | None = None, date_to: date | None = None, item_type: str | None = None,
                limit: int = 500, offset: int = 0, a: Actor = Depends(ext_actor), s: Session = Depends(get_session)):
    return integration.stock_moves(s, a, date_from=date_from, date_to=date_to, item_type=item_type,
                                   limit=limit, offset=offset)


@router.get("/products")
def products(a: Actor = Depends(ext_actor), s: Session = Depends(get_session)):
    return integration.products(s, a)


@router.get("/services")
def services(a: Actor = Depends(ext_actor), s: Session = Depends(get_session)):
    return integration.services_catalog(s, a)


@router.get("/clients")
def clients(limit: int = 500, offset: int = 0, a: Actor = Depends(ext_actor), s: Session = Depends(get_session)):
    return integration.clients(s, a, limit=limit, offset=offset)


@router.get("/daily")
def daily(date_from: date | None = None, date_to: date | None = None,
          a: Actor = Depends(ext_actor), s: Session = Depends(get_session)):
    return integration.daily(s, a, date_from=date_from, date_to=date_to)


@router.get("/webhooks")
def webhooks_pull(request: Request, limit: int = 50, a: Actor = Depends(ext_actor), s: Session = Depends(get_session)):
    return webhooks.pull(s, a, limit=limit, timeout_minutes=request.app.state.config.webhook_processing_timeout_minutes)


class AckIn(BaseModel):
    status: str
    error: str | None = None


@router.post("/webhooks/{uuid}/ack")
def webhooks_ack(uuid: str, body: AckIn, a: Actor = Depends(ext_actor), s: Session = Depends(get_session)):
    return webhooks.ack(s, a, uuid, body.status, body.error)
