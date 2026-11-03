"""Вхідні моделі API. Усі гроші — цілі копійки, відсотки — bp (5000 = 50%)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


class UserIn(BaseModel):
    username: str
    password: str
    full_name: str
    role: str
    phone: str | None = None
    share_bp: int | None = None
    materials_mode: str | None = None
    schedule: dict | None = None
    service_ids: list[int] | None = None
    bot_pin: str | None = None


class UserPatch(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    role: str | None = None
    is_active: bool | None = None
    share_bp: int | None = None
    materials_mode: str | None = None
    schedule: dict | None = None
    service_ids: list[int] | None = None
    bot_pin: str | None = None  # лише запис; у відповіді — bot_pin_set


class PasswordIn(BaseModel):
    password: str


class ServiceIn(BaseModel):
    name: str
    duration_min: int
    price: int
    category: str | None = None
    is_active: bool = True
    sort_order: int = 0


class ServicePatch(BaseModel):
    name: str | None = None
    duration_min: int | None = None
    price: int | None = None
    category: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class MaterialIn(BaseModel):
    name: str
    unit_cost: int
    unit: str = "шт"


class MaterialPatch(BaseModel):
    name: str | None = None
    unit_cost: int | None = None
    unit: str | None = None
    is_active: bool | None = None


class ClientIn(BaseModel):
    name: str
    phone: str
    notes: str | None = None


class ClientPatch(BaseModel):
    name: str | None = None
    phone: str | None = None
    notes: str | None = None


class AppointmentIn(BaseModel):
    master_id: int
    service_id: int
    starts_at: datetime
    client_id: int | None = None
    client_name: str | None = None
    client_phone: str | None = None
    duration_min: int | None = Field(default=None, ge=5, le=720)
    price: int | None = None
    comment: str | None = None
    status: str | None = None


class AppointmentPatch(BaseModel):
    starts_at: datetime | None = None
    master_id: int | None = None
    service_id: int | None = None
    duration_min: int | None = Field(default=None, ge=5, le=720)
    price: int | None = None
    comment: str | None = None


class StatusIn(BaseModel):
    status: str
    reason: str | None = None


class MaterialLine(BaseModel):
    material_id: int | None = None
    name: str | None = None
    qty: float = 1
    unit_cost: int | None = None


class CloseOrderIn(BaseModel):
    amount: int
    payment_method: str
    materials: list[MaterialLine] = []
    idempotency_key: str | None = Field(default=None, max_length=64)


class OrderCorrectionIn(BaseModel):
    reason: str
    amount: int | None = None
    payment_method: str | None = None
    share_bp: int | None = None
    materials_mode: str | None = None
    materials: list[MaterialLine] | None = None


class PayoutIn(BaseModel):
    master_id: int
    amount: int
    shift_id: int | None = None
    method: str = "cash"
    note: str | None = None


class ExpenseIn(BaseModel):
    category: str
    amount: int
    spent_on: date
    description: str | None = None


class ExpensePatch(BaseModel):
    category: str | None = None
    amount: int | None = None
    spent_on: date | None = None
    description: str | None = None
    reason: str | None = None
