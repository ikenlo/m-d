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
        "needs_materials": bool(x.needs_materials),
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
        needs_materials=bool(data.get("needs_materials", False)),
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
    for k in ("name", "section", "category", "subcategory", "duration_min", "price", "price_from", "needs_materials", "is_active", "sort_order"):
        if k in data:
            setattr(x, k, data[k])
    audit(s, actor, "service", x.id, "update", before, service_public(x))
    return x


Node = tuple[str, list]


def service_tree(services: list[Service]) -> list[Node]:
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


def material_public(m: Material) -> dict:
    from .stock import material_public as mp

    return mp(m)


MATERIAL_FIELDS = ("name", "tone", "category", "unit", "pack_name", "pack_size", "unit_cost", "is_active")
UNITS = ("г", "мл", "шт")
PACK_NAMES = ("шт", "туба", "тюбик", "банка", "флакон", "пляшка", "каністра", "упаковка", "пачка", "набір",
              "коробка", "рулон", "пара", "ампула", "саше", "балон")


def _check_material(data: dict, creating: bool) -> dict:
    out = {k: data[k] for k in MATERIAL_FIELDS if k in data}
    for k in ("name", "tone", "category", "unit", "pack_name"):
        if k in out and isinstance(out[k], str):
            out[k] = out[k].strip() or None
    if (creating or "name" in out) and not out.get("name"):
        raise ValidationError("Вкажіть назву матеріалу")
    for k, label in (("unit", "одиницю обліку"), ("pack_name", "назву упаковки")):
        if not creating and k in out and not out[k]:
            raise ValidationError(f"Вкажіть {label}")
    if out.get("unit") and out["unit"] not in UNITS:
        raise ValidationError("Одиниця обліку: г, мл або шт")
    if out.get("pack_name") and len(out["pack_name"]) > 16:
        raise ValidationError("Назва упаковки — до 16 символів")
    if "unit_cost" in out:
        if out["unit_cost"] is None:
            out["unit_cost"] = 0
        if int(out["unit_cost"]) < 0:
            raise ValidationError("Ціна упаковки не може бути від'ємною")
    if "pack_size" in out:
        out["pack_size"] = int(out["pack_size"] or 1)
        if out["pack_size"] <= 0:
            raise ValidationError("Розмір упаковки має бути більшим за 0")
    if creating:
        out.setdefault("unit", "шт")
        out["unit"] = out.get("unit") or "шт"
        if not out.get("pack_name"):
            out["pack_name"] = "шт" if out.get("pack_size", 1) == 1 else "упаковка"
    if "min_stock" in data:
        v = data["min_stock"]
        if v in (None, ""):
            out["min_stock_milli"] = None
        else:
            from .stock import to_milli

            out["min_stock_milli"] = to_milli(v)
            if out["min_stock_milli"] < 0:
                raise ValidationError("Мінімальний залишок не може бути від'ємним")
    return out


def create_material(s: Session, actor: Actor, data: dict) -> Material:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    vals = _check_material(data, creating=True)
    vals.setdefault("unit_cost", 0)
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
    vals = _check_material(data, creating=False)
    if "unit" in vals and vals["unit"] != m.unit:
        from ..models import StockMove

        if m.stock_milli or s.scalar(select(StockMove.id).where(StockMove.material_id == m.id).limit(1)):
            raise ValidationError("Одиницю обліку не можна змінити: вже є залишок або рух складу. "
                                  "Створіть новий матеріал, а цей позначте «не використовується».")
    for k, v in vals.items():
        setattr(m, k, v)
    s.flush()
    audit(s, actor, "material", m.id, "update", before, material_public(m))
    return m


def list_materials(s: Session, active_only: bool = True) -> list[Material]:
    from .stock import list_materials as lm

    return lm(s, active_only)


def normalize_phone(raw: str) -> str:
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
        "blocked": bool(c.blocked),
        "blocked_reason": c.blocked_reason,
        "phone2": c.phone2,
        "birthday": c.birthday.isoformat() if c.birthday else None,
        "telegram_username": c.telegram_username,
        "source": c.source,
        "wishes": c.wishes,
        "marketing_consent": bool(c.marketing_consent),
        "last_contact_at": c.last_contact_at.isoformat() if c.last_contact_at else None,
        "created_at": c.created_at.isoformat(),
    }


def find_client_by_phone(s: Session, phone: str) -> Client | None:
    return s.scalar(select(Client).where(Client.phone == normalize_phone(phone)))


def get_or_create_client(
    s: Session, actor: Actor, name: str, phone: str | None, telegram_user_id: int | None = None
) -> Client:
    if not (phone or "").strip():
        if not (name or "").strip():
            raise ValidationError("Вкажіть ім'я або телефон клієнта")
        c = Client(name=name.strip(), phone=None)
        s.add(c)
        s.flush()
        audit(s, actor, "client", c.id, "create", None, client_public(c))
        return c
    phone_n = normalize_phone(phone)
    c = s.scalar(select(Client).where(Client.phone == phone_n)) or s.scalar(select(Client).where(Client.phone2 == phone_n))
    if c is None:
        if not name.strip():
            raise ValidationError("Вкажіть ім'я клієнта")
        c = Client(name=name.strip(), phone=phone_n, telegram_user_id=telegram_user_id)
        s.add(c)
        s.flush()
        audit(s, actor, "client", c.id, "create", None, client_public(c))
    elif telegram_user_id and c.telegram_user_id != telegram_user_id:
        for other in s.scalars(select(Client).where(Client.telegram_user_id == telegram_user_id)):
            other.telegram_user_id = None
        c.telegram_user_id = telegram_user_id
    return c


def create_client(s: Session, actor: Actor, name: str, phone: str, notes: str | None = None) -> Client:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    c = get_or_create_client(s, actor, name, phone)
    if notes and notes != c.notes:
        before = c.notes
        c.notes = notes
        audit(s, actor, "client", c.id, "update", {"notes": before}, {"notes": notes})
    return c


def update_client(s: Session, actor: Actor, client_id: int, data: dict) -> Client:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    c: Client = get_or_404(s, Client, client_id, "Клієнта")
    before = client_public(c)
    if "phone" in data and not (data["phone"] or "").strip():
        c.phone = None
    elif "phone" in data:
        phone_n = normalize_phone(data["phone"])
        other = s.scalar(select(Client).where(Client.phone == phone_n, Client.id != c.id))
        if other:
            raise Conflict("Клієнт з таким телефоном уже існує")
        c.phone = phone_n
    for k in ("name", "notes"):
        if k in data:
            setattr(c, k, data[k])
    if "blocked" in data and data["blocked"] is not None:
        c.blocked = bool(data["blocked"])
        c.blocked_reason = (data.get("blocked_reason") or "").strip() or None if c.blocked else None
    audit(s, actor, "client", c.id, "update", before, client_public(c), data.get("blocked_reason"))
    return c


def search_clients(s: Session, q: str, limit: int = 50, master_id: int | None = None) -> list[Client]:
    from ..models import Appointment

    stmt = select(Client)
    if master_id:
        stmt = stmt.where(Client.id.in_(select(Appointment.client_id).where(Appointment.master_id == master_id)))
    if q:
        digits = re.sub(r"\D", "", q)
        conds = [Client.search_name.like(f"%{' '.join(q.lower().split())}%")]
        if digits:
            conds.append(Client.phone.like(f"%{digits}%"))
        stmt = stmt.where(or_(*conds))
    return list(s.scalars(stmt.order_by(Client.name).limit(limit)))


NO_PHONE_NAME = "Клієнти без номера"


def purge_clients_without_phone(s: Session, actor: Actor, dry_run: bool = False) -> dict:
    from datetime import datetime as _dt

    from .. import clock
    from ..models import Appointment, ApptStatus, Order

    actor.require(Role.DIRECTOR)
    now = clock.now()
    cands = [c for c in s.scalars(select(Client).where(Client.phone.is_(None))) if c.name != NO_PHONE_NAME]
    out = {"deleted": 0, "merged": 0, "kept_future": 0}
    keep = set(s.scalars(select(Appointment.client_id).where(
        Appointment.client_id.in_([c.id for c in cands] or [0]), Appointment.starts_at >= now,
        Appointment.status.in_((ApptStatus.PENDING, ApptStatus.CONFIRMED, ApptStatus.IN_PROGRESS)))))
    holder = None
    for c in cands:
        if c.id in keep:
            out["kept_future"] += 1
            continue
        has_history = s.scalar(select(Appointment.id).where(Appointment.client_id == c.id).limit(1)) or \
            s.scalar(select(Order.id).where(Order.client_id == c.id).limit(1))
        if dry_run:
            out["merged" if has_history else "deleted"] += 1
            continue
        if has_history:
            if holder is None:
                holder = s.scalar(select(Client).where(Client.phone.is_(None), Client.name == NO_PHONE_NAME))
                if holder is None:
                    holder = Client(name=NO_PHONE_NAME, phone=None, created_at=_dt.combine(now.date(), _dt.min.time()))
                    s.add(holder)
                    s.flush()
            for a in s.scalars(select(Appointment).where(Appointment.client_id == c.id)):
                a.client_id = holder.id
            for o in s.scalars(select(Order).where(Order.client_id == c.id)):
                o.client_id = holder.id
            out["merged"] += 1
        else:
            out["deleted"] += 1
        audit(s, actor, "client", c.id, "purge_no_phone", client_public(c), None,
              "Перенесено в «Клієнти без номера»" if has_history else "Видалено (без номера, без історії)")
        s.flush()
        s.delete(c)
    s.flush()
    return out
