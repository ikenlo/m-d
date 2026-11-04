from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from ...services import bookings, orders
from ...services.common import Actor
from ..deps import get_actor, get_session
from datetime import datetime


from ..schemas import AppointmentIn, AppointmentPatch, BaseModel, CloseOrderIn, StatusIn


class ProposeIn(BaseModel):
    starts_at: datetime

router = APIRouter(prefix="/api", tags=["calendar"])


class ShiftIn(BaseModel):
    master_id: int
    minutes: int
    from_appt_id: int | None = None
    notify_clients: bool = True


@router.post("/appointments/shift")
def shift_appointments(body: ShiftIn, background: BackgroundTasks, actor: Actor = Depends(get_actor),
                       s: Session = Depends(get_session)):
    """Запізнення: зсунути наступні записи майстра на N хв і повідомити клієнтів
    (Telegram — одразу через чергу бота; SMS/Viber — після збереження, якщо TurboSMS налаштовано)."""
    from ...services import sms
    from ...services.common import get_setting

    r = bookings.shift_appointments(s, actor, master_id=body.master_id, minutes=body.minutes,
                                    from_appt_id=body.from_appt_id, notify_clients=body.notify_clients)
    to_sms = r.pop("sms")
    can_sms = bool(get_setting(s, "sms_enabled")) and sms.configured()
    if to_sms and can_sms:
        for x in to_sms:  # кожному свій текст (свій новий час)
            background.add_task(sms.send, [x["phone"]], x["text"])
    r["sms_sent"] = len(to_sms) if can_sms else 0
    r["call"] = [] if can_sms else [x for x in r["shifted"] if x["channel"] == "sms"]  # кому подзвонити
    return r


@router.get("/appointments/unclosed")
def unclosed(master_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    """Час запису минув, а замовлення не закрито (за 7 днів)."""
    return [bookings.appt_public(a) for a in bookings.unclosed(s, actor, master_id)]


@router.get("/appointments")
def list_appointments(
    date_from: date, date_to: date | None = None, master_id: int | None = None,
    actor: Actor = Depends(get_actor), s: Session = Depends(get_session),
):
    items = bookings.list_appointments(s, actor, date_from=date_from, date_to=date_to or date_from, master_id=master_id)
    return [bookings.appt_public(a) for a in items]


@router.get("/availability")
def availability(master_id: int, service_id: int, day: date, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [t.strftime("%H:%M") for t in bookings.free_slots(s, master_id, service_id, day)]


@router.post("/appointments", status_code=201)
def create_appointment(body: AppointmentIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    data = body.model_dump()
    data["starts_at"] = data["starts_at"].replace(tzinfo=None, second=0, microsecond=0)
    return bookings.appt_public(bookings.create_appointment(s, actor, **data))


@router.get("/appointments/pending")
def pending(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    """Непідтверджені записи (майстер — лише свої)."""
    return [bookings.appt_public(a) for a in bookings.pending_list(s, actor)]


@router.post("/appointments/{appt_id}/decline-busy")
def decline_busy(appt_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    """«Час зайнятий»: бот запропонує клієнту найближчі вільні години."""
    return bookings.decline_busy(s, actor, appt_id)


@router.post("/appointments/{appt_id}/propose")
def propose(appt_id: int, body: ProposeIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    """Запропонувати клієнту інший час (клієнт підтверджує в боті)."""
    a, notified = bookings.propose_time(s, actor, appt_id, body.starts_at.replace(tzinfo=None, second=0, microsecond=0))
    return {**bookings.appt_public(a), "notified": notified}


@router.get("/appointments/{appt_id}")
def get_appointment(appt_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return bookings.appt_public(bookings.get_appointment(s, actor, appt_id))


@router.patch("/appointments/{appt_id}")
def update_appointment(appt_id: int, body: AppointmentPatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    data = body.model_dump(exclude_unset=True)
    if data.get("starts_at"):
        data["starts_at"] = data["starts_at"].replace(tzinfo=None, second=0, microsecond=0)
    return bookings.appt_public(bookings.reschedule(s, actor, appt_id, **data))


@router.post("/appointments/{appt_id}/status")
def set_status(appt_id: int, body: StatusIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return bookings.appt_public(bookings.set_status(s, actor, appt_id, body.status, body.reason))


@router.post("/appointments/{appt_id}/close")
def close_order(appt_id: int, body: CloseOrderIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    """Закрити замовлення. Передавайте idempotency_key — повтор не задвоїть оплату."""
    o = orders.close_order(
        s, actor, appt_id, amount=body.amount, payment_method=body.payment_method,
        materials=[m.model_dump() for m in body.materials], idempotency_key=body.idempotency_key,
        client_name=body.client_name,
    )
    return orders.order_public(o)

