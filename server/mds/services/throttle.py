from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import AuthThrottle

MAX_FAILS = 5
LOCK = timedelta(minutes=15)


def _row(s: Session, scope: str, key) -> AuthThrottle | None:
    return s.scalar(select(AuthThrottle).where(AuthThrottle.scope == scope, AuthThrottle.key == str(key)))


def is_locked(s: Session, scope: str, key) -> bool:
    r = _row(s, scope, key)
    return bool(r and r.locked_until and r.locked_until > clock.now())


def register_fail(s: Session, scope: str, key, max_fails: int = MAX_FAILS, lock: timedelta = LOCK) -> bool:
    now = clock.now()
    r = _row(s, scope, key)
    if r is None:
        r = AuthThrottle(scope=scope, key=str(key), fails=0, updated_at=now)
        s.add(r)
    if r.locked_until and r.locked_until <= now:
        r.locked_until = None
        r.fails = 0
    r.fails += 1
    r.updated_at = now
    if r.fails >= max_fails:
        r.locked_until = now + lock
        r.fails = 0
    s.flush()
    return bool(r.locked_until and r.locked_until > now)


def clear(s: Session, scope: str, key) -> None:
    r = _row(s, scope, key)
    if r is not None:
        r.fails = 0
        r.locked_until = None
        r.updated_at = clock.now()
