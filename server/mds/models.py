from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    event,
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
    MASTER = "master"
    ALL = (DIRECTOR, MASTER)


class MaterialsMode:

    SALON = "salon"
    MASTER = "master"
    SPLIT = "split"
    ALL = (SALON, MASTER, SPLIT)


class ApptStatus:
    PENDING = "pending"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    ALL = (PENDING, CONFIRMED, IN_PROGRESS, COMPLETED, CANCELLED, NO_SHOW)
    OCCUPYING = (PENDING, CONFIRMED, IN_PROGRESS, COMPLETED)
    CLOSABLE = (PENDING, CONFIRMED, IN_PROGRESS)
    EDITABLE = (PENDING, CONFIRMED, IN_PROGRESS)


class PaymentMethod:
    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"
    ALL = (CASH, CARD, TRANSFER)


class OrderStatus:
    COMPLETED = "completed"
    UNPAID = "unpaid"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    ALL = (COMPLETED, UNPAID, CANCELLED, REFUNDED)
    ACCRUING = (COMPLETED, UNPAID)
    CLOSE_AS = (COMPLETED, UNPAID)


class PayoutTag:
    ADVANCE = "advance"
    BONUS = "bonus"
    PRODUCT_BONUS = "product_bonus"
    CORRECTION_OVERPAYMENT = "correction_overpayment"
    ALL = (ADVANCE, BONUS, PRODUCT_BONUS, CORRECTION_OVERPAYMENT)


class ShiftStatus:
    OPEN = "open"
    CLOSED = "closed"
    PAID = "paid"
    ALL = (OPEN, CLOSED, PAID)


class ExpenseCategory:
    RENT = "rent"
    UTILITIES = "utilities"
    ADVERTISING = "advertising"
    MATERIALS = "materials"
    EQUIPMENT = "equipment"
    HOUSEHOLD = "household"
    FUEL = "fuel"
    OTHER = "other"
    ALL = (RENT, UTILITIES, ADVERTISING, MATERIALS, EQUIPMENT, HOUSEHOLD, FUEL, OTHER)
    CASH_FLOW_ONLY = (MATERIALS, FUEL)


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
    master_rate_bp: Mapped[int] = mapped_column(Integer, default=5000, server_default="5000")
    materials_mode: Mapped[str | None] = mapped_column(String(16))
    schedule: Mapped[dict | None] = mapped_column(JSON)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_link_code: Mapped[str | None] = mapped_column(String(16), unique=True)
    bot_pin_hash: Mapped[str | None] = mapped_column(String(255))
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    booking_lock: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    services: Mapped[list["Service"]] = relationship(secondary=master_services)


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    section: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(64))
    subcategory: Mapped[str | None] = mapped_column(String(64))
    duration_min: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    price_from: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    needs_materials: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Material(Base):

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    tone: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(16), default="шт")
    pack_name: Mapped[str] = mapped_column(String(16), default="шт", server_default="шт")
    pack_size: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    unit_cost: Mapped[int] = mapped_column(Integer)
    markup_bp: Mapped[int] = mapped_column(Integer, default=10000, server_default="10000")
    stock_milli: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    min_stock_milli: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class StockMove(Base):

    __tablename__ = "stock_moves"
    __table_args__ = (Index("ix_stock_material", "material_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_type: Mapped[str] = mapped_column(String(16), default="material", server_default="material")
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", name="fk_stock_moves_product_id"))
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    kind: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(16))
    qty_milli: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[int] = mapped_column(Integer, default=0)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    user_id: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)


class Product(Base):

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    sku: Mapped[str | None] = mapped_column(String(64), unique=True)
    barcode: Mapped[str | None] = mapped_column(String(64), unique=True)
    category: Mapped[str | None] = mapped_column(String(64))
    cost_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    sale_price_cents: Mapped[int] = mapped_column(Integer)
    markup_bp: Mapped[int] = mapped_column(Integer, default=10000, server_default="10000")
    stock_qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class OrderProduct(Base):

    __tablename__ = "order_products"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", name="fk_order_products_order_id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", name="fk_order_products_product_id"))
    name: Mapped[str] = mapped_column(String(128))
    qty: Mapped[int] = mapped_column(Integer)
    sale_price_snapshot_cents: Mapped[int] = mapped_column(Integer)
    cost_price_snapshot_cents: Mapped[int] = mapped_column(Integer, default=0)
    master_bonus_cents: Mapped[int] = mapped_column(Integer)
    refunded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    product: Mapped["Product"] = relationship()

    @property
    def total(self) -> int:
        return self.sale_price_snapshot_cents * self.qty


class BonusAccrual(Base):

    __tablename__ = "bonus_accruals"
    __table_args__ = (Index("ix_bonus_master_unpaid", "master_id", "payout_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_bonus_accruals_master_id"))
    order_product_id: Mapped[int | None] = mapped_column(ForeignKey("order_products.id", name="fk_bonus_accruals_op_id"))
    amount: Mapped[int] = mapped_column(Integer)
    accrued_on: Mapped[date] = mapped_column(Date)
    payout_id: Mapped[int | None] = mapped_column(ForeignKey("payouts.id", name="fk_bonus_accruals_payout_id"))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ScheduleDay(Base):

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
    phone: Mapped[str | None] = mapped_column(String(32), unique=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    reminders: Mapped[bool | None] = mapped_column(Boolean)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    phone2: Mapped[str | None] = mapped_column(String(32))
    birthday: Mapped[date | None] = mapped_column(Date)
    telegram_username: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(32))
    wishes: Mapped[str | None] = mapped_column(Text)
    marketing_consent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime)
    search_name: Mapped[str | None] = mapped_column(String(160), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (Index("ix_appt_master_time", "master_id", "starts_at"), Index("ix_appt_starts", "starts_at"), Index("ix_appt_client", "client_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    price: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=ApptStatus.CONFIRMED)
    source: Mapped[str] = mapped_column(String(16), default="staff")
    comment: Mapped[str | None] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    awaiting_client: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    reminded_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    reminded_2h: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    close_reminded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": version}

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
    report: Mapped[dict | None] = mapped_column(JSON)
    payout_due: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": version}

    master: Mapped[User] = relationship()
    orders: Mapped[list["Order"]] = relationship(back_populates="shift")


class Order(Base):

    __tablename__ = "orders"
    __table_args__ = (Index("ix_order_closed", "closed_at"), Index("ix_order_master_closed", "master_id", "closed_at"), Index("ix_order_client", "client_id"), Index("ix_order_shift", "shift_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"))
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"))
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"))
    price_snapshot: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    materials_cost: Mapped[int] = mapped_column(Integer, default=0)
    payment_method: Mapped[str] = mapped_column(String(16))
    master_rate_bp: Mapped[int] = mapped_column(Integer, default=5000, server_default="5000")
    status: Mapped[str] = mapped_column(String(16), default=OrderStatus.COMPLETED, server_default="completed", index=True)
    materials_not_entered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": version}
    share_bp: Mapped[int | None] = mapped_column(Integer)
    materials_mode: Mapped[str | None] = mapped_column(String(16))
    master_earning: Mapped[int] = mapped_column(Integer)
    salon_earning: Mapped[int] = mapped_column(Integer)
    closed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    closed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    edited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime)

    appointment: Mapped[Appointment | None] = relationship(back_populates="order")
    shift: Mapped[Shift] = relationship(back_populates="orders")
    materials: Mapped[list["OrderMaterial"]] = relationship(
        cascade="all, delete-orphan", order_by="OrderMaterial.id"
    )
    products: Mapped[list["OrderProduct"]] = relationship(order_by="OrderProduct.id", viewonly=True)
    master: Mapped[User] = relationship(foreign_keys=[master_id])
    client: Mapped[Client | None] = relationship()
    service: Mapped[Service | None] = relationship()
    closer: Mapped[User] = relationship(foreign_keys=[closed_by])
    editor: Mapped[User | None] = relationship(foreign_keys=[edited_by])


class OrderMaterial(Base):
    __tablename__ = "order_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"))
    name: Mapped[str] = mapped_column(String(128))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    unit_price_snapshot: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)


class Payout(Base):
    __tablename__ = "payouts"
    __table_args__ = (Index("ix_payout_cash_shift", "cash_shift_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"), unique=True)
    cash_shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"))
    amount: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(16), default=PaymentMethod.CASH)
    note: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    confirmed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    tag: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": version}

    master: Mapped[User] = relationship(foreign_keys=[master_id])


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (Index("ix_expense_spent_on", "spent_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(Integer)
    spent_on: Mapped[date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    payment_method: Mapped[str | None] = mapped_column(String(16))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": version}


class MeterReading(Base):

    __tablename__ = "meter_readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(Date, unique=True)
    read_on: Mapped[date] = mapped_column(Date)
    prev_milli: Mapped[int] = mapped_column(BigInteger)
    curr_milli: Mapped[int] = mapped_column(BigInteger)
    used_milli: Mapped[int] = mapped_column(BigInteger)
    tariff: Mapped[int] = mapped_column(Integer)
    cost: Mapped[int] = mapped_column(Integer)
    paid_amount: Mapped[int | None] = mapped_column(Integer)
    paid_on: Mapped[date | None] = mapped_column(Date)
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expenses.id", name="fk_meter_readings_expense_id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    confirmed_by: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GeneratorRun(Base):

    __tablename__ = "generator_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    running: Mapped[bool | None] = mapped_column(Boolean, unique=True)
    hours_milli: Mapped[int] = mapped_column(Integer, default=0)
    fuel_lph_milli: Mapped[int] = mapped_column(Integer)
    fuel_price: Mapped[int] = mapped_column(Integer)
    amort_per_hour: Mapped[int] = mapped_column(Integer)
    fuel_milli: Mapped[int] = mapped_column(Integer, default=0)
    fuel_cost: Mapped[int] = mapped_column(Integer, default=0)
    amort_cost: Mapped[int] = mapped_column(Integer, default=0)
    total_cost: Mapped[int] = mapped_column(Integer, default=0)
    power_outage: Mapped[bool] = mapped_column(Boolean, default=True)
    corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    started_by: Mapped[int | None] = mapped_column(Integer)
    ended_by: Mapped[int | None] = mapped_column(Integer)


class MasterCharge(Base):

    __tablename__ = "master_charges"

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_master_charges_master_id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    charged_on: Mapped[date] = mapped_column(Date, index=True)
    period: Mapped[date] = mapped_column(Date)
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id", name="fk_master_charges_shift_id"))
    amount: Mapped[int] = mapped_column(Integer)
    dedupe_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("master_charges.id", name="fk_master_charges_reverses_id"))
    reversed: Mapped[bool] = mapped_column(Boolean, default=False)
    basis: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    master: Mapped[User] = relationship()


class LedgerEntry(Base):

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

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_ts", "ts"), Index("ix_audit_entity", "entity", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    user_id: Mapped[int | None] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(64))
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

    __tablename__ = "callback_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(128))
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Notification(Base):

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notif_pending", "sent_at"),
        Index("ix_notif_user", "user_id", "read_at"),
        UniqueConstraint("dedupe_key", "user_id", name="uq_notif_dedupe_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str | None] = mapped_column(String(32))
    level: Mapped[str | None] = mapped_column(String(12))
    group_key: Mapped[str | None] = mapped_column(String(64), index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(96))
    text: Mapped[str] = mapped_column(Text)
    buttons: Mapped[list | None] = mapped_column(JSON)
    photo_path: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    digest_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime)
    handled_by: Mapped[int | None] = mapped_column(Integer)


class PermissionRequest(Base):

    __tablename__ = "permission_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_permission_requests_user_id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_by: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict | None] = mapped_column(JSON)

    user: Mapped[User] = relationship()


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

    __tablename__ = "card_payments"
    __table_args__ = (Index("ix_card_payment_appt", "appointment_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    mode: Mapped[str] = mapped_column(String(8))
    jwt: Mapped[str | None] = mapped_column(Text)
    rid: Mapped[str | None] = mapped_column(String(64))
    close_params: Mapped[dict | None] = mapped_column(JSON)
    transaction_id: Mapped[str | None] = mapped_column(String(64))
    rrn: Mapped[str | None] = mapped_column(String(32))
    approval_code: Mapped[str | None] = mapped_column(String(16))
    masked_pan: Mapped[str | None] = mapped_column(String(32))
    payment_system: Mapped[str | None] = mapped_column(String(32))
    response_code: Mapped[str | None] = mapped_column(String(8))
    message: Mapped[str | None] = mapped_column(String(255))
    receipt: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TaxRate(Base):

    __tablename__ = "tax_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(24), index=True)
    rate_bp: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[int | None] = mapped_column(Integer)
    base: Mapped[str] = mapped_column(String(16), default="fixed")
    period: Mapped[str] = mapped_column(String(8), default="month")
    due_day: Mapped[int | None] = mapped_column(Integer)
    due_from: Mapped[str] = mapped_column(String(8), default="end")
    due_offset_months: Mapped[int] = mapped_column(Integer, default=0)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TaxAccrual(Base):

    __tablename__ = "tax_accruals"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(24))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    base_amount: Mapped[int] = mapped_column(Integer)
    rate_bp: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
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
    rates: Mapped[dict | None] = mapped_column(JSON)
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

    __tablename__ = "employee_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_employee_profiles_user_id"), unique=True)
    tin: Mapped[str | None] = mapped_column(String(10))
    position: Mapped[str | None] = mapped_column(String(128))
    hired_on: Mapped[date | None] = mapped_column(Date)
    fired_on: Mapped[date | None] = mapped_column(Date)
    main_job: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExpenseFile(Base):

    __tablename__ = "expense_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey("expenses.id", name="fk_expense_files_expense_id"), index=True)
    filename: Mapped[str] = mapped_column(String(160))
    content_type: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class IdempotencyKey(Base):

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    response_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class AuthThrottle(Base):

    __tablename__ = "auth_throttle"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_auth_throttle_scope_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(64))
    fails: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class LoginChallenge(Base):

    __tablename__ = "login_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_login_challenges_user_id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TrustedDevice(Base):

    __tablename__ = "trusted_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_trusted_devices_user_id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str | None] = mapped_column(String(160))
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


@event.listens_for(Client, "before_insert")
@event.listens_for(Client, "before_update")
def _client_search_name(_mapper, _conn, c: Client) -> None:
    c.search_name = " ".join((c.name or "").lower().split()) or None


class SmsCampaign(Base):

    __tablename__ = "sms_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    recipients: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[int] = mapped_column(Integer, default=0)
    expense_id: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SmsMessage(Base):

    __tablename__ = "sms_messages"
    __table_args__ = (Index("ix_sms_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", name="fk_sms_messages_client_id", ondelete="SET NULL"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("sms_campaigns.id", name="fk_sms_messages_campaign_id"), index=True)
    phone: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    provider_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
