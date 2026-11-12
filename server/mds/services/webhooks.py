"""Черга вхідних вебхуків Meta для MDs Manager (канон D-5, D-6a): Connect лише приймає й зберігає."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import clock
from ..errors import DomainError, Forbidden, NotFound, ValidationError
from ..models import WebhookQueue
from .common import Actor
from .integration import _require

SOURCES = {"instagram": "instagram", "page": "facebook"}
MAX_PULL = 100
MAX_RETRIES = 5
MAX_ERROR_LEN = 2000


def verify_challenge(params: dict, verify_token: str) -> str | None:
    if not verify_token or params.get("hub.mode") != "subscribe":
        return None
    if not hmac.compare_digest((params.get("hub.verify_token") or "").encode(), verify_token.encode()):
        return None
    return params.get("hub.challenge") or ""


def check_signature(raw: bytes, header: str | None, app_secret: str) -> None:
    if not app_secret:
        return
    expected = "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not header or not hmac.compare_digest(header.strip().encode(), expected.encode()):
        raise Forbidden("Невірний підпис вебхука")


def receive(s: Session, actor: Actor, raw: bytes) -> WebhookQueue:
    if actor.role != "system":
        raise Forbidden("Вебхуки приймає лише система")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise DomainError("Тіло вебхука — не JSON", code="bad_json") from None
    if not isinstance(body, dict):
        raise DomainError("Тіло вебхука має бути JSON-об'єктом", code="bad_json")
    row = WebhookQueue(source=SOURCES.get(body.get("object"), "unknown"), payload=body, status="pending",
                       retry_count=0, received_at=clock.now())
    s.add(row)
    s.flush()
    return row


def _public(w: WebhookQueue) -> dict:
    return {"uuid": w.uuid, "source": w.source, "payload": w.payload, "received_at": w.received_at.isoformat()}


def pull(s: Session, actor: Actor, *, limit: int = 50, timeout_minutes: int = 5) -> dict:
    _require(actor)
    if not 1 <= limit <= MAX_PULL:
        raise ValidationError(f"limit — від 1 до {MAX_PULL}")
    now = clock.now()
    s.execute(update(WebhookQueue)
              .where(WebhookQueue.status == "processing",
                     WebhookQueue.processing_started_at < now - timedelta(minutes=timeout_minutes))
              .values(status="pending", processing_started_at=None))
    ids = s.scalars(select(WebhookQueue.id).where(WebhookQueue.status == "pending")
                    .order_by(WebhookQueue.received_at, WebhookQueue.id).limit(limit)).all()
    claimed = []
    for wid in ids:
        # Умова status='pending' в UPDATE: паралельний pull не забере той самий запис удруге.
        res = s.execute(update(WebhookQueue).where(WebhookQueue.id == wid, WebhookQueue.status == "pending")
                        .values(status="processing", processing_started_at=now))
        if res.rowcount == 1:
            claimed.append(wid)
    rows = s.scalars(select(WebhookQueue).where(WebhookQueue.id.in_(claimed))
                     .order_by(WebhookQueue.received_at, WebhookQueue.id)).all() if claimed else []
    items = [_public(w) for w in rows]
    return {"webhooks": items, "count": len(items)}


def ack(s: Session, actor: Actor, uuid: str, status: str, error: str | None = None) -> dict:
    _require(actor)
    if status not in ("completed", "failed"):
        raise ValidationError("status — completed або failed")
    w = s.scalar(select(WebhookQueue).where(WebhookQueue.uuid == uuid))
    if w is None:
        raise NotFound("Вебхук не знайдено")
    if w.status in ("completed", "failed"):
        return {"uuid": w.uuid, "status": w.status}
    now = clock.now()
    if status == "completed":
        w.status, w.processed_at, w.processing_started_at = "completed", now, None
    else:
        w.retry_count += 1
        w.error_message = (error or "")[:MAX_ERROR_LEN] or None
        if w.retry_count >= MAX_RETRIES:
            w.status, w.processed_at = "failed", now
        else:
            w.status, w.processing_started_at = "pending", None
    s.flush()
    return {"uuid": w.uuid, "status": w.status}
