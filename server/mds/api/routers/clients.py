"""Клієнтська база (Customer Intelligence): таблиця, картка зі статистикою, дублікати, аналітика, експорт.
Реєструється раніше за catalog, щоб /api/clients/base тощо не сприймались як /api/clients/{id}."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy.orm import Session

from ... import clock
from ...services import client_base as cb
from ...services.catalog import client_public
from ...services.common import Actor
from ..deps import get_actor, get_session
from ..schemas import BaseModel

router = APIRouter(prefix="/api/clients", tags=["clients"])


@router.get("/base")
def base(q: str = "", segment: str = "", sort: str = "name", page: int = 1, per_page: int = 50, birthdays: int | None = None,
         actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return cb.list_clients(s, actor, q=q, segment_key=segment, sort=sort, page=page, per_page=per_page, birthdays_days=birthdays)


@router.get("/lookup")
def lookup(phone: str, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    """Є клієнт із таким номером? (щоб відкрити картку, а не створювати новий)"""
    c = cb.find_by_phone(s, actor, phone)
    return {"found": bool(c), "client": client_public(c) if c else None}


@router.get("/duplicates")
def duplicates(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return cb.duplicates(s, actor)


@router.get("/analytics")
def analytics(date_from: date | None = None, date_to: date | None = None, actor: Actor = Depends(get_actor),
              s: Session = Depends(get_session)):
    b = date_to or clock.now().date()
    return cb.analytics(s, actor, date_from or b - timedelta(days=29), b)


@router.get("/export.csv")
def export(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return Response(cb.export_csv(s, actor), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="klienty.csv"'})


@router.get("/{client_id}/card")
def card(client_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return cb.card(s, actor, client_id)


class CardIn(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    phone: str | None = None
    phone2: str | None = None
    birthday: date | None = None
    telegram_username: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=32)
    wishes: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=4000)
    marketing_consent: bool | None = None
    blocked: bool | None = None
    blocked_reason: str | None = None


@router.patch("/{client_id}/card")
def update_card(client_id: int, body: CardIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    cb.update_card(s, actor, client_id, body.model_dump(exclude_unset=True))
    return cb.card(s, actor, client_id)


class MergeIn(BaseModel):
    duplicate_id: int


@router.post("/{client_id}/merge")
def merge(client_id: int, body: MergeIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return client_public(cb.merge(s, actor, client_id, body.duplicate_id))


class ReasonIn(BaseModel):
    reason: str


@router.post("/{client_id}/anonymize")
def anonymize(client_id: int, body: ReasonIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return client_public(cb.anonymize(s, actor, client_id, body.reason))
