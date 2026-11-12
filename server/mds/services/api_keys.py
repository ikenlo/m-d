from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, Forbidden, ValidationError
from ..models import ApiKey, Role
from .common import Actor, audit, get_or_404

SCOPES = {"read": "Лише читання", "write": "Читання і зміни"}
BLOCKED_PREFIXES = (
    "/api/auth", "/api/users", "/api/settings", "/api/api-keys", "/api/backups", "/api/gdrive",
    "/api/employees", "/api/payroll", "/api/perf", "/api/bug-report",
    "/api/clients/purge-no-phone",
)
BLOCKED_SUFFIXES = ("/anonymize", "/merge")
LAST_USED_STEP = timedelta(minutes=5)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def key_public(k: ApiKey) -> dict:
    return {
        "id": k.id, "name": k.name, "prefix": k.prefix, "scope": k.scope, "scope_label": SCOPES.get(k.scope, k.scope),
        "created_by": k.creator.full_name if k.creator else None, "created_at": k.created_at.isoformat(),
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None, "active": k.revoked_at is None,
    }


def _require_person(actor: Actor) -> None:
    actor.require(Role.DIRECTOR)
    if actor.name.startswith("api:"):
        raise Forbidden("Ключами API керують лише через панель")


def create_key(s: Session, actor: Actor, name: str, scope: str) -> tuple[ApiKey, str]:
    _require_person(actor)
    name = " ".join((name or "").split())
    if not (2 <= len(name) <= 80):
        raise ValidationError("Назва ключа — від 2 до 80 символів (напр. «Google Таблиці»)")
    if scope not in SCOPES:
        raise ValidationError("Права ключа: лише читання або читання і зміни")
    raw = "mds_" + secrets.token_urlsafe(32)
    k = ApiKey(name=name, prefix=raw[:12], key_hash=_hash(raw), scope=scope, created_by=actor.user_id, created_at=clock.now())
    s.add(k)
    s.flush()
    audit(s, actor, "api_key", k.id, "create", None, {"name": name, "scope": scope, "prefix": k.prefix})
    return k, raw


def list_keys(s: Session, actor: Actor) -> list[ApiKey]:
    _require_person(actor)
    return list(s.scalars(select(ApiKey).order_by(ApiKey.revoked_at.is_not(None), ApiKey.created_at.desc())))


def revoke_key(s: Session, actor: Actor, key_id: int) -> ApiKey:
    _require_person(actor)
    k: ApiKey = get_or_404(s, ApiKey, key_id, "Ключ")
    if k.revoked_at:
        raise Conflict("Ключ уже відкликано")
    k.revoked_at, k.revoked_by = clock.now(), actor.user_id
    audit(s, actor, "api_key", k.id, "revoke", {"name": k.name, "prefix": k.prefix}, None)
    return k


def authenticate(s: Session, raw: str, method: str, path: str) -> ApiKey:
    from ..errors import AuthError

    k = s.scalar(select(ApiKey).where(ApiKey.key_hash == _hash(raw)))
    if k is None or k.revoked_at is not None:
        raise AuthError("Ключ API недійсний або відкликаний")
    creator = k.creator
    if creator is None or not creator.is_active or creator.role != Role.DIRECTOR:
        raise AuthError("Ключ API недійсний: власник ключа неактивний")
    if path.startswith(BLOCKED_PREFIXES) or path.endswith(BLOCKED_SUFFIXES):
        raise Forbidden("Цей розділ недоступний через ключ API")
    if k.scope == "read" and method not in ("GET", "HEAD"):
        raise Forbidden("Ключ лише для читання")
    now = clock.now()
    if k.last_used_at is None or now - k.last_used_at > LAST_USED_STEP:
        k.last_used_at = now
    return k
