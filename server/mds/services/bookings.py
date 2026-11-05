from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import clock
from ..errors import Conflict, Forbidden, NotFound, ValidationError
from ..models import Appointment, ApptStatus, Client, Role, Service, User
from . import notifications
from .catalog import client_public, get_or_create_client
from .common import Actor, audit, get_or_404, get_setting


def appt_public(a: Appointment) -> dict:
    return {
        "id": a.id,
        "client": client_public(a.client),
        "master_id": a.master_id,
        "master_name": a.master.full_name,
        "service_id": a.service_id,
        "service_name": a.service.name,
        "starts_at": a.starts_at.isoformat(),
        "ends_at": a.ends_at.isoformat(),
        "duration_min": int((a.ends_at - a.starts_at).total_seconds() // 60),
        "price": a.price,
        "status": a.status,
        "source": a.source,
        "comment": a.comment,
        "cancel_reason": a.cancel_reason,
        "awaiting_client": bool(a.awaiting_client),
        "order_id": a.order.id if a.order else None,
    }


def _snapshot(a: Appointment) -> dict:
    return {
        "master_id": a.master_id,
        "service_id": a.service_id,
        "starts_at": a.starts_at,
        "ends_at": a.ends_at,
        "price": a.price,
        "status": a.status,
        "comment": a.comment,
    }


def _parse_hm(v: str) -> time:
    h, m = v.split(":")
    return time(int(h), int(m))


def working_hours(s: Session, master: User, day: date) -> tuple[datetime, datetime] | None:
    from .schedule import day_span

    start, end, _ = day_span(s, master, day)
    if not start:
        return None
    return datetime.combine(day, _parse_hm(start)), datetime.combine(day, _parse_hm(end))


EXTENDED_DAY = ("08:00", "21:00")


def staff_hours(s: Session, master: User, day: date) -> tuple[datetime, datetime] | None:
    hours = working_hours(s, master, day)
    if hours is None and day == clock.now().date():
        from .orders import current_shift
        from .schedule import _usual_hours

        if current_shift(s, master.id) is not None:
            start, end = _usual_hours(s, master)
            hours = datetime.combine(day, _parse_hm(start)), datetime.combine(day, _parse_hm(end))
    return hours


def is_off_hours(s: Session, master: User, start: datetime, end: datetime) -> bool:
    hours = staff_hours(s, master, start.date())
    return hours is None or start < hours[0] or end > hours[1]


def booking_options(s: Session, actor: Actor, master_id: int, service_id: int, day: date,
                    duration_min: int | None = None) -> dict:
    if actor.role == "client":
        raise Forbidden("Недостатньо прав")
    _check_access(actor, master_id)
    from .orders import current_shift

    master: User = get_or_404(s, User, master_id, "Майстра")
    service: Service = get_or_404(s, Service, service_id, "Послугу")
    hours = staff_hours(s, master, day)
    dur = timedelta(minutes=duration_min or service.duration_min)
    step = timedelta(minutes=int(get_setting(s, "slot_step_min")))
    ext = [datetime.combine(day, _parse_hm(x)) for x in EXTENDED_DAY]
    lo, hi = (min(ext[0], hours[0]), max(ext[1], hours[1])) if hours else ext
    busy = list(s.scalars(select(Appointment).where(
        Appointment.master_id == master_id, Appointment.status.in_(ApptStatus.OCCUPYING),
        Appointment.starts_at < hi, Appointment.ends_at > lo)))
    earliest = clock.now() - timedelta(minutes=10)
    slots, off = [], []
    t = lo
    while t + dur <= hi:
        if t >= earliest and not any(b.starts_at < t + dur and b.ends_at > t for b in busy):
            inside = hours is not None and t >= hours[0] and t + dur <= hours[1]
            (slots if inside else off).append(t.strftime("%H:%M"))
        t += step
    if not master.is_active:
        slots, off = [], []
    return {
        "slots": slots,
        "off_hours": off,
        "working": hours is not None,
        "hours": [hours[0].strftime("%H:%M"), hours[1].strftime("%H:%M")] if hours else None,
        "shift_open": current_shift(s, master_id) is not None if day == clock.now().date() else None,
    }


def _overlapping(
    s: Session, master_id: int, start: datetime, end: datetime, exclude_id: int | None = None
) -> Appointment | None:
    q = select(Appointment).where(
        Appointment.master_id == master_id,
        Appointment.status.in_(ApptStatus.OCCUPYING),
        Appointment.starts_at < end,
        Appointment.ends_at > start,
    )
    if exclude_id:
        q = q.where(Appointment.id != exclude_id)
    return s.scalar(q.limit(1))


def _lock_master(s: Session, master_id: int) -> None:
    s.execute(update(User).where(User.id == master_id).values(booking_lock=User.booking_lock + 1))


def free_slots(
    s: Session, master_id: int, service_id: int, day: date, *, for_client: bool = False, duration_min: int | None = None
) -> list[datetime]:
    master: User = get_or_404(s, User, master_id, "Майстра")
    service: Service = get_or_404(s, Service, service_id, "Послугу")
    hours = working_hours(s, master, day)
    if hours is None or not master.is_active:
        return []
    start, end = hours
    step = timedelta(minutes=int(get_setting(s, "slot_step_min")))
    dur = timedelta(minutes=duration_min or service.duration_min)
    earliest = clock.now()
    if for_client:
        earliest += timedelta(minutes=int(get_setting(s, "booking_min_lead_min")))
    busy = list(
        s.scalars(
            select(Appointment).where(
                Appointment.master_id == master_id,
                Appointment.status.in_(ApptStatus.OCCUPYING),
                Appointment.starts_at < end,
                Appointment.ends_at > start,
            )
        )
    )
    out = []
    t = start
    while t + dur <= end:
        if t >= earliest and not any(b.starts_at < t + dur and b.ends_at > t for b in busy):
            out.append(t)
        t += step
    return out


def _check_access(actor: Actor, master_id: int) -> None:
    if actor.is_staff_manager:
        return
    if actor.is_master and actor.user_id == master_id:
        return
    raise Forbidden("Можна працювати лише з власними записами")


def _check_slot(s: Session, master: User, start: datetime, end: datetime, exclude_id: int | None, strict_hours: bool):
    if end <= start:
        raise ValidationError("Невірна тривалість")
    if strict_hours:
        hours = working_hours(s, master, start.date())
        if hours is None or start < hours[0] or end > hours[1]:
            raise ValidationError("Час поза графіком роботи майстра")
    _lock_master(s, master.id)
    clash = _overlapping(s, master.id, start, end, exclude_id)
    if clash:
        raise Conflict(
            f"Майстер зайнятий: {clash.starts_at:%H:%M}–{clash.ends_at:%H:%M} вже є запис",
            code="slot_taken",
        )


def create_appointment(
    s: Session,
    actor: Actor,
    *,
    master_id: int,
    service_id: int,
    starts_at: datetime,
    client_id: int | None = None,
    client_name: str | None = None,
    client_phone: str | None = None,
    duration_min: int | None = None,
    price: int | None = None,
    comment: str | None = None,
    status: str | None = None,
    confirm_off_hours: bool = False,
) -> Appointment:
    if actor.role != "client":
        _check_access(actor, master_id)
    master: User = get_or_404(s, User, master_id, "Майстра")
    if master.role != Role.MASTER or not master.is_active:
        raise ValidationError("Обраний співробітник не є активним майстром")
    service: Service = get_or_404(s, Service, service_id, "Послугу")
    if not service.is_active:
        raise ValidationError("Послуга недоступна")
    from_bot = actor.role == "client"
    source = "telegram" if from_bot else "staff"
    if from_bot and starts_at < clock.now():
        raise ValidationError("Не можна записатися на минулий час")

    if client_id:
        client = get_or_404(s, Client, client_id, "Клієнта")
    else:
        if not client_phone and from_bot:
            raise ValidationError("Вкажіть телефон клієнта")
        client = get_or_create_client(
            s, actor, client_name or "", client_phone, telegram_user_id=actor.telegram_user_id
        )

    if from_bot:
        from . import antispam

        antispam.check_booking(s, client, source)
    dur = duration_min or service.duration_min
    if price is not None and price < 0:
        raise ValidationError("Ціна не може бути від'ємною")
    ends_at = starts_at + timedelta(minutes=dur)
    if actor.is_master and not confirm_off_hours and is_off_hours(s, master, starts_at, ends_at):
        raise Conflict("Цей час поза графіком роботи. Підтвердіть запис у неробочий час", code="off_hours")
    _check_slot(s, master, starts_at, ends_at, None, strict_hours=from_bot)

    if status is None:
        if from_bot:
            auto = get_setting(s, "telegram_auto_confirm") and source == "telegram" and not antispam.needs_confirmation(s, client)
            status = ApptStatus.CONFIRMED if auto else ApptStatus.PENDING
        else:
            status = ApptStatus.CONFIRMED
    if status not in (ApptStatus.PENDING, ApptStatus.CONFIRMED):
        raise ValidationError("Новий запис може бути лише 'очікує' або 'підтверджено'")

    a = Appointment(
        client=client,
        master=master,
        service=service,
        starts_at=starts_at,
        ends_at=ends_at,
        price=service.price if price is None else price,
        status=status,
        source=source,
        comment=comment,
        created_by=actor.user_id,
    )
    s.add(a)
    s.flush()
    audit(s, actor, "appointment", a.id, "create", None, _snapshot(a))
    notifications.notify_appointment(s, a, "🆕 Новий запис", actor.user_id)
    return a


def get_appointment(s: Session, actor: Actor, appt_id: int) -> Appointment:
    a = s.get(Appointment, appt_id)
    if a is None:
        raise NotFound(f"Запис #{appt_id} не знайдено")
    if actor.role == "client":
        if a.client.telegram_user_id != actor.telegram_user_id:
            raise NotFound(f"Запис #{appt_id} не знайдено")
    else:
        _check_access(actor, a.master_id)
    return a


def reschedule(
    s: Session,
    actor: Actor,
    appt_id: int,
    *,
    starts_at: datetime | None = None,
    master_id: int | None = None,
    service_id: int | None = None,
    duration_min: int | None = None,
    price: int | None = None,
    comment: str | None = None,
) -> Appointment:
    a = get_appointment(s, actor, appt_id)
    if a.status not in ApptStatus.EDITABLE:
        raise Conflict("Цей запис уже не можна змінити")
    before = _snapshot(a)
    from_bot = actor.role == "client"

    new_master = a.master
    if master_id and master_id != a.master_id:
        if from_bot:
            raise Forbidden("Зміна майстра — через адміністратора")
        _check_access(actor, master_id)
        new_master = get_or_404(s, User, master_id, "Майстра")
        if new_master.role != Role.MASTER or not new_master.is_active:
            raise ValidationError("Обраний співробітник не є активним майстром")
    if service_id and service_id != a.service_id:
        if from_bot:
            raise Forbidden("Зміна послуги — через адміністратора")
        a.service = get_or_404(s, Service, service_id, "Послугу")
        if duration_min is None:
            duration_min = a.service.duration_min
        if price is None:
            price = a.service.price
    new_start = starts_at or a.starts_at
    if from_bot and new_start < clock.now():
        raise ValidationError("Не можна перенести на минулий час")
    dur = timedelta(minutes=duration_min) if duration_min else (a.ends_at - a.starts_at)
    new_end = new_start + dur
    time_changed = new_start != a.starts_at or new_end != a.ends_at or new_master.id != a.master_id
    if time_changed:
        _check_slot(s, new_master, new_start, new_end, a.id, strict_hours=from_bot)
    old_master = a.master
    a.master = new_master
    if new_start != a.starts_at:
        a.reminded_day = a.reminded_2h = False
    a.starts_at, a.ends_at = new_start, new_end
    if price is not None:
        if price < 0:
            raise ValidationError("Ціна не може бути від'ємною")
        a.price = price
    if comment is not None:
        a.comment = comment
    if from_bot:
        a.awaiting_client = False
    if from_bot and a.status == ApptStatus.CONFIRMED and time_changed:
        a.status = ApptStatus.CONFIRMED if get_setting(s, "telegram_auto_confirm") else ApptStatus.PENDING
    s.flush()
    audit(s, actor, "appointment", a.id, "update", before, _snapshot(a))
    if time_changed:
        notifications.notify_appointment(
            s, a, f"🔁 Запис перенесено (було {before['starts_at']:%d.%m %H:%M})", actor.user_id
        )
        if old_master.id != a.master_id and old_master.id != actor.user_id:
            notifications.enqueue(
                s, old_master.telegram_chat_id,
                f"↪️ Запис {before['starts_at']:%d.%m %H:%M} передано іншому майстру",
            )
    return a


def set_status(
    s: Session, actor: Actor, appt_id: int, status: str, reason: str | None = None, *, notify_client: bool = True
) -> Appointment:
    a = get_appointment(s, actor, appt_id)
    allowed = {
        ApptStatus.CONFIRMED: (ApptStatus.PENDING,),
        ApptStatus.IN_PROGRESS: (ApptStatus.PENDING, ApptStatus.CONFIRMED),
        ApptStatus.CANCELLED: ApptStatus.EDITABLE,
        ApptStatus.NO_SHOW: (ApptStatus.PENDING, ApptStatus.CONFIRMED),
    }
    if status not in allowed:
        raise ValidationError("Недопустимий статус")
    if actor.role == "client" and status != ApptStatus.CANCELLED:
        raise Forbidden("Клієнт може лише скасувати запис")
    if a.status not in allowed[status]:
        raise Conflict(f"Неможливо змінити статус з '{a.status}' на '{status}'")
    before = _snapshot(a)
    a.status = status
    a.awaiting_client = False
    if status == ApptStatus.CANCELLED:
        a.cancel_reason = reason or ("Скасовано клієнтом" if actor.role == "client" else None)
    s.flush()
    audit(s, actor, "appointment", a.id, status, before, _snapshot(a), reason)
    if status == ApptStatus.CANCELLED:
        notifications.notify_appointment(s, a, "❌ Запис скасовано", actor.user_id)
        if actor.role != "client" and notify_client:
            notifications.enqueue(
                s, a.client.telegram_user_id,
                f"❌ Ваш запис {a.starts_at:%d.%m о %H:%M} ({a.service.name}) скасовано салоном.",
            )
    elif status == ApptStatus.CONFIRMED:
        notifications.enqueue(
            s, a.client.telegram_user_id,
            f"✅ Ваш запис підтверджено: {a.starts_at:%d.%m о %H:%M}, {a.service.name}, майстер {a.master.full_name}.",
        )
    return a


def list_appointments(
    s: Session,
    actor: Actor,
    *,
    date_from: date,
    date_to: date,
    master_id: int | None = None,
    include_cancelled: bool = True,
) -> list[Appointment]:
    if actor.is_master:
        master_id = actor.user_id
    elif not actor.is_staff_manager:
        raise Forbidden("Недостатньо прав")
    q = (
        select(Appointment)
        .options(joinedload(Appointment.client), joinedload(Appointment.master), joinedload(Appointment.service),
                 selectinload(Appointment.order))
        .where(
            Appointment.starts_at >= datetime.combine(date_from, time.min),
            Appointment.starts_at < datetime.combine(date_to + timedelta(days=1), time.min),
        )
    )
    if master_id:
        q = q.where(Appointment.master_id == master_id)
    if not include_cancelled:
        q = q.where(Appointment.status.in_(ApptStatus.OCCUPYING))
    return list(s.scalars(q.order_by(Appointment.starts_at)).unique())


def client_history(s: Session, client_id: int) -> list[Appointment]:
    return list(
        s.scalars(
            select(Appointment).where(Appointment.client_id == client_id).order_by(Appointment.starts_at.desc())
        )
    )


def upcoming_for_telegram(s: Session, tg_user_id: int) -> list[Appointment]:
    return list(
        s.scalars(
            select(Appointment)
            .join(Client)
            .where(
                Client.telegram_user_id == tg_user_id,
                Appointment.status.in_((ApptStatus.PENDING, ApptStatus.CONFIRMED)),
                Appointment.starts_at >= clock.now(),
            )
            .order_by(Appointment.starts_at)
        )
    )


def free_slots_any(s: Session, service_id: int, day: date, *, for_client: bool = False) -> dict[datetime, list[int]]:
    from .users import masters_for_service

    out: dict[datetime, list[int]] = {}
    for m in masters_for_service(s, service_id):
        for t in free_slots(s, m.id, service_id, day, for_client=for_client):
            out.setdefault(t, []).append(m.id)
    return dict(sorted(out.items()))


def free_windows(
    s: Session, master_id: int, day: date, *, for_client: bool = True, min_minutes: int = 30
) -> list[tuple[datetime, datetime]]:
    master: User = get_or_404(s, User, master_id, "Майстра")
    hours = working_hours(s, master, day)
    if hours is None or not master.is_active:
        return []
    start, end = hours
    earliest = clock.now()
    if for_client:
        earliest += timedelta(minutes=int(get_setting(s, "booking_min_lead_min")))
    cur = max(start, _ceil_to_step(earliest, int(get_setting(s, "slot_step_min")))) if earliest > start else start
    busy = sorted(
        s.scalars(
            select(Appointment).where(
                Appointment.master_id == master_id,
                Appointment.status.in_(ApptStatus.OCCUPYING),
                Appointment.starts_at < end,
                Appointment.ends_at > start,
            )
        ),
        key=lambda a: a.starts_at,
    )
    out = []
    for b in busy:
        if b.starts_at > cur:
            out.append((cur, min(b.starts_at, end)))
        cur = max(cur, b.ends_at)
    if cur < end:
        out.append((cur, end))
    return [(a, b) for a, b in out if (b - a) >= timedelta(minutes=min_minutes)]


def _ceil_to_step(t: datetime, step_min: int) -> datetime:
    base = datetime.combine(t.date(), time.min)
    mins = int((t - base).total_seconds() // 60)
    if (t - base).total_seconds() % 60:
        mins += 1
    mins = -(-mins // step_min) * step_min
    return base + timedelta(minutes=mins)


def pending_list(s: Session, actor: Actor) -> list[Appointment]:
    q = select(Appointment).where(
        Appointment.status == ApptStatus.PENDING,
        Appointment.starts_at >= datetime.combine(clock.now().date(), time.min),
    )
    if actor.is_master:
        q = q.where(Appointment.master_id == actor.user_id)
    elif not actor.is_staff_manager:
        raise Forbidden("Недостатньо прав")
    return list(s.scalars(q.order_by(Appointment.starts_at)))


def suggest_slots(
    s: Session, master_id: int, service_id: int, limit: int = 6, exclude: tuple[datetime, datetime] | None = None
) -> list[datetime]:
    out: list[datetime] = []
    today = clock.now().date()
    for i in range(int(get_setting(s, "booking_horizon_days"))):
        day_picks: list[datetime] = []
        for t in free_slots(s, master_id, service_id, today + timedelta(days=i), for_client=True):
            if exclude and exclude[0] <= t < exclude[1]:
                continue
            if not day_picks or (len(day_picks) < 2 and t - day_picks[-1] >= timedelta(hours=3)):
                day_picks.append(t)
        for t in day_picks:
            out.append(t)
            if len(out) >= limit:
                return out
    return out


def _when(t: datetime) -> str:
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
    return f"{days[t.weekday()]} {t:%d.%m} о {t:%H:%M}"


def decline_busy(s: Session, actor: Actor, appt_id: int) -> dict:
    actor.require(Role.DIRECTOR, Role.ADMIN, Role.MASTER)
    a = get_appointment(s, actor, appt_id)
    if a.status != ApptStatus.PENDING:
        raise Conflict("Запис уже не очікує підтвердження")
    set_status(s, actor, appt_id, ApptStatus.CANCELLED, "Час зайнятий", notify_client=False)
    slots = suggest_slots(s, a.master_id, a.service_id, exclude=(a.starts_at, a.ends_at))
    buttons = [[[_when(t), f"rb:{a.id}:{t:%Y%m%d%H%M}"]] for t in slots]
    buttons.append([["📅 Обрати інший день або майстра", "book"]])
    text = (f"😔 На жаль, час {_when(a.starts_at)} ({a.service.name}) уже зайнятий.\n"
            + ("Оберіть, будь ласка, інший вільний час:" if slots else "Оберіть інший день або майстра:"))
    notified = notifications.enqueue(s, a.client.telegram_user_id, text, buttons)
    return {"notified": notified, "slots": [t.isoformat() for t in slots]}


def propose_time(s: Session, actor: Actor, appt_id: int, starts_at: datetime) -> tuple[Appointment, bool]:
    actor.require(Role.DIRECTOR, Role.ADMIN, Role.MASTER)
    a = get_appointment(s, actor, appt_id)
    if a.status != ApptStatus.PENDING:
        raise Conflict("Запис уже не очікує підтвердження")
    old = a.starts_at
    a = reschedule(s, actor, appt_id, starts_at=starts_at)
    a.awaiting_client = True
    audit(s, actor, "appointment", a.id, "propose_time", {"starts_at": old}, {"starts_at": a.starts_at})
    text = (f"🕐 Майстер {a.master.full_name.split()[0]} пропонує інший час для запису «{a.service.name}»:\n"
            f"{_when(a.starts_at)} (замість {_when(old)}).\nПідходить?")
    buttons = [[["✅ Підходить", f"acc:{a.id}"]], [["🕐 Інший час", f"mv:{a.id}"]], [["❌ Скасувати запис", f"cnl:{a.id}"]]]
    notified = notifications.enqueue(s, a.client.telegram_user_id, text, buttons)
    return a, notified


def client_accept(s: Session, actor: Actor, appt_id: int) -> Appointment:
    if actor.role != "client":
        raise Forbidden("Лише для клієнта")
    a = get_appointment(s, actor, appt_id)
    if a.status != ApptStatus.PENDING or not a.awaiting_client:
        raise Conflict("Ця пропозиція вже неактуальна")
    before = _snapshot(a)
    a.status, a.awaiting_client = ApptStatus.CONFIRMED, False
    s.flush()
    audit(s, actor, "appointment", a.id, "client_accept", before, _snapshot(a))
    notifications.notify_appointment(s, a, "✅ Клієнт погодився на запропонований час", None)
    return a


def client_rebook(s: Session, actor: Actor, old_appt_id: int, starts_at: datetime) -> Appointment:
    if actor.role != "client":
        raise Forbidden("Лише для клієнта")
    old = get_appointment(s, actor, old_appt_id)
    if old.status != ApptStatus.CANCELLED:
        raise Conflict("Ця пропозиція вже неактуальна")
    dup = s.scalar(select(Appointment.id).where(
        Appointment.client_id == old.client_id, Appointment.master_id == old.master_id,
        Appointment.service_id == old.service_id, Appointment.starts_at >= clock.now(),
        Appointment.status.in_((ApptStatus.PENDING, ApptStatus.CONFIRMED)),
    ))
    if dup:
        raise Conflict("Ви вже записані — новий запис не потрібен. Перенести його можна в меню бота.")
    return create_appointment(s, actor, master_id=old.master_id, service_id=old.service_id,
                              starts_at=starts_at, client_id=old.client_id)


def favorite_master(s: Session, client_id: int, service_id: int | None = None) -> tuple[User, int] | None:
    from sqlalchemy import func

    from .users import masters_for_service

    need = int(get_setting(s, "favorite_master_visits"))
    rows = s.execute(
        select(Appointment.master_id, func.count(Appointment.id).label("n"))
        .where(Appointment.client_id == client_id, Appointment.status == ApptStatus.COMPLETED)
        .group_by(Appointment.master_id)
        .order_by(func.count(Appointment.id).desc())
    ).all()
    allowed = {m.id for m in masters_for_service(s, service_id)} if service_id else None
    for master_id, n in rows:
        if n < need:
            break
        m = s.get(User, master_id)
        if m and m.is_active and m.role == Role.MASTER and (allowed is None or m.id in allowed):
            return m, int(n)
    return None


def shift_appointments(s: Session, actor: Actor, *, master_id: int, minutes: int, from_appt_id: int | None = None,
                       notify_clients: bool = True) -> dict:
    _check_access(actor, master_id)
    if not (5 <= int(minutes) <= 180):
        raise ValidationError("Зсув — від 5 до 180 хвилин")
    now = clock.now()
    delta = timedelta(minutes=int(minutes))
    q = select(Appointment).where(
        Appointment.master_id == master_id, Appointment.status.in_((ApptStatus.PENDING, ApptStatus.CONFIRMED)),
        Appointment.starts_at < datetime.combine(now.date() + timedelta(days=1), time.min))
    if from_appt_id:
        first = get_or_404(s, Appointment, from_appt_id, "Запис")
        if first.master_id != master_id:
            raise ValidationError("Запис іншого майстра")
        q = q.where(Appointment.starts_at >= first.starts_at)
    else:
        q = q.where(Appointment.starts_at >= now)
    _lock_master(s, master_id)
    items = list(s.scalars(q.order_by(Appointment.starts_at)))
    if not items:
        raise ValidationError("Немає наступних записів, які можна зсунути")
    shifted, sms, no_contact, telegram = [], [], [], 0
    for a in reversed(items):
        before = _snapshot(a)
        a.starts_at += delta
        a.ends_at += delta
        a.reminded_2h = a.reminded_2h and a.starts_at - now <= timedelta(hours=2)
        audit(s, actor, "appointment", a.id, "shift", before, _snapshot(a), f"Зсув на {minutes} хв")
    s.flush()
    for a in items:
        text = (f"⏰ Салон M&D: ваш запис на {a.service.name} перенесено на {int(minutes)} хв — "
                f"тепер {a.starts_at:%d.%m} о {a.starts_at:%H:%M}. Вибачте за незручності! Якщо час не підходить — напишіть або зателефонуйте.")
        row = {"id": a.id, "client": a.client.name, "phone": a.client.phone, "starts_at": a.starts_at.isoformat(),
               "service": a.service.name, "channel": None}
        if notify_clients and a.client.telegram_user_id:
            notifications.enqueue(s, a.client.telegram_user_id, text)
            row["channel"] = "telegram"
            telegram += 1
        elif notify_clients and a.client.phone:
            sms.append({"phone": a.client.phone, "text": text, "client_id": a.client_id})
            row["channel"] = "sms"
        else:
            no_contact.append(row)
        shifted.append(row)
    master = s.get(User, master_id)
    notifications.notify_directors(s, f"⏰ {master.full_name}: записи зсунуто на {minutes} хв ({len(items)}), "
                                      f"з {items[0].starts_at:%H:%M}", exclude_user_id=actor.user_id)
    return {"shifted": shifted, "telegram": telegram, "sms": sms, "minutes": int(minutes)}


def unclosed(s: Session, actor: Actor, master_id: int | None = None) -> list[Appointment]:
    if actor.is_master:
        master_id = actor.user_id
    elif not actor.is_staff_manager:
        raise Forbidden("Недостатньо прав")
    now = clock.now()
    q = select(Appointment).where(Appointment.status.in_(ApptStatus.CLOSABLE), Appointment.ends_at <= now,
                                  Appointment.starts_at >= now - timedelta(days=7))
    if master_id:
        q = q.where(Appointment.master_id == master_id)
    return list(s.scalars(q.order_by(Appointment.starts_at)))
