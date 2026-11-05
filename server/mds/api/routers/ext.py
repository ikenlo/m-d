"""/api/ext/v1 — службовий API для MDs manager. Доступ лише з ключем MDS_INTEGRATION_TOKEN."""
from __future__ import annotations

import hmac
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, Request
from pydantic import Field
from sqlalchemy.orm import Session

from ...errors import AuthError, NotFound
from ...services import integration
from ..deps import get_session
from ..schemas import BaseModel


def require_integration(request: Request, x_mds_integration_token: str | None = Header(default=None)) -> None:
    expected = request.app.state.config.integration_token
    if not expected:
        raise NotFound("Інтеграцію вимкнено")
    if not x_mds_integration_token or not hmac.compare_digest(x_mds_integration_token, expected):
        raise AuthError("Невірний ключ інтеграції")


router = APIRouter(prefix="/api/ext/v1", tags=["integration"], dependencies=[Depends(require_integration)])


class Attribution(BaseModel):
    lead_source: str | None = Field(default=None, max_length=64)
    utm_source: str | None = Field(default=None, max_length=64)
    utm_medium: str | None = Field(default=None, max_length=64)
    utm_campaign: str | None = Field(default=None, max_length=128)
    utm_content: str | None = Field(default=None, max_length=128)
    campaign_id: str | None = Field(default=None, max_length=64)


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    phone: str = Field(min_length=5, max_length=32)
    telegram_user_id: int | None = None
    instagram_user_id: str | None = Field(default=None, max_length=64)


class BookIn(BaseModel):
    channel: str
    service_id: int
    master_id: int
    starts_at: datetime
    client: ClientIn
    comment: str | None = Field(default=None, max_length=500)
    external_ref: str | None = Field(default=None, max_length=64)
    attribution: Attribution | None = None


class Identity(BaseModel):
    phone: str | None = None
    telegram_user_id: int | None = None
    instagram_user_id: str | None = None


class CancelIn(Identity):
    reason: str | None = Field(default=None, max_length=300)


@router.get("/catalog")
def get_catalog(s: Session = Depends(get_session)):
    return integration.catalog(s)


@router.get("/slots")
def get_slots(service_id: int, date_from: date, date_to: date, master_id: int | None = None,
              s: Session = Depends(get_session)):
    return integration.slots(s, service_id, date_from, date_to, master_id)


@router.post("/appointments")
def book(body: BookIn, s: Session = Depends(get_session)):
    a, created = integration.book(
        s, channel=body.channel, service_id=body.service_id, master_id=body.master_id,
        starts_at=body.starts_at.replace(tzinfo=None, second=0, microsecond=0),
        client_name=body.client.name, client_phone=body.client.phone,
        telegram_user_id=body.client.telegram_user_id, instagram_user_id=body.client.instagram_user_id,
        comment=body.comment, external_ref=body.external_ref,
        attribution=body.attribution.model_dump() if body.attribution else None,
    )
    return {**integration.appointment_public(a), "created": created}


@router.post("/appointments/lookup")
def lookup(body: Identity, s: Session = Depends(get_session)):
    return integration.client_appointments(s, phone=body.phone, telegram_user_id=body.telegram_user_id,
                                           instagram_user_id=body.instagram_user_id)


@router.post("/appointments/{appt_id}/cancel")
def cancel(appt_id: int, body: CancelIn, s: Session = Depends(get_session)):
    a = integration.cancel(s, appt_id, phone=body.phone, telegram_user_id=body.telegram_user_id,
                           instagram_user_id=body.instagram_user_id, reason=body.reason)
    return integration.appointment_public(a)


@router.get("/visits")
def visits(date_from: date, date_to: date, s: Session = Depends(get_session)):
    return integration.visits(s, date_from, date_to)


@router.get("/ad-spend")
def ad_spend(date_from: date, date_to: date, s: Session = Depends(get_session)):
    return integration.ad_spend(s, date_from, date_to)
