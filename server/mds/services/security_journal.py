from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import AuthError, Forbidden, TooManyRequests, ValidationError
from ..models import LoginEvent, Role, TrustedDevice, User
from ..security import hash_password, verify_password
from .common import Actor, audit, get_or_404, get_setting
from . import throttle

KEEP = timedelta(days=90)
IP_MAX_FAILS = 20


def log_login(s: Session, *, user: User | None, username: str | None, ip: str | None, user_agent: str | None,
              method: str, success: bool, detail: str | None = None) -> LoginEvent:
    ev = LoginEvent(user_id=user.id if user else None, username=(username or (user.username if user else None) or "")[:64],
                    ip=(ip or "")[:64] or None, user_agent=(user_agent or "")[:255] or None, method=method, success=success,
                    detail=(detail or "")[:128] or None, ts=clock.now())
    s.add(ev)
    return ev


def check_ip(s: Session, ip: str | None) -> None:
    if ip and throttle.is_locked(s, "ip", ip):
        raise TooManyRequests("Забагато невдалих спроб з цієї адреси. Спробуйте через 15 хвилин.")


def ip_failed(s: Session, ip: str | None) -> None:
    if ip:
        throttle.register_fail(s, "ip", ip, max_fails=IP_MAX_FAILS)


def event_public(e: LoginEvent, names: dict) -> dict:
    return {"id": e.id, "ts": e.ts.isoformat(), "user_id": e.user_id, "user_name": names.get(e.user_id) or e.username,
            "ip": e.ip, "user_agent": e.user_agent, "method": e.method, "success": e.success, "detail": e.detail}


def list_logins(s: Session, actor: Actor, *, user_id: int | None = None, success: bool | None = None,
                limit: int = 200) -> list[dict]:
    actor.require(Role.DIRECTOR)
    q = select(LoginEvent)
    if user_id:
        q = q.where(LoginEvent.user_id == user_id)
    if success is not None:
        q = q.where(LoginEvent.success.is_(success))
    names = dict(s.execute(select(User.id, User.full_name)).all())
    return [event_public(e, names) for e in s.scalars(q.order_by(LoginEvent.id.desc()).limit(min(limit, 1000)))]


def cleanup(s: Session) -> int:
    cut = clock.now() - KEEP
    return s.execute(delete(LoginEvent).where(LoginEvent.ts < cut).execution_options(synchronize_session=False)).rowcount


def revoke_sessions(s: Session, actor: Actor, user_id: int) -> None:
    if actor.user_id != user_id:
        actor.require(Role.DIRECTOR)
    u: User = get_or_404(s, User, user_id, "Користувача")
    u.token_version += 1
    s.execute(delete(TrustedDevice).where(TrustedDevice.user_id == u.id).execution_options(synchronize_session=False))
    audit(s, actor, "user", u.id, "revoke_sessions")


def finance_pin_required(s: Session, u: User) -> bool:
    return u.role == Role.DIRECTOR and bool(get_setting(s, "finance_pin_enabled"))


def _check_pin_format(pin: str) -> str:
    pin = str(pin or "").strip()
    if not (pin.isdigit() and 4 <= len(pin) <= 8):
        raise ValidationError("PIN — від 4 до 8 цифр")
    return pin


def set_finance_pin(s: Session, actor: Actor, pin: str, current: str | None = None) -> None:
    actor.require(Role.DIRECTOR)
    u: User = get_or_404(s, User, actor.user_id or 0, "Користувача")
    if u.finance_pin_hash and not (current and verify_password(str(current).strip(), u.finance_pin_hash)):
        raise ValidationError("Вкажіть поточний PIN")
    u.finance_pin_hash = hash_password(_check_pin_format(pin))
    audit(s, actor, "user", u.id, "set_finance_pin")


def verify_finance_pin(s: Session, actor: Actor, pin: str) -> User:
    actor.require(Role.DIRECTOR)
    u: User = get_or_404(s, User, actor.user_id or 0, "Користувача")
    if not u.finance_pin_hash:
        raise ValidationError("Спершу задайте PIN для вкладки «Фінанси»")
    if throttle.is_locked(s, "fin_pin", u.id):
        raise TooManyRequests("Забагато невдалих спроб PIN. Спробуйте через 15 хвилин.")
    if not verify_password(str(pin).strip(), u.finance_pin_hash):
        throttle.register_fail(s, "fin_pin", u.id)
        audit(s, actor, "user", u.id, "finance_pin_failed")
        raise AuthError("Невірний PIN")
    throttle.clear(s, "fin_pin", u.id)
    return u


def reset_finance_pin(s: Session, actor: Actor, user_id: int) -> None:
    actor.require(Role.DIRECTOR)
    if actor.user_id == user_id:
        raise Forbidden("PIN скидає інший директор")
    u: User = get_or_404(s, User, user_id, "Користувача")
    u.finance_pin_hash = None
    throttle.clear(s, "fin_pin", u.id)
    audit(s, actor, "user", u.id, "reset_finance_pin")
