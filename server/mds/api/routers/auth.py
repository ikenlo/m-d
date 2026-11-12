from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...errors import AuthError, DomainError
from ...models import LoginChallenge, User
from ...security import create_scoped_token, create_token
from ...services import orders, twofa, users
from ...services import security_journal as sj
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
    token = create_token(cfg.secret_key, u.id, u.token_version, cfg.token_ttl_hours, idle_minutes=cfg.idle_minutes)
    return {"token": token, "idle_minutes": cfg.idle_minutes, "user": users.user_public(u)}


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def _ua(request: Request) -> str | None:
    return request.headers.get("user-agent")


@router.post("/login")
def login(body: LoginIn, request: Request):
    cfg = request.app.state.config
    ip = client_ip(request)
    err = None
    with request.app.state.db.session() as s:
        try:
            sj.check_ip(s, ip)
        except DomainError as e:
            sj.log_login(s, user=None, username=body.username, ip=ip, user_agent=_ua(request), method="password",
                         success=False, detail="ip_locked")
            err = e
        else:
            u, msg = users.authenticate(s, body.username, body.password)
            if u is None:
                sj.ip_failed(s, ip)
                sj.log_login(s, user=None, username=body.username, ip=ip, user_agent=_ua(request), method="password",
                             success=False, detail=msg)
                err = AuthError(msg)
            elif twofa.applies(s, u) and not twofa.device_ok(s, u, body.device_token):
                ch = twofa.start(s, u)
                sj.log_login(s, user=u, username=u.username, ip=ip, user_agent=_ua(request), method="password",
                             success=True, detail="2fa_challenge")
                data = {"twofa": True, "challenge_id": ch.id, "expires_in": int(twofa.CODE_TTL.total_seconds()),
                        "sent_to": "Telegram"}
            else:
                sj.log_login(s, user=u, username=u.username, ip=ip, user_agent=_ua(request), method="password", success=True)
                data = _issue(cfg, u)
    if err:
        raise err
    return data


@router.post("/login/verify")
def login_verify(body: VerifyIn, request: Request):
    cfg = request.app.state.config
    ip = client_ip(request)
    err = None
    with request.app.state.db.session() as s:
        try:
            sj.check_ip(s, ip)
            u = twofa.verify(s, body.challenge_id, body.code)
        except DomainError as e:
            sj.ip_failed(s, ip)
            ch = s.get(LoginChallenge, body.challenge_id)
            sj.log_login(s, user=s.get(User, ch.user_id) if ch else None, username=None, ip=ip, user_agent=_ua(request),
                         method="2fa", success=False, detail=e.message)
            err = e
        else:
            sj.log_login(s, user=u, username=u.username, ip=ip, user_agent=_ua(request), method="2fa", success=True)
            data = _issue(cfg, u)
            if body.remember:
                data["device_token"] = twofa.remember_device(s, u, body.device_label)
    if err:
        raise err
    return data


@router.post("/refresh")
def refresh(request: Request, u: User = Depends(get_user)):
    from ...security import decode_token

    cfg = request.app.state.config
    payload = decode_token(cfg.secret_key, request.headers.get("authorization", "").removeprefix("Bearer ").strip()) or {}
    token = create_token(cfg.secret_key, u.id, u.token_version, cfg.token_ttl_hours, idle_minutes=cfg.idle_minutes,
                         started=payload.get("st"))
    return {"token": token, "idle_minutes": cfg.idle_minutes}


@router.post("/logout-all")
def logout_all(actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    sj.revoke_sessions(s, actor, actor.user_id)
    return {"ok": True}


class FinancePinIn(BaseModel):
    pin: str
    current_pin: str | None = None


@router.post("/finance-pin")
def set_finance_pin(body: FinancePinIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    sj.set_finance_pin(s, actor, body.pin, body.current_pin)
    return {"ok": True}


@router.post("/finance-verify")
def finance_verify(body: FinancePinIn, request: Request, actor: Actor = Depends(get_actor)):
    cfg = request.app.state.config
    err = None
    with request.app.state.db.session() as s:
        try:
            u = sj.verify_finance_pin(s, actor, body.pin)
        except DomainError as e:
            err = e
        else:
            token = create_scoped_token(cfg.secret_key, u.id, u.token_version, "fin", cfg.token_ttl_hours * 60)
    if err:
        raise err
    return {"finance_token": token}


@router.get("/devices")
def my_devices(actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return twofa.list_devices(s, actor)


@router.delete("/devices")
def forget_my_devices(device_id: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return {"forgotten": twofa.forget_devices(s, actor, device_id)}


@router.get("/me")
def me(u: User = Depends(get_user), s: Session = Depends(get_session, scope="function")):
    out = users.user_public(u)
    if u.role == "master":
        sh = orders.current_shift(s, u.id)
        out["open_shift_id"] = sh.id if sh else None
    return out


@router.post("/change-password")
def change_password(body: ChangePasswordIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    users.change_own_password(s, actor, body.old_password, body.new_password)
    return {"ok": True}


@router.post("/telegram-code")
def my_telegram_code(actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return {"code": users.new_telegram_link_code(s, actor, actor.user_id)}
