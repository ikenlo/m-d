"""Черга повідомлень Telegram (outbox-патерн).

Події записують повідомлення в таблицю `notifications` у тій самій транзакції.
Процес бота періодично забирає непрочитані та надсилає їх (див. bot/outbox.py).
Так повідомлення не губляться, а API не залежить від доступності Telegram.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import Appointment, Notification, Role, User

MAX_ATTEMPTS = 5


def enqueue(s: Session, chat_id: int | None, text: str, buttons: list | None = None, photo_path: str | None = None) -> bool:
    """Поставити повідомлення в чергу. buttons — рядки кнопок [[текст, callback_data], ...].
    Повертає False, якщо в отримувача немає Telegram."""
    if not chat_id:
        return False
    s.add(Notification(chat_id=chat_id, text=text, buttons=buttons, photo_path=photo_path))
    return True


def notify_directors(s: Session, text: str, exclude_user_id: int | None = None, photo_path: str | None = None) -> int:
    """Повідомити всіх директорів з підключеним Telegram. Повертає, скільком поставлено в чергу."""
    n = 0
    for u in s.scalars(select(User).where(User.role == Role.DIRECTOR, User.is_active.is_(True))):
        if u.id != exclude_user_id:
            n += enqueue(s, u.telegram_chat_id, text, photo_path=photo_path)
    return n


def appt_line(a: Appointment) -> str:
    return (
        f"{a.starts_at:%d.%m %H:%M}–{a.ends_at:%H:%M} · {a.service.name}\n"
        f"Майстер: {a.master.full_name}\nКлієнт: {a.client.name}, {a.client.phone or 'без номера'}"
    )


def notify_appointment(s: Session, a: Appointment, title: str, actor_user_id: int | None) -> None:
    """Повідомити директора(ів) і майстра про подію із записом."""
    text = f"{title}\n{appt_line(a)}"
    notify_directors(s, text, exclude_user_id=actor_user_id)
    if a.master.id != actor_user_id:
        enqueue(s, a.master.telegram_chat_id, text)


def pending(s: Session, limit: int = 50) -> list[Notification]:
    return list(
        s.scalars(
            select(Notification)
            .where(Notification.sent_at.is_(None), Notification.attempts < MAX_ATTEMPTS)
            .order_by(Notification.id)
            .limit(limit)
        )
    )


def mark_sent(n: Notification) -> None:
    n.sent_at = clock.now()


def mark_failed(n: Notification, error: str) -> None:
    n.attempts += 1
    n.last_error = error[:500]
