from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services import bookings, orders
from ...services.common import Actor
from ..deps import get_actor, get_session
from datetime import datetime


from ..schemas import AppointmentIn, AppointmentPatch, BaseModel, CloseOrderIn, StatusIn


class ProposeIn(BaseModel):
    starts_at: datetime

router = APIRouter(prefix="/api", tags=["calendar"])


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
