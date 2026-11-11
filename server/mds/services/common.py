from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import Forbidden, NotFound
from ..models import AuditLog, LedgerEntry, Role, Setting, User


@dataclass(frozen=True)
class Actor:

    user_id: int | None
    name: str
    role: str
    telegram_user_id: int | None = None

    @classmethod
    def of(cls, user: User) -> "Actor":
        return cls(user.id, user.username, user.role)

    @classmethod
    def telegram(cls, tg_user_id: int) -> "Actor":
        return cls(None, f"telegram:{tg_user_id}", "client", tg_user_id)

    @classmethod
    def system(cls) -> "Actor":
        return cls(None, "system", "system")

    @property
    def is_director(self) -> bool:
        return self.role in (Role.DIRECTOR, "system")

    @property
    def is_master(self) -> bool:
        return self.role == Role.MASTER

    def require(self, *roles: str) -> None:
        if self.role == "system":
            return
        if self.role not in roles:
            raise Forbidden("Недостатньо прав для цієї дії")


def check_version(entity: Any, expected: int | None, public: Any = None) -> None:
    if expected is None or int(expected) == entity.version:
        return
    from ..errors import VersionConflict

    raise VersionConflict(entity.version, public(entity) if public else None)


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def audit(
    s: Session,
    actor: Actor,
    entity: str,
    entity_id: int,
    action: str,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
) -> None:
    s.add(
        AuditLog(
            user_id=actor.user_id,
            actor=actor.name,
            entity=entity,
            entity_id=entity_id,
            action=action,
            before=_jsonable(before),
            after=_jsonable(after),
            reason=reason,
        )
    )


def ledger(
    s: Session,
    actor: Actor,
    kind: str,
    ref_type: str,
    ref_id: int,
    *,
    master_id: int | None = None,
    amount: int = 0,
    master_amount: int = 0,
    salon_amount: int = 0,
    materials_amount: int = 0,
    details: dict | None = None,
) -> None:
    s.add(
        LedgerEntry(
            kind=kind,
            ref_type=ref_type,
            ref_id=ref_id,
            master_id=master_id,
            amount=amount,
            master_amount=master_amount,
            salon_amount=salon_amount,
            materials_amount=materials_amount,
            user_id=actor.user_id,
            details=_jsonable(details),
        )
    )


DEFAULT_SETTINGS: dict[str, Any] = {
    "salon_name": "M&D",
    "schedule": {str(d): ["09:00", "20:00"] for d in range(6)},
    "slot_step_min": 30,
    "booking_horizon_days": 30,
    "booking_min_lead_min": 60,
    "off_hours_booking": "if_free_place",
    "workplaces": 2,
    "max_masters": 4,
    "telegram_auto_confirm": False,
    "bot_staff_code": "0000",
    "master_takes_cash": True,
    "favorite_master_visits": 3,
    "reminders_enabled": True,
    "twofa_roles": ["director"],
    "alerts_disabled": [],
    "finance_pin_enabled": False,
    "reports_hidden": [],
    "alerts_digest_hour": 9,
    "vacation_mode_active": False,
    "vacation_mode_expires_at": None,
    "jobs_daily_last": None,
    "stock_auto_writeoff_milli": 100,
    "antispam_whitelist": ["+380687166910"],
    "sms_enabled": False,
    "sms_price": 0,
    "sms_auto_birthday": False,
    "sms_auto_return": False,
    "sms_auto_hour": 11,
    "sms_birthday_text": "{name}, вітаємо з днем народження! 🎉 Даруємо приємний бонус на наступний візит у M&D.",
    "sms_return_text": "{name}, час оновити образ 💇 Запишіться в M&D — оберемо зручний час.",
    "antispam_max_active": 2,
    "antispam_max_per_day": 3,
    "antispam_max_pending_total": 20,
    "antispam_callbacks_per_day": 2,
    "antispam_callbacks_per_hour": 20,
    "client_seg_regular_visits": 3,
    "client_seg_vip_visits": 10,
    "client_seg_vip_spent": 1_000_000,
    "client_seg_lapsed_days": 45,
    "client_seg_inactive_days": 90,
    "generator_fuel_lph_milli": 0,
    "generator_fuel_price": 0,
    "generator_amort_per_hour": 0,
    "generator_hours_initial_milli": 0,
    "fop_income_limit": 0,
    "card_terminal_mode": "off",
    "fop_tin": "",
    "fop_name": "",
    "fop_address": "",
    "fop_sti_name": "",
    "fop_c_reg": 0,
    "fop_c_raj": 0,
    "fop_c_sti": 0,
    "fop_kved": "96.02",
    "fop_kved_name": "Надання послуг перукарнями та салонами краси",
    "fop_single_tax_month": 0,
    "fop_min_wage": 0,
    "fop_email": "",
    "fop_phone": "",
    "salon_address": "",
    "salon_phone": "",
    "salon_instagram": "",
}


INTERNAL_SETTINGS = ("vacation_mode_active", "vacation_mode_expires_at", "jobs_daily_last")


def get_setting(s: Session, key: str) -> Any:
    row = s.get(Setting, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    return row.value


def all_settings(s: Session) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    for row in s.scalars(select(Setting)):
        out[row.key] = row.value
    return out


def set_setting(s: Session, actor: Actor, key: str, value: Any) -> None:
    actor.require(Role.DIRECTOR)
    row = s.get(Setting, key)
    before = row.value if row else DEFAULT_SETTINGS.get(key)
    if row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value
    audit(s, actor, "setting", 0, "update", {key: before}, {key: value})


def get_or_404(s: Session, model, obj_id: int, what: str = "Об'єкт"):
    obj = s.get(model, obj_id)
    if obj is None:
        raise NotFound(f"{what} #{obj_id} не знайдено")
    return obj


def update_settings(s: Session, actor: Actor, data: dict[str, Any]) -> dict[str, Any]:
    from ..errors import ValidationError
    from .users import _validate_schedule

    actor.require(Role.DIRECTOR)
    for key, value in data.items():
        if key not in DEFAULT_SETTINGS:
            raise ValidationError(f"Невідоме налаштування: {key}")
        if key in INTERNAL_SETTINGS:
            raise ValidationError(f"{key}: змінюється окремою дією, не через налаштування")
        if key == "alerts_disabled":
            from .alerts import IMPORTANT

            if not (isinstance(value, list) and all(k in IMPORTANT for k in value)):
                raise ValidationError("Вимкнути можна лише важливі сповіщення")
        if key == "alerts_digest_hour" and not (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 23):
            raise ValidationError("Година дайджесту — від 0 до 23")
        if key == "schedule":
            _validate_schedule(value)
        if key in ("slot_step_min", "booking_horizon_days", "booking_min_lead_min") and not (
            isinstance(value, int) and 0 <= value <= 365 * 24 * 60
        ):
            raise ValidationError(f"{key}: очікується ціле невід'ємне число")
        if key in ("master_takes_cash", "telegram_auto_confirm", "reminders_enabled", "finance_pin_enabled") and not isinstance(value, bool):
            raise ValidationError(f"{key}: так або ні")
        if key.startswith("antispam_") and key != "antispam_whitelist" and not (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000):
            raise ValidationError(f"{key}: ціле число від 0 до 1000")
        if key == "antispam_whitelist":
            from .catalog import normalize_phone

            if not isinstance(value, list) or len(value) > 50:
                raise ValidationError("Білий список: перелік номерів")
            value = sorted({normalize_phone(str(x)) for x in value if str(x).strip()})
            data[key] = value
        if key in ("sms_auto_birthday", "sms_auto_return") and not isinstance(value, bool):
            raise ValidationError(f"{key}: так або ні")
        if key == "sms_price" and not (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100_000):
            raise ValidationError("Ціна SMS — від 0 до 1000 ₴")
        if key == "sms_auto_hour" and not (isinstance(value, int) and 8 <= value <= 21):
            raise ValidationError("Година автоповідомлень — з 8 до 21")
        if key in ("sms_birthday_text", "sms_return_text") and not (isinstance(value, str) and 0 < len(value) <= 670):
            raise ValidationError("Текст повідомлення — до 670 символів")
        if key == "sms_enabled" and not isinstance(value, bool):
            raise ValidationError("SMS: так або ні")
        if key.startswith("client_seg_") and not (isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 10**10):
            raise ValidationError("Критерії сегментів — додатні цілі числа")
        if key == "stock_auto_writeoff_milli" and not (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 500):
            raise ValidationError("Автосписання залишку: від 0 до 0,5 упаковки")
        if key == "twofa_roles" and not (isinstance(value, list) and all(r == Role.DIRECTOR for r in value)):
            raise ValidationError("Двофакторний вхід: лише для директора та/або адміністратора")
        if key == "favorite_master_visits" and not (isinstance(value, int) and 1 <= value <= 50):
            raise ValidationError("Улюблений майстер: від 1 до 50 візитів")
        if key == "bot_staff_code" and not (isinstance(value, str) and value.isdigit() and 4 <= len(value) <= 8):
            raise ValidationError("Код персоналу: від 4 до 8 цифр")
        if key in ("salon_address", "salon_phone", "salon_instagram", "salon_name") and not isinstance(value, str):
            raise ValidationError(f"{key}: очікується текст")
        if key in ("generator_fuel_lph_milli", "generator_fuel_price", "generator_amort_per_hour",
                   "generator_hours_initial_milli", "fop_income_limit") and not (
            isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**12
        ):
            raise ValidationError(f"{key}: очікується ціле невід'ємне число")
        if key == "card_terminal_mode":
            from .card_terminal_providers import MODES, configured

            if value not in MODES:
                raise ValidationError("Режим «Термінала»: вимкнено, імітація або реальний")
            from .card_terminal import AUTO_CLOSE_ENABLED

            if value == "live" and not AUTO_CLOSE_ENABLED:
                raise ValidationError("Автоматичні оплати «Терміналом» — з версії 0.4.0. Зараз: після оплати оберіть «Картка»")
            if value == "live" and not configured():
                raise ValidationError("Для реальних оплат задайте ключі «Термінала» на сервері (MDS_PRIVAT_TERMINAL_*)")
        if key.startswith("fop_") and key != "fop_income_limit":
            from .tax_declaration import validate_profile_value

            value = validate_profile_value(key, value)
        if key == "off_hours_booking" and value not in ("forbid", "if_free_place", "allow"):
            raise ValidationError("Запис у неробочий час: заборонити, лише якщо є вільне місце, або дозволити")
        if key == "workplaces" and not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 20):
            raise ValidationError("Робочих місць — від 1 до 20")
        if key == "max_masters" and not (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 50):
            raise ValidationError("Майстрів у салоні — від 1 до 50")
        if key == "slot_step_min" and value < 5:
            raise ValidationError("Крок слотів — щонайменше 5 хвилин")
        set_setting(s, actor, key, value)
    return all_settings(s)
