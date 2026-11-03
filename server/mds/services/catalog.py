"""Довідники: послуги, матеріали, клієнти."""

from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..errors import Conflict, ValidationError
from ..models import Client, Material, Role, Service
from .common import Actor, audit, get_or_404


def service_public(x: Service) -> dict:
    return {
        "id": x.id,
        "name": x.name,
        "category": x.category,
        "duration_min": x.duration_min,
        "price": x.price,
        "is_active": x.is_active,
        "sort_order": x.sort_order,
    }


def _check_service(data: dict) -> None:
    if "duration_min" in data and not (5 <= int(data["duration_min"]) <= 12 * 60):
        raise ValidationError("Тривалість має бути від 5 хв до 12 год")
    if "price" in data and int(data["price"]) < 0:
        raise ValidationError("Ціна не може бути від'ємною")
    if "name" in data and not str(data["name"]).strip():
        raise ValidationError("Вкажіть назву")


def create_service(s: Session, actor: Actor, data: dict) -> Service:
    actor.require(Role.DIRECTOR)
    _check_service(data)
    x = Service(
        name=data["name"].strip(),
        category=data.get("category"),
        duration_min=int(data["duration_min"]),
        price=int(data["price"]),
        is_active=data.get("is_active", True),
        sort_order=data.get("sort_order", 0),
    )
    s.add(x)
    s.flush()
    audit(s, actor, "service", x.id, "create", None, service_public(x))
    return x


def update_service(s: Session, actor: Actor, service_id: int, data: dict) -> Service:
    actor.require(Role.DIRECTOR)
    _check_service(data)
    x: Service = get_or_404(s, Service, service_id, "Послугу")
    before = service_public(x)
    for k in ("name", "category", "duration_min", "price", "is_active", "sort_order"):
        if k in data:
            setattr(x, k, data[k])
    audit(s, actor, "service", x.id, "update", before, service_public(x))
    return x


def list_services(s: Session, active_only: bool = True) -> list[Service]:
    q = select(Service)
    if active_only:
        q = q.where(Service.is_active.is_(True))
    return list(s.scalars(q.order_by(Service.sort_order, Service.name)))


# ---------------------------------------------------------------- матеріали

def material_public(m: Material) -> dict:
    return {"id": m.id, "name": m.name, "unit": m.unit, "unit_cost": m.unit_cost, "is_active": m.is_active}


def create_material(s: Session, actor: Actor, data: dict) -> Material:
    actor.require(Role.DIRECTOR)
    if int(data.get("unit_cost", -1)) < 0 or not str(data.get("name", "")).strip():
        raise ValidationError("Вкажіть назву і собівартість")
    m = Material(name=data["name"].strip(), unit=data.get("unit") or "шт", unit_cost=int(data["unit_cost"]))
    s.add(m)
    s.flush()
    audit(s, actor, "material", m.id, "create", None, material_public(m))
    return m


def update_material(s: Session, actor: Actor, material_id: int, data: dict) -> Material:
    actor.require(Role.DIRECTOR)
    m: Material = get_or_404(s, Material, material_id, "Матеріал")
    before = material_public(m)
    if "unit_cost" in data and int(data["unit_cost"]) < 0:
        raise ValidationError("Собівартість не може бути від'ємною")
    for k in ("name", "unit", "unit_cost", "is_active"):
        if k in data:
            setattr(m, k, data[k])
    audit(s, actor, "material", m.id, "update", before, material_public(m))
    return m


def list_materials(s: Session, active_only: bool = True) -> list[Material]:
    q = select(Material)
    if active_only:
        q = q.where(Material.is_active.is_(True))
    return list(s.scalars(q.order_by(Material.name)))


# ----------------------------------------------------------------- клієнти

def normalize_phone(raw: str) -> str:
    """Приводить номер до формату +380XXXXXXXXX (для українських номерів)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    elif len(digits) == 9:
        digits = "380" + digits
    if len(digits) < 10 or len(digits) > 15:
        raise ValidationError("Невірний номер телефону")
    return "+" + digits


def client_public(c: Client) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "phone": c.phone,
        "notes": c.notes,
        "telegram_linked": c.telegram_user_id is not None,
        "created_at": c.created_at.isoformat(),
    }


def find_client_by_phone(s: Session, phone: str) -> Client | None:
    return s.scalar(select(Client).where(Client.phone == normalize_phone(phone)))


def get_or_create_client(
    s: Session, actor: Actor, name: str, phone: str, telegram_user_id: int | None = None
) -> Client:
    phone_n = normalize_phone(phone)
    c = s.scalar(select(Client).where(Client.phone == phone_n))
    if c is None:
        if not name.strip():
            raise ValidationError("Вкажіть ім'я клієнта")
        c = Client(name=name.strip(), phone=phone_n, telegram_user_id=telegram_user_id)
        s.add(c)
        s.flush()
        audit(s, actor, "client", c.id, "create", None, client_public(c))
    elif telegram_user_id and c.telegram_user_id != telegram_user_id:
        # клієнт підтвердив номер через Telegram (контакт) — прив'язуємо
        for other in s.scalars(select(Client).where(Client.telegram_user_id == telegram_user_id)):
            other.telegram_user_id = None
        c.telegram_user_id = telegram_user_id
    return c


def update_client(s: Session, actor: Actor, client_id: int, data: dict) -> Client:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    c: Client = get_or_404(s, Client, client_id, "Клієнта")
    before = client_public(c)
    if "phone" in data:
        phone_n = normalize_phone(data["phone"])
        other = s.scalar(select(Client).where(Client.phone == phone_n, Client.id != c.id))
        if other:
            raise Conflict("Клієнт з таким телефоном уже існує")
        c.phone = phone_n
    for k in ("name", "notes"):
        if k in data:
            setattr(c, k, data[k])
    audit(s, actor, "client", c.id, "update", before, client_public(c))
    return c


def search_clients(s: Session, q: str, limit: int = 50, master_id: int | None = None) -> list[Client]:
    """master_id — обмежити клієнтами, які записувались до цього майстра
    (майстер не бачить усю клієнтську базу салону)."""
    from ..models import Appointment

    stmt = select(Client)
    if master_id:
        stmt = stmt.where(Client.id.in_(select(Appointment.client_id).where(Appointment.master_id == master_id)))
    if q:
        digits = re.sub(r"\D", "", q)
        conds = [Client.name.ilike(f"%{q}%")]
        if digits:
            conds.append(Client.phone.like(f"%{digits}%"))
        stmt = stmt.where(or_(*conds))
    return list(s.scalars(stmt.order_by(Client.name).limit(limit)))
