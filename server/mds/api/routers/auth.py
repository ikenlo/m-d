from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...errors import AuthError
from ...models import User
from ...security import create_token
from ...services import orders, users
from ...services.common import Actor
from ..deps import get_actor, get_session, get_user
from ..schemas import ChangePasswordIn, LoginIn

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginIn, request: Request):
    cfg = request.app.state.config
    # окрема транзакція: лічильник невдалих спроб має зберегтися навіть при відмові
    with request.app.state.db.session() as s:
        u, err = users.authenticate(s, body.username, body.password)
        if u is not None:
            token = create_token(cfg.secret_key, u.id, u.token_version, cfg.token_ttl_hours)
            data = {"token": token, "user": users.user_public(u)}
    if err:
        raise AuthError(err)
    return data


@router.get("/me")
def me(u: User = Depends(get_user), s: Session = Depends(get_session)):
    out = users.user_public(u)
    if u.role == "master":
        sh = orders.current_shift(s, u.id)
        out["open_shift_id"] = sh.id if sh else None
    return out


@router.post("/change-password")
def change_password(body: ChangePasswordIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    users.change_own_password(s, actor, body.old_password, body.new_password)
    return {"ok": True}


@router.post("/telegram-code")
def my_telegram_code(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    """Код для прив'язки Telegram: надішліть боту /link КОД."""
    return {"code": users.new_telegram_link_code(s, actor, actor.user_id)}
