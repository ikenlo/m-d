"""Спільні інструменти сервісного шару: актор, журнали, налаштування."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import Forbidden, NotFound
from ..models import AuditLog, LedgerEntry, MaterialsMode, Role, Setting, User


@dataclass(frozen=True)
class Actor:
    """Хто виконує дію: співробітник, клієнт з Telegram або система."""

    user_id: int | None
    name: str
    role: str  # director | admin | master | client | system
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
    def is_staff_manager(self) -> bool:
        """Директор або адміністратор — керують календарем усіх майстрів."""
        return self.role in (Role.DIRECTOR, Role.ADMIN, "system")

    @property
    def is_master(self) -> bool:
        return self.role == Role.MASTER

    def require(self, *roles: str) -> None:
        if self.role == "system":
            return
        if self.role not in roles:
            raise Forbidden("Недостатньо прав для цієї дії")


# ---------------------------------------------------------------- журнали

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


# ----------------------------------------------------------- налаштування

DEFAULT_SETTINGS: dict[str, Any] = {
    "salon_name": "M&D",
    "default_share_bp": 5000,  # 50% майстру
    "default_materials_mode": MaterialsMode.SALON,
    # графік салону за замовчуванням: 0 = понеділок
    "schedule": {str(d): ["09:00", "20:00"] for d in range(6)},
    "slot_step_min": 30,
    "booking_horizon_days": 30,
    "booking_min_lead_min": 60,  # мінімум хвилин до запису через бот
    "telegram_auto_confirm": False,  # записи з бота одразу підтверджені?
    "bot_staff_code": "0000",
    # при закритті зміни майстер забирає свій заробіток із готівки в касі; решту доплачує директор
    "master_takes_cash": True,
    # скільки візитів до одного майстра — і він стає «улюбленим» для клієнта
    "favorite_master_visits": 3,
    # нагадування клієнтам у Telegram за добу і за 2 години (лише тим, хто погодився)
    "reminders_enabled": True,
    # ---- генератор (значення діють для НОВИХ запусків; минулі зберігають свої)
    "generator_fuel_lph_milli": 0,  # витрата бензину, л/год × 1000 (1500 = 1,5 л/год)
    "generator_fuel_price": 0,  # ціна бензину, копійок за 1 л
    "generator_amort_per_hour": 0,  # амортизація, копійок за 1 мотогодину
    "generator_hours_initial_milli": 0,  # мотогодини на момент початку обліку × 1000
    # ---- розподіл комунальних і генератора між майстрами
    "utilities_charge_mode": "none",  # none | month_end | fixed
    "utilities_split": "revenue",  # month_end: revenue (пропорційно виручці) | equal (порівну)
    "utilities_charge_share_bp": 10000,  # яку частку витрат розподіляти (10000 = 100%)
    "utilities_fixed_amount": 0,  # fixed: сума, копійки
    "utilities_fixed_per": "shift",  # fixed: shift (за зміну) | month (за місяць)
    # ліміт доходу групи ФОП за рік, копійки (0 — не задано); вносить директор/бухгалтер
    "fop_income_limit": 0,
    # оплата карткою через «Термінал» ПриватБанку на планшеті: off | mock (імітація) | live (ключі — змінні сервера)
    "card_terminal_mode": "off",
    # профіль ФОП для XML-декларації (services/tax_declaration.py); ставки вносить директор/бухгалтер
    "fop_tin": "",
    "fop_name": "",
    "fop_address": "",
    "fop_sti_name": "",
    "fop_c_reg": 0,
    "fop_c_raj": 0,
    "fop_c_sti": 0,
    "fop_kved": "96.02",
    "fop_kved_name": "Надання послуг перукарнями та салонами краси",
    "fop_single_tax_month": 0,  # єдиний податок на місяць, копійки (рішення місцевої ради)
    "fop_min_wage": 0,  # мінімальна зарплата на 1 січня року декларації, копійки
    "fop_email": "",
    "fop_phone": "",
    "salon_address": "",
    "salon_phone": "",
    "salon_instagram": "",
}


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
    """Оновлення налаштувань салону з валідацією."""
    from ..errors import ValidationError
    from .users import _validate_schedule

    actor.require(Role.DIRECTOR)
    for key, value in data.items():
        if key not in DEFAULT_SETTINGS:
            raise ValidationError(f"Невідоме налаштування: {key}")
        if key == "default_share_bp" and not (isinstance(value, int) and 0 <= value <= 10_000):
            raise ValidationError("Відсоток майстра має бути від 0 до 100%")
        if key == "default_materials_mode" and value not in MaterialsMode.ALL:
            raise ValidationError("Невідомий режим обліку матеріалів")
        if key == "schedule":
            _validate_schedule(value)
        if key in ("slot_step_min", "booking_horizon_days", "booking_min_lead_min") and not (
            isinstance(value, int) and 0 <= value <= 365 * 24 * 60
        ):
            raise ValidationError(f"{key}: очікується ціле невід'ємне число")
        if key in ("master_takes_cash", "telegram_auto_confirm", "reminders_enabled") and not isinstance(value, bool):
            raise ValidationError(f"{key}: так або ні")
        if key == "favorite_master_visits" and not (isinstance(value, int) and 1 <= value <= 50):
            raise ValidationError("Улюблений майстер: від 1 до 50 візитів")
        if key == "bot_staff_code" and not (isinstance(value, str) and value.isdigit() and 4 <= len(value) <= 8):
            raise ValidationError("Код персоналу: від 4 до 8 цифр")
        if key in ("salon_address", "salon_phone", "salon_instagram", "salon_name") and not isinstance(value, str):
            raise ValidationError(f"{key}: очікується текст")
        if key in ("generator_fuel_lph_milli", "generator_fuel_price", "generator_amort_per_hour",
                   "generator_hours_initial_milli", "utilities_fixed_amount", "fop_income_limit") and not (
            isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**12
        ):
            raise ValidationError(f"{key}: очікується ціле невід'ємне число")
        if key == "utilities_charge_share_bp" and not (isinstance(value, int) and 0 <= value <= 10_000):
            raise ValidationError("Частка для розподілу — від 0 до 100%")
        if key == "utilities_charge_mode" and value not in ("none", "month_end", "fixed"):
            raise ValidationError("Режим списання: вимкнено, наприкінці місяця або фіксована ставка")
        if key == "utilities_split" and value not in ("revenue", "equal"):
            raise ValidationError("Розподіл: пропорційно виручці або порівну")
        if key == "utilities_fixed_per" and value not in ("shift", "month"):
            raise ValidationError("Фіксована ставка: за зміну або за місяць")
        if key == "card_terminal_mode":
            from .card_terminal_providers import MODES, configured

            if value not in MODES:
                raise ValidationError("Режим «Термінала»: вимкнено, імітація або реальний")
            if value == "live" and not configured():
                raise ValidationError("Для реальних оплат задайте ключі «Термінала» на сервері (MDS_PRIVAT_TERMINAL_*)")
        if key.startswith("fop_") and key != "fop_income_limit":
            from .tax_declaration import validate_profile_value

            value = validate_profile_value(key, value)
        if key == "slot_step_min" and value < 5:
            raise ValidationError("Крок слотів — щонайменше 5 хвилин")
        set_setting(s, actor, key, value)
    return all_settings(s)
