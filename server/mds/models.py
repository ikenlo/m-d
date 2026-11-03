"""ORM-моделі. Гроші — цілі копійки, відсотки — базисні пункти (bp).

Дати-час — наївні, у локальному часі салону (див. clock.py).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
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
    OTHER = "other"
    ALL = (RENT, UTILITIES, ADVERTISING, MATERIALS, OTHER)


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
    """Довідник матеріалів/розхідників для швидкого вибору при закритті замовлення."""

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(16), default="шт")
    unit_cost: Mapped[int] = mapped_column(Integer)  # копійки за одиницю
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    phone: Mapped[str] = mapped_column(String(32), unique=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
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
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


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
    "MaterialsMode",
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
