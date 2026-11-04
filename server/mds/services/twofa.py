"""Двофакторний вхід: після правильного пароля — 6-значний код у Telegram.

Кому: ролі з налаштування `twofa_roles` (за замовчуванням директор), якщо в людини підключено Telegram.
Код дійсний 5 хвилин, 5 спроб; зберігається лише хеш. Після підтвердження пристрій можна «запам'ятати»
на 30 днів — тоді код на ньому не питається. Зміна пароля (token_version) забуває всі пристрої.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .. import clock
from ..errors import AuthError, ValidationError
from ..models import LoginChallenge, Role, TrustedDevice, User
from . import notifications
from .common import Actor, audit, get_setting

CODE_TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 5
DEVICE_TTL = timedelta(days=30)
ROLES = (Role.DIRECTOR, Role.ADMIN)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _code_hash(ch_id: int, user_id: int, code: str) -> str:
    return _hash(f"{ch_id}:{user_id}:{code}")


def applies(s: Session, u: User) -> bool:
    """Чи потрібен код для цієї людини (роль у налаштуванні і підключений Telegram).
    Аварійно (Telegram недоступний) вимикається змінною сервера MDS_TWOFA_DISABLED=1."""
    if os.environ.get("MDS_TWOFA_DISABLED", "").strip().lower() in ("1", "yes", "true"):
        return False
    roles = get_setting(s, "twofa_roles") or []
    return u.role in roles and bool(u.telegram_chat_id)


def device_ok(s: Session, u: User, device_token: str | None) -> bool:
    if not device_token:
        return False
    d = s.scalar(select(TrustedDevice).where(TrustedDevice.token_hash == _hash(device_token), TrustedDevice.user_id == u.id))
    now = clock.now()
    if d is None or d.expires_at < now or d.token_version != u.token_version:
        return False
    d.last_used_at = now
    return True


def start(s: Session, u: User) -> LoginChallenge:
    """Новий код (попередні невикористані — анулюються) і повідомлення в Telegram."""
    now = clock.now()
    s.execute(update(LoginChallenge).where(LoginChallenge.user_id == u.id, LoginChallenge.used.is_(False)).values(used=True))
    s.execute(delete(LoginChallenge).where(LoginChallenge.created_at < now - timedelta(days=1)))
    ch = LoginChallenge(user_id=u.id, code_hash="", expires_at=now + CODE_TTL, attempts=0, used=False, created_at=now)
    s.add(ch)
    s.flush()
    code = f"{secrets.randbelow(10**6):06d}"
    ch.code_hash = _code_hash(ch.id, u.id, code)
    notifications.enqueue(s, u.telegram_chat_id,
                          f"🔐 Код для входу в MDS Connect: <b>{code}</b>\n"
                          f"Дійсний 5 хвилин. Нікому його не повідомляйте.\n\n"
                          f"Якщо ви зараз не входите — хтось знає ваш пароль: змініть його в «Профілі».")
    audit(s, Actor.of(u), "user", u.id, "twofa_sent")
    return ch


def verify(s: Session, challenge_id: int, code: str) -> User:
    """Перевірити код. Помилка — AuthError (401). Спроби рахуються навіть при помилці (commit у роутері)."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        raise ValidationError("Код — 6 цифр")
    ch = s.get(LoginChallenge, challenge_id)
    now = clock.now()
    if ch is None or ch.used or ch.expires_at < now:
        raise AuthError("Код недійсний або застарів. Увійдіть ще раз — надішлемо новий.")
    if ch.attempts >= MAX_ATTEMPTS:
        raise AuthError("Забагато спроб. Увійдіть ще раз — надішлемо новий код.")
    ch.attempts += 1
    if not hmac.compare_digest(ch.code_hash, _code_hash(ch.id, ch.user_id, code)):
        left = MAX_ATTEMPTS - ch.attempts
        raise AuthError(f"Невірний код. Залишилось спроб: {left}" if left else "Забагато спроб. Увійдіть ще раз — надішлемо новий код.")
    ch.used = True
    u = s.get(User, ch.user_id)
    if u is None or not u.is_active:
        raise AuthError("Акаунт деактивовано")
    return u


def remember_device(s: Session, u: User, label: str | None) -> str:
    token = secrets.token_urlsafe(32)
    now = clock.now()
    s.add(TrustedDevice(user_id=u.id, token_hash=_hash(token), label=(label or "")[:160] or None,
                        token_version=u.token_version, created_at=now, last_used_at=now, expires_at=now + DEVICE_TTL))
    audit(s, Actor.of(u), "user", u.id, "device_trusted", None, {"label": label})
    return token


def list_devices(s: Session, actor: Actor) -> list[dict]:
    now = clock.now()
    u = s.get(User, actor.user_id)
    rows = s.scalars(select(TrustedDevice).where(TrustedDevice.user_id == actor.user_id, TrustedDevice.expires_at >= now,
                                                 TrustedDevice.token_version == u.token_version)
                     .order_by(TrustedDevice.last_used_at.desc()))
    return [{"id": d.id, "label": d.label or "Пристрій", "created_at": d.created_at.isoformat(),
             "last_used_at": d.last_used_at.isoformat() if d.last_used_at else None, "expires_at": d.expires_at.isoformat()} for d in rows]


def forget_devices(s: Session, actor: Actor, device_id: int | None = None) -> int:
    q = delete(TrustedDevice).where(TrustedDevice.user_id == actor.user_id)
    if device_id is not None:
        q = q.where(TrustedDevice.id == device_id)
    n = s.execute(q).rowcount or 0
    audit(s, actor, "user", actor.user_id, "devices_forgotten", None, {"count": n})
    return n
