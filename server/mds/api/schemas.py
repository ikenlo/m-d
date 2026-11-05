from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel as _PydanticModel
from pydantic import Field, model_validator

MAX_INT = 10**12


def _check_ints(v) -> None:
    if isinstance(v, bool):
        return
    if isinstance(v, int) and abs(v) > MAX_INT:
        raise ValueError("завелике число")
    if isinstance(v, (list, tuple)):
        for x in v:
            _check_ints(x)
    elif isinstance(v, dict):
        for x in v.values():
            _check_ints(x)


class BaseModel(_PydanticModel):

    @model_validator(mode="after")
    def _limit_ints(self):
        for name in type(self).model_fields:
            _check_ints(getattr(self, name))
        return self


class LoginIn(BaseModel):
    username: str
    password: str
    device_token: str | None = None


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
    bot_pin: str | None = None


class PasswordIn(BaseModel):
    password: str


class ServiceIn(BaseModel):
    name: str
    duration_min: int
    price: int
    section: str | None = None
    category: str | None = None
    subcategory: str | None = None
    price_from: bool = False
    needs_materials: bool = False
    is_active: bool = True
    sort_order: int = 0


class ServicePatch(BaseModel):
    name: str | None = None
    duration_min: int | None = None
    price: int | None = None
    section: str | None = None
    category: str | None = None
    subcategory: str | None = None
    price_from: bool | None = None
    needs_materials: bool | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class MaterialIn(BaseModel):
    name: str
    unit_cost: int | None = 0
    unit: str = "шт"
    tone: str | None = None
    category: str | None = None
    pack_name: str | None = None
    pack_size: int = 1
    min_stock: float | None = None
    stock: float | None = None


class MaterialPatch(BaseModel):
    name: str | None = None
    unit_cost: int | None = None
    unit: str | None = None
    tone: str | None = None
    category: str | None = None
    pack_name: str | None = None
    pack_size: int | None = None
    min_stock: float | None = None
    is_active: bool | None = None


class ClientIn(BaseModel):
    name: str
    phone: str
    notes: str | None = None


class ClientPatch(BaseModel):
    name: str | None = None
    phone: str | None = None
    notes: str | None = None
    blocked: bool | None = None
    blocked_reason: str | None = None


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
    confirm_off_hours: bool = False


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
    client_name: str | None = Field(default=None, max_length=128)


class OrderCorrectionIn(BaseModel):
    reason: str
    amount: int | None = None
    payment_method: str | None = None
    share_bp: int | None = None
    materials_mode: str | None = None
    materials: list[MaterialLine] | None = None


class OrderPhoneIn(BaseModel):
    phone: str = Field(max_length=32)


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
