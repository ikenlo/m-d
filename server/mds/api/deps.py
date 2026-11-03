"""Залежності FastAPI: сесія БД, поточний користувач, перевірка ролей."""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..errors import AuthError
from ..models import User
from ..security import decode_token
from ..services.common import Actor

bearer = HTTPBearer(auto_error=False)


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.db.session() as s:
        yield s


def get_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    s: Session = Depends(get_session),
) -> User:
    if creds is None:
        raise AuthError("Потрібна авторизація")
    payload = decode_token(request.app.state.config.secret_key, creds.credentials)
    if not payload:
        raise AuthError("Сесія завершилась, увійдіть знову")
    u = s.get(User, int(payload["sub"]))
    if u is None or not u.is_active or u.token_version != payload.get("tv"):
        raise AuthError("Сесія завершилась, увійдіть знову")
    return u


def get_actor(u: User = Depends(get_user)) -> Actor:
    return Actor.of(u)
