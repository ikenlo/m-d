"""ORM-моделі. Гроші — цілі копійки, відсотки — базисні пункти (bp).

Дати-час — наївні, у локальному часі салону (див. clock.py).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    LargeBinary,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Column,
    Text,
    BigInteger,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import clock
from .db import Base


class Role:
    DIRECTOR = "director"
    ADMIN = "admin"
    MASTER = "master"
    ALL = (DIRECTOR, ADMIN, MASTER)


class MaterialsMode:
    """Хто несе собівартість матеріалів."""

    SALON = "salon"  # зменшує частку салону (за замовчуванням)
    MASTER = "master"  # віднімається від заробітку майстра
    SPLIT = "split"  # віднімається від суми до поділу (пропорційно)
    ALL = (SALON, MASTER, SPLIT)


class ApptStatus:
    PENDING = "pending"  # очікує підтвердження (наприклад, з Telegram)
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"  # клієнт прийнятий
    COMPLETED = "completed"  # замовлення закрито
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    ALL = (PENDING, CONFIRMED, IN_PROGRESS, COMPLETED, CANCELLED, NO_SHOW)
    # статуси, що займають час у календарі
    OCCUPYING = (PENDING, CONFIRMED, IN_PROGRESS, COMPLETED)
    # статуси, з яких можна закрити замовлення
    CLOSABLE = (PENDING, CONFIRMED, IN_PROGRESS)
    # статуси, які ще можна змінювати/скасовувати
    EDITABLE = (PENDING, CONFIRMED, IN_PROGRESS)


class PaymentMethod:
    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"
    ALL = (CASH, CARD, TRANSFER)


class ShiftStatus:
    OPEN = "open"
    CLOSED = "closed"  # звіт сформовано, очікує виплати
    PAID = "paid"
    ALL = (OPEN, CLOSED, PAID)


class ExpenseCategory:
    RENT = "rent"
    UTILITIES = "utilities"
    ADVERTISING = "advertising"
    MATERIALS = "materials"  # закупівля (грошовий потік, див. docs/FINANCE.md)
    EQUIPMENT = "equipment"  # обладнання
    HOUSEHOLD = "household"  # господарські витрати
    FUEL = "fuel"  # закупівля бензину для генератора (грошовий потік; витрата — за мотогодинами)
    OTHER = "other"
    ALL = (RENT, UTILITIES, ADVERTISING, MATERIALS, EQUIPMENT, HOUSEHOLD, FUEL, OTHER)
    CASH_FLOW_ONLY = (MATERIALS, FUEL)  # не входять в операційні витрати (інакше подвійний облік)


def _now() -> datetime:
    return clock.now()


master_services = Table(
    "master_services",
    Base.metadata,
    Column("master_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("service_id", ForeignKey("services.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # індивідуальні фінансові умови майстра (None = значення з налаштувань салону)
    share_bp: Mapped[int | None] = mapped_column(Integer)
    materials_mode: Mapped[str | None] = mapped_column(String(16))
    # графік: {"0": ["09:00", "18:00"], ...} (0 = понеділок); None = графік салону
    schedule: Mapped[dict | None] = mapped_column(JSON)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_link_code: Mapped[str | None] = mapped_column(String(16), unique=True)
    # особистий PIN для входу в розділ персоналу Telegram-бота (хеш scrypt)
    bot_pin_hash: Mapped[str | None] = mapped_column(String(255))
    # безпека
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    # лічильник для серіалізації записів до одного майстра (див. bookings.py)
    booking_lock: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    services: Mapped[list["Service"]] = relationship(secondary=master_services)


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # каталог для запису: розділ («Чоловічі», «Жіночі») → тип («Стрижка», «Фарбування») → послуга
    section: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(64))
    # необов'язковий третій рівень: «Фарбування» → «Мелірування» → послуга
    subcategory: Mapped[str | None] = mapped_column(String(64))
    duration_min: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)  # копійки
    # ціна «від»: остаточна сума залежить від довжини/густоти волосся тощо
    price_from: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Material(Base):
    """Матеріал на складі. Окремий тон фарби — окремий рядок (name="Igora Royal", tone="7-1").

    Облік ведеться в базових одиницях (unit: г / мл / шт), залишок — у тисячних (stock_milli).
    Упаковка: pack_name («туба») містить pack_size базових одиниць (напр. 60 г);
    unit_cost — ціна однієї упаковки в копійках. Для штучного товару pack_size = 1.
    """

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    tone: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(64))  # «Фарби», «Окисники», «Догляд»…
    unit: Mapped[str] = mapped_column(String(16), default="шт")
    pack_name: Mapped[str] = mapped_column(String(16), default="шт", server_default="шт")
    pack_size: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    unit_cost: Mapped[int] = mapped_column(Integer)  # копійки за упаковку
    stock_milli: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    min_stock_milli: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class StockMove(Base):
    """Рух складу: прихід (+), списання на замовлення (−), інвентаризація (±). Лише додавання."""

    __tablename__ = "stock_moves"
    __table_args__ = (Index("ix_stock_material", "material_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    kind: Mapped[str] = mapped_column(String(16))  # receipt | order | order_return | adjust | writeoff
    qty_milli: Mapped[int] = mapped_column(Integer)  # зі знаком, у тисячних базової одиниці
    cost: Mapped[int] = mapped_column(Integer, default=0)  # копійки (для приходу — сума закупівлі)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    user_id: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)


class ScheduleDay(Base):
    """Графік майстра на конкретну дату — перекриває тижневий шаблон.
    is_off=True — вихідний; інакше робочі години start–end («09:00»)."""

    __tablename__ = "schedule_days"
    __table_args__ = (UniqueConstraint("master_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    is_off: Mapped[bool] = mapped_column(Boolean, default=False)
    start: Mapped[str | None] = mapped_column(String(5))
    end: Mapped[str | None] = mapped_column(String(5))


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    phone: Mapped[str] = mapped_column(String(32), unique=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    # нагадування в Telegram за добу і за 2 години: None — ще не питали, True/False — відповідь клієнта
    reminders: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (Index("ix_appt_master_time", "master_id", "starts_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    price: Mapped[int] = mapped_column(Integer)  # планова ціна, копійки
    status: Mapped[str] = mapped_column(String(16), default=ApptStatus.CONFIRMED)
    source: Mapped[str] = mapped_column(String(16), default="staff")  # staff | telegram
    comment: Mapped[str | None] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    # майстер запропонував клієнту інший час — чекаємо відповіді в боті
    awaiting_client: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # які нагадування вже надіслано (скидаються при перенесенні)
    reminded_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    reminded_2h: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    client: Mapped[Client] = relationship()
    master: Mapped[User] = relationship(foreign_keys=[master_id])
    service: Mapped[Service] = relationship()
    order: Mapped["Order | None"] = relationship(back_populates="appointment", uselist=False)


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default=ShiftStatus.OPEN)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # знімок звіту на момент закриття
    report: Mapped[dict | None] = mapped_column(JSON)
    payout_due: Mapped[int | None] = mapped_column(Integer)

    master: Mapped[User] = relationship()
    orders: Mapped[list["Order"]] = relationship(back_populates="shift")


class Order(Base):
    """Закрите замовлення — фінансовий факт наданої послуги."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique: одне замовлення на запис (захист від повторного проведення)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"))
    amount: Mapped[int] = mapped_column(Integer)  # фактична вартість (виручка)
    materials_cost: Mapped[int] = mapped_column(Integer, default=0)
    payment_method: Mapped[str] = mapped_column(String(16))
    share_bp: Mapped[int] = mapped_column(Integer)  # знімок умов на момент закриття
    materials_mode: Mapped[str] = mapped_column(String(16))
    master_earning: Mapped[int] = mapped_column(Integer)
    salon_earning: Mapped[int] = mapped_column(Integer)  # після матеріалів
    closed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    closed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)

    appointment: Mapped[Appointment] = relationship(back_populates="order")
    shift: Mapped[Shift] = relationship(back_populates="orders")
    materials: Mapped[list["OrderMaterial"]] = relationship(
        cascade="all, delete-orphan", order_by="OrderMaterial.id"
    )
    master: Mapped[User] = relationship(foreign_keys=[master_id])
    client: Mapped[Client] = relationship()
    service: Mapped[Service] = relationship()


class OrderMaterial(Base):
    __tablename__ = "order_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"))
    name: Mapped[str] = mapped_column(String(128))
    qty: Mapped[int] = mapped_column(Integer, default=1)  # у тисячних (1000 = 1 од.)
    unit_cost: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # unique: одна виплата на зміну (захист від подвійної виплати)
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"), unique=True)
    # майстер узяв свій заробіток із готівки в касі при закритті цієї зміни
    cash_shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"))
    amount: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(16), default=PaymentMethod.CASH)
    note: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    confirmed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))

    master: Mapped[User] = relationship(foreign_keys=[master_id])


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(Integer)
    spent_on: Mapped[date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    payment_method: Mapped[str | None] = mapped_column(String(16))  # cash | card | account
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MeterReading(Base):
    """Показники лічильника електроенергії за місяць. Тариф зберігається в записі —
    зміна тарифу пізніше не змінює історію."""

    __tablename__ = "meter_readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(Date, unique=True)  # перше число місяця
    read_on: Mapped[date] = mapped_column(Date)
    prev_milli: Mapped[int] = mapped_column(BigInteger)  # кВт·год × 1000
    curr_milli: Mapped[int] = mapped_column(BigInteger)
    used_milli: Mapped[int] = mapped_column(BigInteger)
    tariff: Mapped[int] = mapped_column(Integer)  # копійок за 1 кВт·год
    cost: Mapped[int] = mapped_column(Integer)  # копійки
    paid_amount: Mapped[int | None] = mapped_column(Integer)
    paid_on: Mapped[date | None] = mapped_column(Date)
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expenses.id", name="fk_meter_readings_expense_id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    confirmed_by: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GeneratorRun(Base):
    """Робота генератора. Параметри (витрата, ціна бензину, амортизація) — знімок на момент запуску."""

    __tablename__ = "generator_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    # True лише в одного запису, що зараз працює (UNIQUE) — двох одночасних запусків бути не може
    running: Mapped[bool | None] = mapped_column(Boolean, unique=True)
    hours_milli: Mapped[int] = mapped_column(Integer, default=0)  # мотогодини × 1000
    fuel_lph_milli: Mapped[int] = mapped_column(Integer)  # л/год × 1000
    fuel_price: Mapped[int] = mapped_column(Integer)  # копійок за 1 л
    amort_per_hour: Mapped[int] = mapped_column(Integer)  # копійок за мотогодину
    fuel_milli: Mapped[int] = mapped_column(Integer, default=0)  # літри × 1000
    fuel_cost: Mapped[int] = mapped_column(Integer, default=0)
    amort_cost: Mapped[int] = mapped_column(Integer, default=0)
    total_cost: Mapped[int] = mapped_column(Integer, default=0)
    power_outage: Mapped[bool] = mapped_column(Boolean, default=True)
    corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    started_by: Mapped[int | None] = mapped_column(Integer)
    ended_by: Mapped[int | None] = mapped_column(Integer)


class MasterCharge(Base):
    """Списання з майстра (частка комунальних/генератора). Лише додавання: скасування — рядок-сторно.
    dedupe_key (UNIQUE) не дає списати двічі за той самий місяць/зміну."""

    __tablename__ = "master_charges"

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_master_charges_master_id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # utilities_month | utilities_shift | utilities_fixed_month | reversal
    charged_on: Mapped[date] = mapped_column(Date, index=True)
    period: Mapped[date] = mapped_column(Date)  # місяць (перше число)
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id", name="fk_master_charges_shift_id"))
    amount: Mapped[int] = mapped_column(Integer)  # копійки; сторно — від'ємне
    dedupe_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("master_charges.id", name="fk_master_charges_reverses_id"))
    reversed: Mapped[bool] = mapped_column(Boolean, default=False)
    basis: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    master: Mapped[User] = relationship()


class LedgerEntry(Base):
    """Журнал фінансових операцій. Тільки додавання, ніколи не змінюється."""

    __tablename__ = "ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    kind: Mapped[str] = mapped_column(String(32))
    ref_type: Mapped[str] = mapped_column(String(32))
    ref_id: Mapped[int] = mapped_column(Integer)
    master_id: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    master_amount: Mapped[int] = mapped_column(Integer, default=0)
    salon_amount: Mapped[int] = mapped_column(Integer, default=0)
    materials_amount: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSON)


class AuditLog(Base):
    """Історія змін сутностей (хто, коли, що було, що стало, чому)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    user_id: Mapped[int | None] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(64))  # username / "telegram:<id>" / "system"
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | None] = mapped_column(JSON)


class CallbackRequest(Base):
    """«Швидкий запис»: клієнт лишив номер, адміністратор/директор передзвонює."""

    __tablename__ = "callback_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(128))
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Notification(Base):
    """Черга повідомлень Telegram (outbox). Записується в тій самій транзакції,
    що й подія; доставляє процес бота."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notif_pending", "sent_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    # кнопки під повідомленням: [[["Текст", "callback_data"], ...], ...]
    buttons: Mapped[list | None] = mapped_column(JSON)
    # фото до повідомлення (файл у data_dir), напр. знімок екрана в баг-репорті
    photo_path: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


__all__ = [
    "Appointment",
    "ApptStatus",
    "AuditLog",
    "CallbackRequest",
    "Client",
    "Expense",
    "ExpenseCategory",
    "LedgerEntry",
    "Material",
    "ScheduleDay",
    "StockMove",
    "MaterialsMode",
    "GeneratorRun",
    "MasterCharge",
    "MeterReading",
    "Notification",
    "Order",
    "OrderMaterial",
    "PaymentMethod",
    "Payout",
    "Role",
    "Service",
    "Setting",
    "Shift",
    "ShiftStatus",
    "User",
]


class CardPayment(Base):
    """Оплата карткою через застосунок «Термінал» ПриватБанку (NFC на планшеті).

    Створюється ДО оплати (статус pending, токен банку); замовлення закривається лише після того, як
    сервер сам перевірив результат у банку (check.php, response_code "00"). Повторна перевірка не задвоює оплату.
    """

    __tablename__ = "card_payments"
    __table_args__ = (Index("ix_card_payment_appt", "appointment_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    amount: Mapped[int] = mapped_column(Integer)  # копійки
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | paid | failed
    mode: Mapped[str] = mapped_column(String(8))  # mock | live
    jwt: Mapped[str | None] = mapped_column(Text)  # токен операції банку (одноразовий, не ключ)
    rid: Mapped[str | None] = mapped_column(String(64))
    close_params: Mapped[dict | None] = mapped_column(JSON)  # матеріали, ім'я клієнта — для закриття після оплати
    transaction_id: Mapped[str | None] = mapped_column(String(64))
    rrn: Mapped[str | None] = mapped_column(String(32))
    approval_code: Mapped[str | None] = mapped_column(String(16))
    masked_pan: Mapped[str | None] = mapped_column(String(32))
    payment_system: Mapped[str | None] = mapped_column(String(32))
    response_code: Mapped[str | None] = mapped_column(String(8))
    message: Mapped[str | None] = mapped_column(String(255))
    receipt: Mapped[str | None] = mapped_column(Text)  # HTML-чек банку
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TaxRate(Base):
    """Ставка/показник з періодом дії. Значення НЕ зашиті в код — вводить директор/бухгалтер.
    key: min_wage | single_tax | military_fop | esv_fop | pdfo | military_payroll | esv_employer."""

    __tablename__ = "tax_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(24), index=True)
    rate_bp: Mapped[int | None] = mapped_column(Integer)  # ставка в bp (2000 = 20%)
    amount: Mapped[int | None] = mapped_column(Integer)  # фіксована сума, копійки (напр. мінімальна зарплата)
    base: Mapped[str] = mapped_column(String(16), default="fixed")  # min_wage | income | salary | salary_min_wage | fixed
    period: Mapped[str] = mapped_column(String(8), default="month")  # month | quarter
    due_day: Mapped[int | None] = mapped_column(Integer)  # день сплати
    due_from: Mapped[str] = mapped_column(String(8), default="end")  # start | end — від початку чи кінця періоду
    due_offset_months: Mapped[int] = mapped_column(Integer, default=0)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TaxAccrual(Base):
    """Нарахування податку ФОП за період. Лише додавання: виправлення — сторно + нове нарахування."""

    __tablename__ = "tax_accruals"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(24))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    base_amount: Mapped[int] = mapped_column(Integer)
    rate_bp: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)  # сторно — від'ємне
    due_date: Mapped[date] = mapped_column(Date)
    dedupe_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("tax_accruals.id", name="fk_tax_accruals_reverses_id"))
    reversed: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_id: Mapped[int | None] = mapped_column(Integer)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TaxPayment(Base):
    __tablename__ = "tax_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(24))
    period_start: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(Integer)
    paid_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PayrollAccrual(Base):
    """Офіційне (юридичне) нарахування зарплати працівнику за місяць. НЕ пов'язане з управлінським
    поділом 50/50. Лише додавання: виправлення — сторно + нове нарахування."""

    __tablename__ = "payroll_accruals"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_payroll_accruals_user_id"), index=True)
    month: Mapped[date] = mapped_column(Date)
    gross: Mapped[int] = mapped_column(Integer)
    pdfo: Mapped[int] = mapped_column(Integer)
    military: Mapped[int] = mapped_column(Integer)
    net: Mapped[int] = mapped_column(Integer)
    esv_employer: Mapped[int] = mapped_column(Integer)
    esv_base: Mapped[int] = mapped_column(Integer)
    rates: Mapped[dict | None] = mapped_column(JSON)  # знімок ставок на дату нарахування
    dedupe_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("payroll_accruals.id", name="fk_payroll_accruals_reverses_id"))
    reversed: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    user: Mapped[User] = relationship()


class PayrollPayment(Base):
    __tablename__ = "payroll_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_payroll_payments_user_id"), index=True)
    month: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(Integer)
    paid_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EmployeeProfile(Base):
    """Кадрові дані найманого працівника для офіційної зарплати і звітів (Податковий розрахунок).
    Окремо від облікового запису: майстер у системі ≠ обов'язково найманий працівник."""

    __tablename__ = "employee_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_employee_profiles_user_id"), unique=True)
    tin: Mapped[str | None] = mapped_column(String(10))  # РНОКПП
    position: Mapped[str | None] = mapped_column(String(128))
    hired_on: Mapped[date | None] = mapped_column(Date)
    fired_on: Mapped[date | None] = mapped_column(Date)
    main_job: Mapped[bool] = mapped_column(Boolean, default=True)  # основне місце роботи (мінімальна база ЄСВ)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExpenseFile(Base):
    """Фото або файл документа до витрати (чек, накладна). Зберігається в базі — потрапляє в резервні копії."""

    __tablename__ = "expense_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey("expenses.id", name="fk_expense_files_expense_id"), index=True)
    filename: Mapped[str] = mapped_column(String(160))
    content_type: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
