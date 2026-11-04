"""Живе оновлення панелі: що змінилось з останньої перевірки (за журналом змін audit_log).

Кожна важлива зміна пишеться в audit_log, тож його останній id — «версія» даних. Панель питає раз на ~15 с:
якщо змінилось те, що показує відкрита сторінка (записи, замовлення…), вона сама перемальовується,
а про нові записи (з бота, сайту чи від колег) показує сповіщення.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Appointment, AuditLog, Role
from .common import Actor

MAX_NEW = 10


def changes(s: Session, actor: Actor, since: int | None) -> dict:
    actor.require(Role.DIRECTOR, Role.ADMIN, Role.MASTER)
    rev = int(s.scalar(select(func.max(AuditLog.id))) or 0)
    out = {"rev": rev, "entities": [], "new": []}
    if since is None or rev <= since:
        return out
    if rev - since > 5000:  # давно не перевіряли — просто оновити все
        out["entities"] = ["*"]
        return out
    rows = s.execute(select(AuditLog.entity, AuditLog.entity_id, AuditLog.action, AuditLog.user_id)
                     .where(AuditLog.id > since).order_by(AuditLog.id)).all()
    # власні дії користувача сторінка вже показала — їх не рахуємо
    others = [r for r in rows if r.user_id != actor.user_id]
    out["entities"] = sorted({r.entity for r in others})
    new_ids = [r.entity_id for r in others if r.entity == "appointment" and r.action == "create"][-MAX_NEW:]
    for a in s.scalars(select(Appointment).where(Appointment.id.in_(new_ids)).order_by(Appointment.starts_at)) if new_ids else []:
        if actor.is_master and a.master_id != actor.user_id:
            continue  # майстер бачить лише свої записи
        out["new"].append({"id": a.id, "client_name": a.client.name, "starts_at": a.starts_at.isoformat(),
                           "service_name": a.service.name, "master_name": a.master.full_name, "source": a.source,
                           "status": a.status})
    return out
