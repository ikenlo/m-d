from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Forbidden, NotFound
from ..models import Appointment, ApptStatus, Client
from . import notifications
from .common import Actor, audit, get_setting

DAY = timedelta(hours=24)
TWO_H = timedelta(hours=2)
DAY_MIN_LEAD = timedelta(hours=25)
TWO_H_MIN_LEAD = timedelta(hours=3)

ASK_TEXT = "🔔 Нагадати вам про візит у Telegram — за добу і за 2 години?"
ASK_BUTTONS = [[["✅ Так, нагадуйте", "rem:1"], ["Ні, дякую", "rem:0"]]]


def _short(full_name: str) -> str:
    return full_name.split()[0] if full_name else ""


def _text(a: Appointment, kind: str) -> str:
    when = "Завтра" if kind == "day" else "Сьогодні"
    head = "⏰ Нагадуємо про запис" if kind == "day" else "⏰ Чекаємо вас через 2 години"
    return (f"{head}\n{when} о {a.starts_at:%H:%M} · {a.service.name}\n"
            f"Майстер: {_short(a.master.full_name)}\n\n"
            "Якщо плани змінились — перенесіть або скасуйте запис кнопкою нижче.")


def _buttons(a: Appointment) -> list:
    return [[["🔁 Перенести", f"mv:{a.id}"], ["❌ Скасувати", f"cnl:{a.id}"]],
            [["🔕 Більше не нагадувати", "rem:0"]]]


def send_due(s: Session) -> int:
    if not get_setting(s, "reminders_enabled"):
        return 0
    now = clock.now()
    q = (select(Appointment).join(Client, Appointment.client_id == Client.id)
         .where(Appointment.status == ApptStatus.CONFIRMED, Appointment.starts_at > now,
                Appointment.starts_at <= now + DAY,
                Client.reminders.is_(True), Client.telegram_user_id.is_not(None))
         .where((Appointment.reminded_day.is_(False)) | (Appointment.reminded_2h.is_(False))))
    sent = 0
    for a in s.scalars(q):
        left = a.starts_at - now
        lead = a.starts_at - a.created_at
        if left <= TWO_H:
            if not a.reminded_2h:
                a.reminded_2h = True
                a.reminded_day = True
                if lead >= TWO_H_MIN_LEAD:
                    sent += notifications.enqueue(s, a.client.telegram_user_id, _text(a, "2h"), _buttons(a))
        elif not a.reminded_day:
            a.reminded_day = True
            if lead >= DAY_MIN_LEAD:
                sent += notifications.enqueue(s, a.client.telegram_user_id, _text(a, "day"), _buttons(a))
    return sent


def should_ask(s: Session, telegram_user_id: int) -> bool:
    if not get_setting(s, "reminders_enabled"):
        return False
    c = s.scalar(select(Client).where(Client.telegram_user_id == telegram_user_id))
    return c is not None and c.reminders is None


def set_choice(s: Session, actor: Actor, enabled: bool) -> Client:
    if actor.role != "client":
        raise Forbidden("Лише для клієнта")
    c = s.scalar(select(Client).where(Client.telegram_user_id == actor.telegram_user_id))
    if c is None:
        raise NotFound("Спочатку запишіться — тоді зможемо нагадати")
    if c.reminders is not enabled:
        before = c.reminders
        c.reminders = enabled
        audit(s, actor, "client", c.id, "reminders", {"reminders": before}, {"reminders": enabled})
    return c


UNCLOSED_AFTER = timedelta(minutes=15)


def remind_unclosed(s: Session) -> int:
    now = clock.now()
    q = select(Appointment).where(Appointment.status.in_(ApptStatus.CLOSABLE), Appointment.close_reminded.is_(False),
                                  Appointment.ends_at <= now - UNCLOSED_AFTER, Appointment.ends_at >= now - timedelta(days=1))
    n = 0
    for a in s.scalars(q):
        a.close_reminded = True
        n += notifications.enqueue(s, a.master.telegram_chat_id,
                                   f"🧾 Не закрито замовлення: {a.client.name}, {a.starts_at:%H:%M} — {a.service.name}.\n"
                                   "Закрийте його в програмі («Сьогодні» → «Не закрито»), щоб виручка й ваш заробіток порахувались.")
    return n
