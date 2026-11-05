from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, Forbidden, ValidationError
from ..models import MaterialsMode, Role, Service, User
from ..money import BP_100
from ..security import hash_password, validate_password_strength, verify_password
from .common import Actor, audit, get_or_404, get_setting

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15


def user_public(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "phone": u.phone,
        "role": u.role,
        "is_active": u.is_active,
        "share_bp": u.share_bp,
        "materials_mode": u.materials_mode,
        "schedule": u.schedule,
        "service_ids": sorted(s.id for s in u.services),
        "telegram_linked": u.telegram_chat_id is not None,
        "bot_pin_set": u.bot_pin_hash is not None,
    }


def effective_terms(s: Session, master: User) -> tuple[int, str]:
    share = master.share_bp if master.share_bp is not None else get_setting(s, "default_share_bp")
    mode = master.materials_mode or get_setting(s, "default_materials_mode")
    return int(share), str(mode)


def _validate_terms(share_bp: int | None, materials_mode: str | None) -> None:
    if share_bp is not None and not (0 <= share_bp <= BP_100):
        raise ValidationError("Відсоток майстра має бути від 0 до 100%")
    if materials_mode is not None and materials_mode not in MaterialsMode.ALL:
        raise ValidationError("Невідомий режим обліку матеріалів")


def _validate_schedule(schedule: dict | None) -> None:
    if schedule is None:
        return
    for day, span in schedule.items():
        if day not in {str(i) for i in range(7)}:
            raise ValidationError("День графіка має бути 0..6")
        if span is None:
            continue
        if not (isinstance(span, list) and len(span) == 2):
            raise ValidationError("Графік: очікується [\"09:00\", \"18:00\"]")
        try:
            a = tuple(map(int, span[0].split(":")))
            b = tuple(map(int, span[1].split(":")))
        except (ValueError, AttributeError):
            raise ValidationError("Графік: невірний формат часу") from None
        if a >= b:
            raise ValidationError("Графік: початок має бути раніше кінця")


def create_user(
    s: Session,
    actor: Actor,
    *,
    username: str,
    password: str,
    full_name: str,
    role: str,
    phone: str | None = None,
    share_bp: int | None = None,
    materials_mode: str | None = None,
    schedule: dict | None = None,
    service_ids: list[int] | None = None,
    bot_pin: str | None = None,
) -> User:
    if actor.role != "system":
        actor.require(Role.DIRECTOR)
    username = username.strip().lower()
    if role not in Role.ALL:
        raise ValidationError("Невідома роль")
    if not username or not username.replace("_", "").replace(".", "").isalnum():
        raise ValidationError("Логін: лише латинські літери, цифри, '_' та '.'")
    if err := validate_password_strength(password):
        raise ValidationError(err)
    _validate_terms(share_bp, materials_mode)
    _validate_schedule(schedule)
    if s.scalar(select(User).where(func.lower(User.username) == username)):
        raise Conflict("Такий логін уже існує")
    if role == Role.MASTER:
        _check_master_limit(s)
    u = User(
        username=username,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        phone=phone,
        role=role,
        share_bp=share_bp,
        materials_mode=materials_mode,
        schedule=schedule,
    )
    if service_ids:
        u.services = list(s.scalars(select(Service).where(Service.id.in_(service_ids))))
    if bot_pin:
        _set_pin(u, bot_pin)
    s.add(u)
    s.flush()
    audit(s, actor, "user", u.id, "create", None, user_public(u))
    return u


def _check_master_limit(s: Session) -> None:
    from .common import get_setting

    n = s.scalar(select(func.count(User.id)).where(User.role == Role.MASTER, User.is_active.is_(True))) or 0
    limit = int(get_setting(s, "max_masters"))
    if n >= limit:
        raise ValidationError(f"У салоні вже {n} активних майстрів — максимум {limit} (Налаштування → Запис клієнтів)")


UPDATABLE = ("full_name", "phone", "is_active", "share_bp", "materials_mode", "schedule", "role")


def update_user(s: Session, actor: Actor, user_id: int, data: dict) -> User:
    actor.require(Role.DIRECTOR)
    u: User = get_or_404(s, User, user_id, "Користувача")
    before = user_public(u)
    if "share_bp" in data or "materials_mode" in data:
        _validate_terms(data.get("share_bp"), data.get("materials_mode"))
    if "schedule" in data:
        _validate_schedule(data["schedule"])
    if "role" in data and data["role"] not in Role.ALL:
        raise ValidationError("Невідома роль")
    if u.id == actor.user_id and (data.get("is_active") is False or data.get("role", u.role) != u.role):
        raise Forbidden("Не можна деактивувати себе або змінити собі роль")
    becomes_master = data.get("role", u.role) == Role.MASTER and data.get("is_active", u.is_active)
    if becomes_master and not (u.role == Role.MASTER and u.is_active):
        _check_master_limit(s)
    for k in UPDATABLE:
        if k in data:
            setattr(u, k, data[k])
    if "service_ids" in data and data["service_ids"] is not None:
        u.services = list(s.scalars(select(Service).where(Service.id.in_(data["service_ids"]))))
    if data.get("bot_pin"):
        _set_pin(u, data["bot_pin"])
    if data.get("is_active") is False:
        u.token_version += 1
    s.flush()
    audit(s, actor, "user", u.id, "update", before, user_public(u))
    return u


def reset_password(s: Session, actor: Actor, user_id: int, new_password: str) -> None:
    actor.require(Role.DIRECTOR)
    u: User = get_or_404(s, User, user_id, "Користувача")
    if err := validate_password_strength(new_password):
        raise ValidationError(err)
    u.password_hash = hash_password(new_password)
    u.token_version += 1
    u.failed_logins = 0
    u.locked_until = None
    audit(s, actor, "user", u.id, "reset_password")


def change_own_password(s: Session, actor: Actor, old: str, new: str) -> None:
    u: User = get_or_404(s, User, actor.user_id or 0, "Користувача")
    if not verify_password(old, u.password_hash):
        raise ValidationError("Поточний пароль невірний")
    if err := validate_password_strength(new):
        raise ValidationError(err)
    u.password_hash = hash_password(new)
    u.token_version += 1
    audit(s, actor, "user", u.id, "change_password")


def authenticate(s: Session, username: str, password: str) -> tuple[User | None, str | None]:
    u = s.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    now = clock.now()
    if u is None:
        return None, "Невірний логін або пароль"
    if u.locked_until and u.locked_until > now:
        return None, "Акаунт тимчасово заблоковано через невдалі спроби входу. Спробуйте пізніше."
    if not verify_password(password, u.password_hash):
        u.failed_logins += 1
        if u.failed_logins >= MAX_FAILED_LOGINS:
            u.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            u.failed_logins = 0
            audit(s, Actor.system(), "user", u.id, "locked")
        return None, "Невірний логін або пароль"
    if not u.is_active:
        return None, "Акаунт деактивовано"
    u.failed_logins = 0
    u.locked_until = None
    return u, None


def new_telegram_link_code(s: Session, actor: Actor, user_id: int) -> str:
    if actor.user_id != user_id:
        actor.require(Role.DIRECTOR)
    u: User = get_or_404(s, User, user_id, "Користувача")
    u.telegram_link_code = secrets.token_hex(4).upper()
    return u.telegram_link_code


def link_telegram(s: Session, code: str, chat_id: int) -> User:
    u = s.scalar(select(User).where(User.telegram_link_code == code.strip().upper()))
    if u is None or not u.is_active:
        raise ValidationError("Код не знайдено або він уже використаний")
    for other in s.scalars(select(User).where(User.telegram_chat_id == chat_id)):
        other.telegram_chat_id = None
    u.telegram_chat_id = chat_id
    u.telegram_link_code = None
    audit(s, Actor.of(u), "user", u.id, "link_telegram")
    return u


def list_masters(s: Session, active_only: bool = True) -> list[User]:
    q = select(User).where(User.role == Role.MASTER)
    if active_only:
        q = q.where(User.is_active.is_(True))
    return list(s.scalars(q.order_by(User.full_name)))


def masters_for_service(s: Session, service_id: int) -> list[User]:
    out = []
    for m in list_masters(s):
        ids = {x.id for x in m.services}
        if not ids or service_id in ids:
            out.append(m)
    return out


def _set_pin(u: User, pin: str) -> None:
    pin = str(pin).strip()
    if not (pin.isdigit() and 4 <= len(pin) <= 8):
        raise ValidationError("PIN для бота: від 4 до 8 цифр")
    u.bot_pin_hash = hash_password(pin)


def set_bot_pin(s: Session, actor: Actor, user_id: int, pin: str) -> None:
    if actor.user_id != user_id:
        actor.require(Role.DIRECTOR)
    u: User = get_or_404(s, User, user_id, "Користувача")
    _set_pin(u, pin)
    audit(s, actor, "user", u.id, "set_bot_pin")


def check_staff_code(s: Session, code: str) -> bool:
    import hmac

    expected = str(get_setting(s, "bot_staff_code"))
    return hmac.compare_digest(str(code).strip(), expected)


def staff_for_bot(s: Session) -> list[User]:
    q = select(User).where(
        User.is_active.is_(True), User.bot_pin_hash.is_not(None), User.role.in_((Role.MASTER, Role.DIRECTOR))
    )
    return sorted(s.scalars(q), key=lambda u: (u.role != Role.MASTER, u.full_name))


def verify_bot_pin(s: Session, user_id: int, pin: str, chat_id: int | None = None) -> User | None:
    u = s.get(User, user_id)
    if u is None or not u.is_active or not u.bot_pin_hash:
        return None
    if not verify_password(str(pin).strip(), u.bot_pin_hash):
        audit(s, Actor.system(), "user", u.id, "bot_pin_failed")
        return None
    if chat_id and u.telegram_chat_id != chat_id:
        for other in s.scalars(select(User).where(User.telegram_chat_id == chat_id)):
            other.telegram_chat_id = None
        u.telegram_chat_id = chat_id
    audit(s, Actor.of(u), "user", u.id, "bot_login")
    return u
