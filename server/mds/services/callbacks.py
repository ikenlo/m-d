"""«Швидкий запис»: заявки клієнтів на зворотний дзвінок."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict
from ..models import CallbackRequest, Role
from . import notifications
from .catalog import normalize_phone
from .common import Actor, audit, get_or_404

REPEAT_WINDOW = timedelta(hours=1)


def callback_public(r: CallbackRequest) -> dict:
    return {
        "id": r.id,
        "phone": r.phone,
        "name": r.name,
        "topic": r.topic,
        "source": r.source,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
        "handled_at": r.handled_at.isoformat() if r.handled_at else None,
    }


def create_request(s: Session, actor: Actor, phone: str, name: str | None = None, topic: str | None = None,
                   source: str = "telegram") -> CallbackRequest:
    """Клієнт просить передзвонити (Telegram-бот або швидка заявка на сайті). Повторна заявка з того ж
    Telegram/номера протягом години не створюється (захист від спаму), а повертається вже наявна
    (тему з нової заявки дописуємо, якщо її ще не було)."""
    phone_n = normalize_phone(phone)
    since = clock.now() - REPEAT_WINDOW
    q = select(CallbackRequest).where(CallbackRequest.status == "new", CallbackRequest.created_at >= since)
    if actor.telegram_user_id:
        q = q.where((CallbackRequest.phone == phone_n) | (CallbackRequest.telegram_user_id == actor.telegram_user_id))
    else:
        q = q.where(CallbackRequest.phone == phone_n)
    existing = s.scalar(q.limit(1))
    if existing:
        if topic and not existing.topic:
            existing.topic = topic.strip()[:160]
        return existing
    from .antispam import check_callback

    check_callback(s, phone_n, actor.telegram_user_id)
    r = CallbackRequest(phone=phone_n, name=(name or "").strip() or None, topic=(topic or "").strip()[:160] or None,
                        source=source, telegram_user_id=actor.telegram_user_id, created_at=clock.now())
    s.add(r)
    s.flush()
    audit(s, actor, "callback", r.id, "create", None, callback_public(r))
    where = "заявка з сайту" if source == "site" else "просять передзвонити"
    notifications.notify_directors(
        s,
        f"📞 Швидкий запис: {where}\n{r.name or 'Клієнт'}, {r.phone}\n"
        + (f"{r.topic}\n" if r.topic else "")
        + f"{r.created_at:%d.%m %H:%M}",
    )
    return r


def list_requests(s: Session, actor: Actor, status: str | None = "new", limit: int = 50) -> list[CallbackRequest]:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    q = select(CallbackRequest)
    if status:
        q = q.where(CallbackRequest.status == status)
    return list(s.scalars(q.order_by(CallbackRequest.created_at.desc()).limit(limit)))


def mark_done(s: Session, actor: Actor, request_id: int) -> CallbackRequest:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    r: CallbackRequest = get_or_404(s, CallbackRequest, request_id, "Заявку")
    if r.status == "done":
        raise Conflict("Заявку вже опрацьовано")
    r.status, r.handled_at, r.handled_by = "done", clock.now(), actor.user_id
    audit(s, actor, "callback", r.id, "done")
    return r
