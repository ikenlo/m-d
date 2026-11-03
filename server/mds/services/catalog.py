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
        "section": x.section,
        "category": x.category,
        "subcategory": x.subcategory,
        "duration_min": x.duration_min,
        "price": x.price,
        "price_from": bool(x.price_from),
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
        section=(data.get("section") or "").strip() or None,
        category=(data.get("category") or "").strip() or None,
        subcategory=(data.get("subcategory") or "").strip() or None,
        duration_min=int(data["duration_min"]),
        price=int(data["price"]),
        price_from=bool(data.get("price_from", False)),
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
    for k in ("name", "section", "category", "subcategory", "duration_min", "price", "price_from", "is_active", "sort_order"):
        if k in data:
            setattr(x, k, data[k])
    audit(s, actor, "service", x.id, "update", before, service_public(x))
    return x


Node = tuple[str, list]  # (назва, діти): діти — вузли або, на останньому рівні, послуги


def service_tree(services: list[Service]) -> list[Node]:
    """Дерево каталогу для запису: розділ → тип → підтип → послуги.

    [(розділ, [(тип, [(підтип, [Service, ...]), ...]), ...]), ...]
    Порожній розділ/тип групується як «Послуги»; порожній підтип — "" (рівень пропускається).
    Порядок: «Чоловічі», «Жіночі», далі — за sort_order послуг.
    """
    tree: dict[str, dict[str, dict[str, list[Service]]]] = {}
    for x in services:
        tree.setdefault(x.section or "Послуги", {}).setdefault(x.category or "Послуги", {}) \
            .setdefault(x.subcategory or "", []).append(x)

    def first(node) -> int:
        if isinstance(node, list):
            return min(x.sort_order for x in node)
        return min(first(ch) for ch in node.values())

    def sec_key(sec: str) -> tuple:
        pref = SECTION_ORDER.index(sec.lower()) if sec.lower() in SECTION_ORDER else len(SECTION_ORDER)
        return (pref, first(tree[sec]), sec)

    def ordered(d: dict) -> list:
        return sorted(d.items(), key=lambda kv: (first(kv[1]), kv[0]))

    return [
        (sec, [(cat, [(sub, items) for sub, items in ordered(subs)]) for cat, subs in ordered(tree[sec])])
        for sec in sorted(tree, key=sec_key)
    ]


SECTION_ORDER = ["чоловічі", "жіночі"]


def list_services(s: Session, active_only: bool = True) -> list[Service]:
    q = select(Service)
    if active_only:
        q = q.where(Service.is_active.is_(True))
    return list(s.scalars(q.order_by(Service.sort_order, Service.name)))


# ---------------------------------------------------------------- матеріали

def material_public(m: Material) -> dict:
    from .stock import material_public as mp

    return mp(m)


MATERIAL_FIELDS = ("name", "tone", "category", "unit", "pack_name", "pack_size", "unit_cost", "is_active")


def _check_material(data: dict, creating: bool) -> dict:
    out = {k: data[k] for k in MATERIAL_FIELDS if k in data}
    for k in ("name", "tone", "category", "unit", "pack_name"):
        if k in out and isinstance(out[k], str):
            out[k] = out[k].strip() or None
    if creating and not out.get("name"):
        raise ValidationError("Вкажіть назву матеріалу")
    if "unit_cost" in out and (out["unit_cost"] is None or int(out["unit_cost"]) < 0):
        raise ValidationError("Вкажіть ціну упаковки")
    if "pack_size" in out:
        out["pack_size"] = int(out["pack_size"] or 1)
        if out["pack_size"] <= 0:
            raise ValidationError("Розмір упаковки має бути більшим за 0")
    if creating:
        out.setdefault("unit", "шт")
        out.setdefault("pack_name", out["unit"] if out.get("pack_size", 1) == 1 else "уп.")
        out["unit"] = out["unit"] or "шт"
        out["pack_name"] = out["pack_name"] or "шт"
    if "min_stock" in data:
        v = data["min_stock"]
        out["min_stock_milli"] = None if v in (None, "") else round(float(v) * 1000)
    return out


def create_material(s: Session, actor: Actor, data: dict) -> Material:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    vals = _check_material(data, creating=True)
    if "unit_cost" not in vals:
        raise ValidationError("Вкажіть ціну упаковки")
    m = Material(**vals)
    s.add(m)
    s.flush()
    if data.get("stock"):
        from .stock import receive

        receive(s, actor, m.id, data["stock"], note="Початковий залишок")
    audit(s, actor, "material", m.id, "create", None, material_public(m))
    return m


def update_material(s: Session, actor: Actor, material_id: int, data: dict) -> Material:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    m: Material = get_or_404(s, Material, material_id, "Матеріал")
    before = material_public(m)
    for k, v in _check_material(data, creating=False).items():
        setattr(m, k, v)
    s.flush()
    audit(s, actor, "material", m.id, "update", before, material_public(m))
    return m


def list_materials(s: Session, active_only: bool = True) -> list[Material]:
    from .stock import list_materials as lm

    return lm(s, active_only)


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
