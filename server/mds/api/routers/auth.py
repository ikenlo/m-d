from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...errors import AuthError
from ...models import User
from ...security import create_token
from ...services import orders, twofa, users
from ...services.common import Actor
from ..deps import get_actor, get_session, get_user
from ..schemas import BaseModel, ChangePasswordIn, LoginIn

router = APIRouter(prefix="/api/auth", tags=["auth"])


class VerifyIn(BaseModel):
    challenge_id: int
    code: str
    remember: bool = False
    device_label: str | None = None


def _issue(cfg, u: User) -> dict:
    return {"token": create_token(cfg.secret_key, u.id, u.token_version, cfg.token_ttl_hours), "user": users.user_public(u)}


@router.post("/login")
def login(body: LoginIn, request: Request):
    cfg = request.app.state.config
    with request.app.state.db.session() as s:
        u, err = users.authenticate(s, body.username, body.password)
        if u is not None:
            if twofa.applies(s, u) and not twofa.device_ok(s, u, body.device_token):
                ch = twofa.start(s, u)
                data = {"twofa": True, "challenge_id": ch.id, "expires_in": int(twofa.CODE_TTL.total_seconds()),
                        "sent_to": "Telegram"}
            else:
                data = _issue(cfg, u)
    if err:
        raise AuthError(err)
    return data


@router.post("/login/verify")
def login_verify(body: VerifyIn, request: Request):
    cfg = request.app.state.config
    err = None
    with request.app.state.db.session() as s:
        try:
            u = twofa.verify(s, body.challenge_id, body.code)
        except AuthError as e:
            err = e
        else:
            data = _issue(cfg, u)
            if body.remember:
                data["device_token"] = twofa.remember_device(s, u, body.device_label)
    if err:
        raise err
    return data


@router.get("/devices")
def my_devices(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return twofa.list_devices(s, actor)


@router.delete("/devices")
def forget_my_devices(device_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return {"forgotten": twofa.forget_devices(s, actor, device_id)}


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
    return {"code": users.new_telegram_link_code(s, actor, actor.user_id)}
