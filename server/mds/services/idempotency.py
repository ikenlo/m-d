from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, ValidationError
from ..models import IdempotencyKey

TTL = timedelta(days=30)


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def run(s: Session, *, scope: str, user_id: int | None, key: str | None, payload: Any, fn: Callable[[], dict]) -> dict:
    """Той самий ключ і запит → той самий результат; той самий ключ з іншим запитом → 409."""
    if not key:
        return fn()
    if len(key) > 64:
        raise ValidationError("Ключ ідемпотентності задовгий (до 64 символів)")
    full = f"{scope}:{user_id or 0}:{key}"
    h = _hash(payload)
    row = s.get(IdempotencyKey, full)
    if row is not None:
        if row.request_hash != h:
            raise Conflict("Ключ ідемпотентності вже використано для іншого запиту", code="idempotency_conflict")
        if row.response_json is None:
            raise Conflict("Запит з цим ключем ще обробляється", code="idempotency_in_progress")
        return row.response_json
    row = IdempotencyKey(key=full, request_hash=h, response_json=None, created_at=clock.now())
    s.add(row)
    try:
        s.flush()
    except IntegrityError:
        raise Conflict("Запит з цим ключем ще обробляється", code="idempotency_in_progress") from None
    result = fn()
    row.response_json = result
    return result


def cleanup(s: Session, now=None) -> int:
    cut = (now or clock.now()) - TTL
    ids = list(s.scalars(select(IdempotencyKey.key).where(IdempotencyKey.created_at < cut)))
    if ids:
        s.execute(delete(IdempotencyKey).where(IdempotencyKey.key.in_(ids)))
    return len(ids)
