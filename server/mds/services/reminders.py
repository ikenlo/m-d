"""Нагадування клієнтам у Telegram: за добу і за 2 години до візиту.

- Лише клієнтам, які погодились (Client.reminders = True; бот питає після першого запису).
- Лише підтвердженим записам. Перенесення запису скидає позначки — нагадування прийдуть заново.
- Якщо запис зроблено пізніше, ніж за добу (або за 2 год) до візиту, відповідне нагадування
  не надсилається: клієнт щойно записався, нагадувати нема чого.
- Повідомлення йдуть через чергу (outbox), фоновий цикл кличе send_due() кожні 5 хвилин.
- Директор може вимкнути все налаштуванням reminders_enabled.
"""

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
# запас: запис, зроблений за 25 год до візиту, ще отримає нагадування «за добу»
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
    """Поставити в чергу всі нагадування, час яких настав. Повертає кількість."""
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
                a.reminded_day = True  # «за добу» вже неактуальне
                if lead >= TWO_H_MIN_LEAD:
                    sent += notifications.enqueue(s, a.client.telegram_user_id, _text(a, "2h"), _buttons(a))
        elif not a.reminded_day:
            a.reminded_day = True
            if lead >= DAY_MIN_LEAD:
                sent += notifications.enqueue(s, a.client.telegram_user_id, _text(a, "day"), _buttons(a))
    return sent


def should_ask(s: Session, telegram_user_id: int) -> bool:
    """Чи питати клієнта про нагадування (ще не відповідав і функцію ввімкнено)."""
    if not get_setting(s, "reminders_enabled"):
        return False
    c = s.scalar(select(Client).where(Client.telegram_user_id == telegram_user_id))
    return c is not None and c.reminders is None


def set_choice(s: Session, actor: Actor, enabled: bool) -> Client:
    """Клієнт у боті погодився / відмовився від нагадувань."""
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
