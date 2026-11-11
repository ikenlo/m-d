from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..errors import AuthError, Forbidden
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
    if not payload or payload.get("p") or payload.get("purpose"):
        raise AuthError("Сесія завершилась, увійдіть знову")
    u = s.get(User, int(payload["sub"]))
    if u is None or not u.is_active or u.token_version != payload.get("tv"):
        raise AuthError("Сесія завершилась, увійдіть знову")
    return u


def get_actor(u: User = Depends(get_user)) -> Actor:
    return Actor.of(u)


def require_finance_pin(request: Request, u: User = Depends(get_user), s: Session = Depends(get_session)) -> User:
    from ..services.security_journal import finance_pin_required

    if not finance_pin_required(s, u):
        return u
    if not u.finance_pin_hash:
        raise Forbidden("Задайте PIN для вкладки «Фінанси» (Профіль)", code="finance_pin_setup")
    token = request.headers.get("X-Finance-Token") or ""
    payload = decode_token(request.app.state.config.secret_key, token) if token else None
    if not payload or payload.get("p") != "fin" or payload.get("sub") != str(u.id) or payload.get("tv") != u.token_version:
        raise Forbidden("Введіть PIN для вкладки «Фінанси»", code="finance_pin_required")
    return u
