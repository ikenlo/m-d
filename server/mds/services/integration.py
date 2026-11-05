"""Службовий обмін даними з MDs manager (AI-адміністратор і маркетинг).

MDS Connect — джерело правди для записів, клієнтів і грошей. MDs manager:
читає каталог і вільний час, створює записи від імені клієнта (з усіма
правилами антиспаму й графіка) і передає, звідки прийшов клієнт; назад
отримує візити, неявки й виручку для розрахунку окупності реклами.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import NotFound, ValidationError
from ..models import (
    Appointment,
    AppointmentAttribution,
    ApptStatus,
    Client,
    Expense,
    Order,
    Service,
)
from . import bookings
from .catalog import normalize_phone
from .common import Actor, audit
from .users import list_masters, masters_for_service

CHANNELS = ("telegram-ai", "instagram")
CLIENT_SOURCE = {"instagram": "instagram", "telegram-ai": "telegram-бот"}
MAX_RANGE_DAYS = 31
ATTR_FIELDS = ("lead_source", "utm_source", "utm_medium", "utm_campaign", "utm_content", "campaign_id")


def _channel(channel: str) -> str:
    if channel not in CHANNELS:
        raise ValidationError(f"Невідомий канал: {channel}")
    return channel


def catalog(s: Session) -> dict:
    services = s.scalars(select(Service).where(Service.is_active.is_(True)).order_by(Service.id)).all()
    return {
        "services": [
            {
                "id": x.id, "name": x.name, "section": x.section, "category": x.category,
                "subcategory": x.subcategory, "duration_min": x.duration_min,
                "price": x.price, "price_from": bool(x.price_from),
                "master_ids": [m.id for m in masters_for_service(s, x.id)],
            }
            for x in services
        ],
        "masters": [{"id": m.id, "name": m.full_name} for m in list_masters(s)],
    }


def slots(s: Session, service_id: int, date_from: date, date_to: date, master_id: int | None = None) -> list[dict]:
    if date_to < date_from or (date_to - date_from).days >= MAX_RANGE_DAYS:
        raise ValidationError("Некоректний діапазон дат")
    out = []
    d = max(date_from, clock.now().date())
    while d <= date_to:
        if master_id:
            found = {t: [master_id] for t in bookings.free_slots(s, master_id, service_id, d, for_client=True)}
        else:
            found = bookings.free_slots_any(s, service_id, d, for_client=True)
        out.extend({"starts_at": t.isoformat(timespec="minutes"), "master_ids": ids} for t, ids in found.items())
        d += timedelta(days=1)
    return out


def _attach_identity(s: Session, actor: Actor, client: Client, instagram_user_id: str | None, channel: str) -> None:
    if instagram_user_id and client.instagram_user_id != instagram_user_id:
        for other in s.scalars(select(Client).where(Client.instagram_user_id == instagram_user_id)):
            other.instagram_user_id = None
        before = client.instagram_user_id
        client.instagram_user_id = instagram_user_id
        audit(s, actor, "client", client.id, "link_instagram", {"instagram_user_id": before},
              {"instagram_user_id": instagram_user_id})
    if not client.source:
        client.source = CLIENT_SOURCE[channel]


def book(
    s: Session,
    *,
    channel: str,
    service_id: int,
    master_id: int,
    starts_at: datetime,
    client_name: str,
    client_phone: str,
    telegram_user_id: int | None = None,
    instagram_user_id: str | None = None,
    comment: str | None = None,
    external_ref: str | None = None,
    attribution: dict | None = None,
) -> tuple[Appointment, bool]:
    """Запис від імені клієнта. Повертає (запис, створено_зараз). Повтор з тим самим external_ref — той самий запис."""
    channel = _channel(channel)
    if external_ref:
        existing = s.scalar(select(AppointmentAttribution).where(AppointmentAttribution.external_ref == external_ref))
        if existing:
            return s.get(Appointment, existing.appointment_id), False
    actor = Actor.via(channel, telegram_user_id)
    a = bookings.create_appointment(
        s, actor, master_id=master_id, service_id=service_id, starts_at=starts_at,
        client_name=client_name, client_phone=client_phone, comment=comment,
    )
    _attach_identity(s, actor, a.client, instagram_user_id, channel)
    attr = {k: (str(v)[:128] if v else None) for k, v in (attribution or {}).items() if k in ATTR_FIELDS}
    s.add(AppointmentAttribution(appointment_id=a.id, channel=channel, external_ref=external_ref, **attr))
    s.flush()
    audit(s, actor, "appointment", a.id, "attribution", None, {"channel": channel, **attr})
    return a, True


def _find_client(s: Session, phone: str | None, telegram_user_id: int | None, instagram_user_id: str | None) -> Client | None:
    if telegram_user_id:
        c = s.scalar(select(Client).where(Client.telegram_user_id == telegram_user_id))
        if c:
            return c
    if instagram_user_id:
        c = s.scalar(select(Client).where(Client.instagram_user_id == instagram_user_id))
        if c:
            return c
    if phone:
        p = normalize_phone(phone)
        return s.scalar(select(Client).where((Client.phone == p) | (Client.phone2 == p)))
    return None


def appointment_public(a: Appointment) -> dict:
    return {
        "id": a.id, "status": a.status, "starts_at": a.starts_at.isoformat(timespec="minutes"),
        "ends_at": a.ends_at.isoformat(timespec="minutes"), "service_id": a.service_id,
        "service_name": a.service.name, "master_id": a.master_id, "master_name": a.master.full_name,
        "price": a.price, "source": a.source,
    }


def client_appointments(s: Session, *, phone: str | None = None, telegram_user_id: int | None = None,
                        instagram_user_id: str | None = None) -> list[dict]:
    c = _find_client(s, phone, telegram_user_id, instagram_user_id)
    if c is None:
        return []
    rows = s.scalars(select(Appointment).where(
        Appointment.client_id == c.id, Appointment.status.in_(ApptStatus.EDITABLE),
        Appointment.starts_at >= clock.now()).order_by(Appointment.starts_at))
    return [appointment_public(a) for a in rows]


def cancel(s: Session, appt_id: int, *, phone: str | None = None, telegram_user_id: int | None = None,
           instagram_user_id: str | None = None, reason: str | None = None) -> Appointment:
    c = _find_client(s, phone, telegram_user_id, instagram_user_id)
    a = s.get(Appointment, appt_id)
    if c is None or a is None or a.client_id != c.id:
        raise NotFound(f"Запис #{appt_id} не знайдено")
    actor = Actor.system()
    return bookings.set_status(s, actor, a.id, ApptStatus.CANCELLED,
                               reason or "Скасовано клієнтом через AI-помічника", notify_client=False)


def visits(s: Session, date_from: date, date_to: date) -> list[dict]:
    """Записи з датою візиту в діапазоні: статус, план/факт виручки, джерело — для окупності реклами."""
    if date_to < date_from or (date_to - date_from).days > 366:
        raise ValidationError("Некоректний діапазон дат")
    start = datetime.combine(date_from, datetime.min.time())
    end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
    rows = s.execute(
        select(Appointment, Order, AppointmentAttribution, Client)
        .join(Client, Client.id == Appointment.client_id)
        .outerjoin(Order, Order.appointment_id == Appointment.id)
        .outerjoin(AppointmentAttribution, AppointmentAttribution.appointment_id == Appointment.id)
        .where(Appointment.starts_at >= start, Appointment.starts_at < end)
        .order_by(Appointment.starts_at)
    ).all()
    out = []
    for a, o, attr, c in rows:
        out.append({
            "appointment_id": a.id, "client_id": a.client_id, "client_source": c.source,
            "starts_at": a.starts_at.isoformat(timespec="minutes"), "status": a.status, "source": a.source,
            "service_id": a.service_id, "master_id": a.master_id, "price": a.price,
            "paid_amount": o.amount if o else None, "salon_earning": o.salon_earning if o else None,
            "attribution": {"channel": attr.channel, **{k: getattr(attr, k) for k in ATTR_FIELDS}} if attr else None,
            "created_at": a.created_at.isoformat(timespec="minutes"),
        })
    return out


def ad_spend(s: Session, date_from: date, date_to: date) -> dict:
    rows = s.scalars(select(Expense).where(
        Expense.category == "advertising", Expense.is_deleted.is_(False),
        Expense.spent_on >= date_from, Expense.spent_on <= date_to).order_by(Expense.spent_on))
    items = [{"spent_on": e.spent_on.isoformat(), "amount": e.amount, "description": e.description} for e in rows]
    return {"total": sum(x["amount"] for x in items), "items": items}
