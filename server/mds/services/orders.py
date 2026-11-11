from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import clock
from ..errors import Conflict, Forbidden, NotFound, ValidationError
from ..models import (
    Appointment,
    ApptStatus,
    Client,
    Material,
    Order,
    OrderMaterial,
    OrderStatus,
    PaymentMethod,
    Payout,
    Role,
    Shift,
    ShiftStatus,
    User,
)
from ..money import fmt
from . import notifications
from .common import Actor, audit, get_or_404, get_setting, ledger
from .finance import compute_split
from .users import master_rate

MASTER_HIDDEN_ORDER = ("salon_earning",)
MASTER_HIDDEN_REPORT = ("revenue", "materials_cost", "salon_earning", "price_total", "discounts", "products_revenue",
                        "by_payment", "services")


MISSING_LABELS = {"materials": "матеріали", "phone": "телефон клієнта"}


def order_missing(o: Order) -> list[str]:
    out = []
    if o.service is not None and o.service.needs_materials and not o.materials:
        out.append("materials")
    if o.client is not None and not o.client.phone:
        out.append("phone")
    return out


def master_can_edit(o: Order) -> bool:
    return o.shift is not None and o.shift.status == ShiftStatus.OPEN


def _touch(o: Order, actor: Actor) -> None:
    o.edited_by = actor.user_id
    o.edited_at = clock.now()


def order_for_master(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in MASTER_HIDDEN_ORDER}


def report_for_master(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k not in MASTER_HIDDEN_REPORT}
    if "orders" in out:
        out["orders"] = [order_for_master(o) for o in out["orders"]]
    return out


def order_public(o: Order) -> dict:
    return {
        "id": o.id,
        "appointment_id": o.appointment_id,
        "master_id": o.master_id,
        "master_name": o.master.full_name,
        "client_id": o.client_id,
        "client_name": o.client.name if o.client else "—",
        "service_id": o.service_id,
        "service_name": o.service.name if o.service else "Продаж товарів",
        "shift_id": o.shift_id,
        "price_snapshot": o.price_snapshot,
        "amount": o.amount,
        "discount": o.price_snapshot - o.amount,
        "materials_cost": o.materials_cost,
        "materials": [
            {"material_id": m.material_id, "name": m.name, "qty": m.qty / 1000, "unit_price_snapshot": m.unit_price_snapshot,
             "total": m.total}
            for m in o.materials
        ],
        "payment_method": o.payment_method,
        "master_rate_bp": o.master_rate_bp,
        "status": o.status,
        "version": o.version,
        "materials_not_entered": o.materials_not_entered,
        "master_earning": o.master_earning,
        "salon_earning": o.salon_earning,
        "closed_at": o.closed_at.isoformat(),
        "revision": o.revision,
        "needs_materials": bool(o.service and o.service.needs_materials) and not o.materials,
        "client_phone": o.client.phone if o.client else None,
        "products": [_line_public(x) for x in o.products],
        "products_total": sum(x.total for x in o.products if not x.refunded),
        "missing": order_missing(o),
        "shift_open": master_can_edit(o),
        "closed_by_name": o.closer.full_name if o.closer else None,
        "edited_by_name": o.editor.full_name if o.editor else None,
        "edited_at": o.edited_at.isoformat() if o.edited_at else None,
    }


def _line_public(x) -> dict:
    from .products import line_public

    return line_public(x)


def _order_snapshot(o: Order) -> dict:
    return {
        "price_snapshot": o.price_snapshot,
        "amount": o.amount,
        "materials_cost": o.materials_cost,
        "materials": [{"name": m.name, "qty": m.qty, "unit_price_snapshot": m.unit_price_snapshot, "total": m.total}
                      for m in o.materials],
        "payment_method": o.payment_method,
        "master_rate_bp": o.master_rate_bp,
        "status": o.status,
        "master_earning": o.master_earning,
        "salon_earning": o.salon_earning,
    }


def _build_materials(s: Session, items: list[dict]) -> list[OrderMaterial]:
    out = []
    for it in items or []:
        from .stock import to_milli

        qty_milli = to_milli(it.get("qty", 1))
        if qty_milli <= 0:
            raise ValidationError("Кількість матеріалу має бути більшою за 0")
        mat = None
        if it.get("material_id"):
            mat = get_or_404(s, Material, int(it["material_id"]), "Матеріал")
        name = (it.get("name") or (mat.name if mat else "")).strip()
        unit_cost = it.get("unit_cost")
        if unit_cost is None:
            if mat is None:
                raise ValidationError("Вкажіть собівартість матеріалу")
            unit_cost = mat.unit_cost
        unit_cost = int(unit_cost)
        if unit_cost < 0 or not name:
            raise ValidationError("Невірні дані матеріалу")
        if mat is not None and it.get("unit_cost") is None:
            from .stock import display_name, line_cost

            name = display_name(mat) if not it.get("name") else name
            total = line_cost(mat, qty_milli)
        else:
            total = (unit_cost * qty_milli + 500) // 1000
        out.append(OrderMaterial(material_id=mat.id if mat else None, name=name, qty=qty_milli, unit_price_snapshot=unit_cost,
                                 total=total))
    return out


def current_shift(s: Session, master_id: int) -> Shift | None:
    return s.scalar(select(Shift).where(Shift.master_id == master_id, Shift.status == ShiftStatus.OPEN))


def open_shift(s: Session, actor: Actor, master_id: int) -> Shift:
    if not (actor.is_director or actor.user_id == master_id):
        raise Forbidden("Можна відкрити лише власну зміну")
    master: User = get_or_404(s, User, master_id, "Майстра")
    if master.role != Role.MASTER:
        raise ValidationError("Зміни ведуться лише для майстрів")
    s.execute(update(User).where(User.id == master_id).values(booking_lock=User.booking_lock + 1))
    sh = current_shift(s, master_id)
    if sh:
        return sh
    sh = Shift(master_id=master_id, status=ShiftStatus.OPEN, opened_at=clock.now())
    s.add(sh)
    s.flush()
    audit(s, actor, "shift", sh.id, "open")
    return sh


def _resolve_price(actor: Actor, a: Appointment, price: int | None) -> int:
    if price is None:
        return int(a.price)
    if not isinstance(price, int) or isinstance(price, bool) or price < 0:
        raise ValidationError("Ціна (P) — ціле невід'ємне число копійок")
    if a.service.price_from or actor.is_director or price == a.price:
        return price
    raise ValidationError("Ціна послуги фіксована прайсом — змінює лише директор (виправленням замовлення)")


def _alert_salon_negative(s: Session, o: Order) -> None:
    if o.salon_earning >= 0:
        return
    from . import alerts

    alerts.raise_alert(
        s, "salon_negative",
        f"🔴 Салон у мінусі по замовленню #{o.id}: {fmt(o.salon_earning)}\n"
        f"Ціна {fmt(o.price_snapshot)}, сплачено {fmt(o.amount)}, майстру {fmt(o.master_earning)}, матеріали {fmt(o.materials_cost)}.",
        entity="order", entity_id=o.id,
    )


def close_order(
    s: Session,
    actor: Actor,
    appt_id: int,
    *,
    amount: int,
    payment_method: str,
    materials: list[dict] | None = None,
    idempotency_key: str | None = None,
    client_name: str | None = None,
    price: int | None = None,
    status: str = OrderStatus.COMPLETED,
    products: list[dict] | None = None,
) -> Order:
    if idempotency_key:
        existing = s.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
        if existing:
            if existing.appointment_id != appt_id:
                raise Conflict("Ключ ідемпотентності вже використаний для іншого запису")
            return existing
    a: Appointment | None = s.get(Appointment, appt_id)
    if a is None:
        raise NotFound(f"Запис #{appt_id} не знайдено")
    if not (actor.is_director or actor.user_id == a.master_id):
        raise Forbidden("Можна закривати лише власні замовлення")
    if payment_method not in PaymentMethod.ALL:
        raise ValidationError("Оберіть спосіб оплати")
    if status not in OrderStatus.CLOSE_AS:
        raise ValidationError("Замовлення закривається як оплачене або неоплачене")
    if amount is None or not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise ValidationError("Вкажіть фактичну вартість")
    p_snapshot = _resolve_price(actor, a, price)
    if client_name is not None:
        client_name = " ".join(client_name.split())
        if len(client_name) < 2:
            raise ValidationError("Вкажіть ім'я клієнта")
        if len(client_name) > 128:
            raise ValidationError("Ім'я клієнта задовге")

    res = s.execute(
        update(Appointment)
        .where(Appointment.id == appt_id, Appointment.status.in_(ApptStatus.CLOSABLE))
        .values(status=ApptStatus.COMPLETED, updated_at=clock.now(), version=Appointment.version + 1)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        raise Conflict("Замовлення вже закрито або запис скасовано", code="already_closed")
    s.refresh(a)
    if client_name and client_name != a.client.name:
        before_name = a.client.name
        a.client.name = client_name
        audit(s, actor, "client", a.client_id, "rename", {"name": before_name}, {"name": client_name},
              "Уточнено при закритті замовлення")

    master: User = a.master
    rate = master_rate(master)
    mats = _build_materials(s, materials or [])
    mat_total = sum(m.total for m in mats)
    split = compute_split(p_snapshot, amount, mat_total, rate)
    shift = open_shift(s, actor, master.id)

    o = Order(
        appointment_id=a.id,
        idempotency_key=idempotency_key,
        master_id=master.id,
        client_id=a.client_id,
        service_id=a.service_id,
        shift_id=shift.id,
        price_snapshot=p_snapshot,
        amount=amount,
        materials_cost=mat_total,
        payment_method=payment_method,
        master_rate_bp=rate,
        status=status,
        materials_not_entered=bool(a.service.needs_materials) and not mats,
        master_earning=split.master,
        salon_earning=split.salon,
        closed_at=clock.now(),
        closed_by=actor.user_id or master.id,
        materials=mats,
    )
    s.add(o)
    try:
        s.flush()
    except IntegrityError:
        raise Conflict("Замовлення вже проведено", code="already_closed") from None
    from .stock import apply_order

    apply_order(s, actor, o)
    ledger(
        s, actor, "order_closed", "order", o.id,
        master_id=master.id, amount=amount, master_amount=split.master,
        salon_amount=split.salon, materials_amount=mat_total,
        details={"payment_method": payment_method, "price_snapshot": p_snapshot, "master_rate_bp": rate,
                 "status": status, "appointment_id": a.id},
    )
    audit(s, actor, "order", o.id, "close", None, _order_snapshot(o))
    if products:
        from .products import sell

        for it in products:
            sell(s, actor, o.id, int(it["product_id"]), it.get("qty", 1))
    _alert_salon_negative(s, o)
    return o


def _check_edit(actor: Actor, o: Order) -> None:
    if actor.is_director:
        return
    if actor.is_master:
        if o.master_id != actor.user_id:
            raise Forbidden("Можна редагувати лише власні замовлення")
        if not master_can_edit(o):
            raise Forbidden("Зміну закрито — замовлення виправляє директор")
        return
    raise Forbidden("Недостатньо прав")


def _check_version(o, expected: int | None) -> None:
    from .common import check_version

    check_version(o, expected, order_public)


def _repost(s: Session, actor: Actor, o: Order, kind: str, reason: str, **details) -> None:
    ledger(
        s, actor, "order_reversal", "order", o.id,
        master_id=o.master_id, amount=-o.amount, master_amount=-o.master_earning,
        salon_amount=-o.salon_earning, materials_amount=-o.materials_cost,
        details={"revision": o.revision, "reason": reason, "for": kind},
    )


def correct_order(s: Session, actor: Actor, order_id: int, data: dict, reason: str,
                  expected_version: int | None = None) -> Order:
    o: Order = get_or_404(s, Order, order_id, "Замовлення")
    _check_version(o, expected_version)
    if not actor.is_director:
        if not (set(data) <= {"materials"} and "materials" in data):
            raise Forbidden("Майстер може лише вказати матеріали у своєму замовленні; інше виправляє директор")
        _check_edit(actor, o)
        reason = reason or "Вказано використані матеріали"
    if o.status not in OrderStatus.ACCRUING:
        raise Conflict("Скасоване або повернене замовлення не виправляється")
    if not reason or not reason.strip():
        raise ValidationError("Вкажіть причину виправлення")
    if {"share_bp", "materials_mode"} & set(data):
        raise ValidationError("Режими матеріалів і індивідуальний відсоток скасовано (канон 0.3.0)")
    before = _order_snapshot(o)

    amount = int(data.get("amount", o.amount))
    p_snapshot = int(data.get("price", data.get("price_snapshot", o.price_snapshot)))
    if amount < 0 or p_snapshot < 0:
        raise ValidationError("Суми не можуть бути від'ємними")
    payment_method = data.get("payment_method", o.payment_method)
    if payment_method not in PaymentMethod.ALL:
        raise ValidationError("Невідомий спосіб оплати")
    if "materials" in data:
        from .stock import apply_order, reverse_order

        reverse_order(s, actor, o)
        o.materials = _build_materials(s, data["materials"])
        s.flush()
        apply_order(s, actor, o)
    mat_total = sum(m.total for m in o.materials)
    split = compute_split(p_snapshot, amount, mat_total, o.master_rate_bp)

    _repost(s, actor, o, "correct", reason)
    o.price_snapshot, o.amount, o.payment_method = p_snapshot, amount, payment_method
    o.materials_cost, o.master_earning, o.salon_earning = mat_total, split.master, split.salon
    o.materials_not_entered = bool(o.service and o.service.needs_materials) and not o.materials
    o.revision += 1
    _touch(o, actor)
    s.flush()
    ledger(
        s, actor, "order_corrected", "order", o.id,
        master_id=o.master_id, amount=amount, master_amount=split.master,
        salon_amount=split.salon, materials_amount=mat_total,
        details={"revision": o.revision, "reason": reason, "payment_method": payment_method, "price_snapshot": p_snapshot},
    )
    audit(s, actor, "order", o.id, "correct", before, _order_snapshot(o), reason)
    _alert_salon_negative(s, o)
    _notify_earning_change(s, actor, o, before["master_earning"], reason)
    return o


def _notify_earning_change(s: Session, actor: Actor, o: Order, before: int, reason: str) -> None:
    if o.master_id == actor.user_id or before == o.master_earning:
        return
    from . import alerts

    alerts.earnings_changed(s, o, before, reason)


def mark_paid(s: Session, actor: Actor, order_id: int, *, payment_method: str, amount: int | None = None,
              reason: str | None = None, expected_version: int | None = None) -> Order:
    actor.require(Role.DIRECTOR)
    o: Order = get_or_404(s, Order, order_id, "Замовлення")
    _check_version(o, expected_version)
    if o.status != OrderStatus.UNPAID:
        raise Conflict("Замовлення не має статусу «неоплачене»", code="not_unpaid")
    if payment_method not in PaymentMethod.ALL:
        raise ValidationError("Невідомий спосіб оплати")
    paid = o.amount if amount is None else int(amount)
    if paid < 0:
        raise ValidationError("Сума не може бути від'ємною")
    before = _order_snapshot(o)
    if paid != o.amount or payment_method != o.payment_method:
        split = compute_split(o.price_snapshot, paid, o.materials_cost, o.master_rate_bp)
        _repost(s, actor, o, "mark_paid", reason or "Оплата неоплаченого замовлення")
        o.amount, o.payment_method = paid, payment_method
        o.master_earning, o.salon_earning = split.master, split.salon
        o.revision += 1
        s.flush()
        ledger(s, actor, "order_corrected", "order", o.id, master_id=o.master_id, amount=paid,
               master_amount=split.master, salon_amount=split.salon, materials_amount=o.materials_cost,
               details={"revision": o.revision, "reason": "mark_paid", "payment_method": payment_method})
    o.status = OrderStatus.COMPLETED
    _touch(o, actor)
    s.flush()
    ledger(s, actor, "order_paid", "order", o.id, master_id=o.master_id,
           details={"payment_method": payment_method, "amount": paid})
    audit(s, actor, "order", o.id, "mark_paid", before, _order_snapshot(o), reason)
    _alert_salon_negative(s, o)
    return o


def _void(s: Session, actor: Actor, order_id: int, status: str, reason: str, expected_version: int | None) -> Order:
    actor.require(Role.DIRECTOR)
    if not reason or not reason.strip():
        raise ValidationError("Вкажіть причину")
    o: Order = get_or_404(s, Order, order_id, "Замовлення")
    _check_version(o, expected_version)
    if o.status not in OrderStatus.ACCRUING:
        raise Conflict("Замовлення вже скасоване або повернене", code="already_voided")
    before = _order_snapshot(o)
    keep_materials = status == OrderStatus.REFUNDED
    if not keep_materials:
        from .stock import reverse_order

        reverse_order(s, actor, o, note=f"Скасування замовлення #{o.id}")
    mat = 0 if keep_materials else -o.materials_cost
    ledger(s, actor, f"order_{status}", "order", o.id, master_id=o.master_id,
           amount=-o.amount, master_amount=-o.master_earning, materials_amount=mat,
           salon_amount=-o.amount + o.master_earning - mat,
           details={"reason": reason, "status_before": o.status})
    o.status = status
    if status == OrderStatus.CANCELLED and o.appointment is not None:
        o.appointment.status = ApptStatus.CANCELLED
        o.appointment.cancel_reason = reason
    from .products import refund_all_lines

    refund_all_lines(s, actor, o, reason)
    _touch(o, actor)
    s.flush()
    audit(s, actor, "order", o.id, status, before, _order_snapshot(o), reason)
    from . import alerts

    alerts.order_voided(s, o, reason)
    alerts.check_master_balance(s, o.master_id)
    return o


def refund_order(s: Session, actor: Actor, order_id: int, reason: str, expected_version: int | None = None) -> Order:
    return _void(s, actor, order_id, OrderStatus.REFUNDED, reason, expected_version)


def cancel_order(s: Session, actor: Actor, order_id: int, reason: str, expected_version: int | None = None) -> Order:
    return _void(s, actor, order_id, OrderStatus.CANCELLED, reason, expected_version)


def set_order_phone(s: Session, actor: Actor, order_id: int, phone: str) -> Order:
    from .catalog import client_public, normalize_phone

    o: Order = get_or_404(s, Order, order_id, "Замовлення")
    _check_edit(actor, o)
    phone_n = normalize_phone(phone)
    client = o.client
    if client.phone == phone_n:
        return o
    if client.phone and not actor.is_director:
        raise Forbidden("Телефон клієнта вже вказано — змінює адміністратор у картці клієнта")
    other = s.scalar(select(Client).where(or_(Client.phone == phone_n, Client.phone2 == phone_n), Client.id != client.id))
    before = {"client_id": client.id, "client_name": client.name, "phone": client.phone}
    if other is not None:
        o.client_id = other.id
        o.appointment.client_id = other.id
        s.flush()
        s.refresh(o, ["client"])
        reason = f"Номер належить клієнту {other.name} — замовлення перенесено до його картки"
    else:
        cb = client_public(client)
        client.phone = phone_n
        audit(s, actor, "client", client.id, "update", cb, client_public(client), f"Телефон вказано в замовленні #{o.id}")
        reason = "Вказано телефон клієнта"
    _touch(o, actor)
    s.flush()
    audit(s, actor, "order", o.id, "set_phone", before,
          {"client_id": o.client_id, "client_name": o.client.name, "phone": o.client.phone}, reason)
    return o


def order_history(s: Session, actor: Actor, o: Order) -> list[dict]:
    from ..models import AuditLog

    if not actor.is_director and o.master_id != actor.user_id:
        raise Forbidden("Немає доступу до цього замовлення")
    rows = s.scalars(select(AuditLog).where(AuditLog.entity == "order", AuditLog.entity_id == o.id).order_by(AuditLog.id.desc()))
    names = dict(s.execute(select(User.id, User.full_name)).all())
    return [{"id": a.id, "ts": a.ts.isoformat(), "actor": names.get(a.user_id) or a.actor, "action": a.action,
             "before": a.before, "after": a.after, "reason": a.reason} for a in rows]


def incomplete_orders(s: Session, sh: Shift) -> list[Order]:
    q = select(Order).where(Order.shift_id == sh.id, Order.status.in_(OrderStatus.ACCRUING)).order_by(Order.closed_at)
    return [o for o in s.scalars(q) if order_missing(o)]


def list_orders(
    s: Session, actor: Actor, *, date_from: date | None = None, date_to: date | None = None, master_id: int | None = None,
    status: str | None = None, materials_not_entered: bool | None = None,
) -> list[Order]:
    if actor.is_master:
        master_id = actor.user_id
    else:
        actor.require(Role.DIRECTOR)
    q = select(Order)
    if date_from is not None:
        q = q.where(Order.closed_at >= datetime.combine(date_from, time.min))
    if date_to is not None:
        q = q.where(Order.closed_at < datetime.combine(date_to + timedelta(days=1), time.min))
    if status:
        if status not in OrderStatus.ALL:
            raise ValidationError("Невідомий статус замовлення")
        q = q.where(Order.status == status)
    if materials_not_entered is not None:
        q = q.where(Order.materials_not_entered.is_(bool(materials_not_entered)))
    if master_id:
        q = q.where(Order.master_id == master_id)
    q = q.options(joinedload(Order.master), joinedload(Order.client), joinedload(Order.service), selectinload(Order.materials),
                  joinedload(Order.shift), joinedload(Order.closer), joinedload(Order.editor))
    return list(s.scalars(q.order_by(Order.closed_at.desc())).unique())


def shift_report(s: Session, sh: Shift) -> dict:
    orders = list(s.scalars(select(Order).where(Order.shift_id == sh.id).order_by(Order.closed_at)))
    end = sh.closed_at or clock.now()
    day_from = datetime.combine(sh.opened_at.date(), time.min)
    day_to = datetime.combine(end.date() + timedelta(days=1), time.min)
    cancellations = s.scalar(
        select(func.count(Appointment.id)).where(
            Appointment.master_id == sh.master_id,
            Appointment.status.in_((ApptStatus.CANCELLED, ApptStatus.NO_SHOW)),
            Appointment.starts_at >= day_from,
            Appointment.starts_at < day_to,
        )
    ) or 0
    voided = [o for o in orders if o.status not in OrderStatus.ACCRUING]
    unpaid = [o for o in orders if o.status == OrderStatus.UNPAID]
    orders = [o for o in orders if o.status in OrderStatus.ACCRUING]
    paid_orders = [o for o in orders if o.status == OrderStatus.COMPLETED]
    by_service: dict[str, dict] = {}
    by_payment: dict[str, int] = {}
    for o in orders:
        name = o.service.name if o.service else "Продаж товарів"
        row = by_service.setdefault(name, {"service": name, "count": 0, "revenue": 0})
        row["count"] += 1
        row["revenue"] += o.amount if o.status == OrderStatus.COMPLETED else 0
    products_revenue = 0
    products_bonus = 0
    for o in paid_orders:
        prod = sum(x.total for x in o.products if not x.refunded)
        products_revenue += prod
        products_bonus += sum(x.master_bonus_cents for x in o.products if not x.refunded)
        by_payment[o.payment_method] = by_payment.get(o.payment_method, 0) + o.amount + prod
    master_earning = sum(o.master_earning for o in orders)
    revenue = sum(o.amount for o in paid_orders)
    cash = by_payment.get("cash", 0)
    till_total = revenue + products_revenue
    takes_cash = bool(get_setting(s, "master_takes_cash"))
    take = min(master_earning, cash) if takes_cash else 0
    director_pays = max(master_earning - take, 0)
    return {
        "shift_id": sh.id,
        "master_id": sh.master_id,
        "master_name": sh.master.full_name,
        "opened_at": sh.opened_at.isoformat(),
        "closed_at": sh.closed_at.isoformat() if sh.closed_at else None,
        "clients_count": len({o.client_id for o in orders}),
        "services_count": len(orders),
        "services": sorted(by_service.values(), key=lambda r: -r["count"]),
        "revenue": revenue,
        "materials_cost": sum(o.materials_cost for o in paid_orders),
        "master_earning": master_earning,
        "salon_earning": sum(o.salon_earning for o in paid_orders),
        "price_total": sum(o.price_snapshot for o in paid_orders),
        "products_revenue": products_revenue,
        "products_bonus": products_bonus,
        "discounts": sum(o.price_snapshot - o.amount for o in paid_orders),
        "unpaid": {"count": len(unpaid), "amount": sum(o.amount for o in unpaid)},
        "voided": len(voided),
        "by_payment": by_payment,
        "cancellations": cancellations,
        "cash": {
            "from_clients": cash,
            "noncash": till_total - cash,
            "master_takes": take,
            "leave_in_till": cash - take,
            "director_pays": director_pays,
            "mode": "take_from_till" if takes_cash else "director_pays_all",
        },
        "payout_due": director_pays,
        "orders": [order_public(o) for o in orders],
    }


def shift_payouts(s: Session, shift_ids: list[int]) -> dict:
    out: dict = {}
    if not shift_ids:
        return out
    rows = s.scalars(select(Payout).where(or_(Payout.shift_id.in_(shift_ids), Payout.cash_shift_id.in_(shift_ids))))
    for p in rows:
        if p.shift_id in shift_ids:
            out.setdefault(("shift", p.shift_id), p)
        if p.cash_shift_id in shift_ids:
            out.setdefault(("cash", p.cash_shift_id), p)
    return out


def shift_public(s: Session, sh: Shift, payouts: dict | None = None, for_master: bool = False) -> dict:
    if payouts is None:
        payouts = shift_payouts(s, [sh.id])
    payout = payouts.get(("shift", sh.id))
    cash_payout = payouts.get(("cash", sh.id))
    report = sh.report if sh.status != ShiftStatus.OPEN and sh.report else shift_report(s, sh)
    return {
        "id": sh.id,
        "master_id": sh.master_id,
        "master_name": sh.master.full_name,
        "status": sh.status,
        "opened_at": sh.opened_at.isoformat(),
        "closed_at": sh.closed_at.isoformat() if sh.closed_at else None,
        "payout_due": sh.payout_due if sh.payout_due is not None else report["payout_due"],
        "report": report_for_master(report) if for_master else report,
        "view": "master" if for_master else "director",
        "version": sh.version,
        "payout": payout_public(payout) if payout else None,
        "cash_payout": payout_public(cash_payout) if cash_payout else None,
    }


def close_shift(s: Session, actor: Actor, master_id: int, *, force: bool = False) -> Shift:
    if not (actor.is_director or actor.user_id == master_id):
        raise Forbidden("Можна закрити лише власну зміну")
    s.execute(update(User).where(User.id == master_id).values(booking_lock=User.booking_lock + 1))
    sh = current_shift(s, master_id)
    if sh is None:
        raise Conflict("Немає відкритої зміни")
    bad = incomplete_orders(s, sh)
    if force and actor.is_director:
        no_mats = [o for o in bad if "materials" in order_missing(o)]
        if no_mats:
            lines = "; ".join(f"#{o.id} {o.service.name if o.service else ''}" for o in no_mats[:5])
            raise Conflict(f"Примусове закриття: спершу внесіть матеріали ({lines})", code="materials_required")
    elif bad:
        lines = "; ".join(f"#{o.id} {o.client.name if o.client else ''} — {', '.join(MISSING_LABELS[k] for k in order_missing(o))}"
                          for o in bad[:5])
        raise Conflict(f"Не можна закрити зміну: заповніть замовлення ({lines})", code="incomplete_orders")
    sh.closed_at = clock.now()
    sh.status = ShiftStatus.CLOSED
    s.flush()
    report = shift_report(s, sh)
    sh.report = {k: v for k, v in report.items() if k != "orders"} | {"order_ids": [o["id"] for o in report["orders"]]}
    sh.payout_due = report["payout_due"]
    take = report["cash"]["master_takes"]
    if take > 0:
        p = Payout(master_id=master_id, cash_shift_id=sh.id, amount=take, method=PaymentMethod.CASH,
                   note=f"Взято з каси при закритті зміни #{sh.id}", paid_at=sh.closed_at, confirmed_by=actor.user_id or master_id)
        s.add(p)
        s.flush()
        ledger(s, actor, "payout", "payout", p.id, master_id=master_id, amount=take, master_amount=-take,
               details={"shift_id": sh.id, "method": "cash", "from_till": True})
    if sh.payout_due <= 0:
        sh.status = ShiftStatus.PAID
    audit(s, actor, "shift", sh.id, "close", None, sh.report)
    ledger(
        s, actor, "shift_closed", "shift", sh.id, master_id=master_id,
        amount=report["revenue"], master_amount=report["master_earning"],
        salon_amount=report["salon_earning"], materials_amount=report["materials_cost"],
    )
    notifications.notify_directors(
        s,
        f"📋 Зміну закрито: {sh.master.full_name}\n"
        f"Клієнтів: {report['clients_count']}, послуг: {report['services_count']}, скасувань: {report['cancellations']}\n"
        f"Виручка: {fmt(report['revenue'])} (готівка {fmt(report['cash']['from_clients'])}, картка/переказ {fmt(report['cash']['noncash'])})\n"
        f"Заробіток майстра: {fmt(report['master_earning'])}\n"
        + f"  • взяв готівкою з каси: {fmt(report['cash']['master_takes'])}\n"
        f"  • переказати на карту: {fmt(report['payout_due'])}\n"
        f"Лишилось у касі: {fmt(report['cash']['leave_in_till'])}",
        exclude_user_id=actor.user_id,
    )
    return sh


def list_shifts(s: Session, actor: Actor, *, status: str | None = None, master_id: int | None = None, limit: int = 100) -> list[Shift]:
    if actor.is_master:
        master_id = actor.user_id
    else:
        actor.require(Role.DIRECTOR)
    q = select(Shift)
    if status:
        q = q.where(Shift.status == status)
    if master_id:
        q = q.where(Shift.master_id == master_id)
    q = q.options(joinedload(Shift.master))
    return list(s.scalars(q.order_by(Shift.opened_at.desc()).limit(limit)))


def get_shift(s: Session, actor: Actor, shift_id: int) -> Shift:
    sh: Shift = get_or_404(s, Shift, shift_id, "Зміну")
    if not actor.is_director and actor.user_id != sh.master_id:
        raise Forbidden("Недостатньо прав")
    return sh


def payout_public(p: Payout) -> dict:
    return {
        "id": p.id,
        "master_id": p.master_id,
        "master_name": p.master.full_name,
        "shift_id": p.shift_id,
        "amount": p.amount,
        "method": p.method,
        "note": p.note,
        "from_till": p.cash_shift_id is not None,
        "tag": p.tag,
        "paid_at": p.paid_at.isoformat(),
    }


def confirm_payout(
    s: Session,
    actor: Actor,
    *,
    master_id: int,
    amount: int,
    shift_id: int | None = None,
    method: str = PaymentMethod.CASH,
    note: str | None = None,
    tag: str | None = None,
) -> Payout:
    from ..models import PayoutTag

    actor.require(Role.DIRECTOR)
    if amount <= 0:
        raise ValidationError("Сума виплати має бути більшою за 0")
    if tag is not None and (tag not in PayoutTag.ALL or tag == PayoutTag.PRODUCT_BONUS):
        raise ValidationError("Тег виплати: advance, bonus або correction_overpayment (бонус за товари — окремою кнопкою)")
    if tag is not None and shift_id is not None:
        raise ValidationError("Тег — лише для довільної виплати (без зміни)")
    if method not in PaymentMethod.ALL:
        raise ValidationError("Невідомий спосіб виплати")
    master: User = get_or_404(s, User, master_id, "Майстра")
    if shift_id is not None:
        res = s.execute(
            update(Shift)
            .where(Shift.id == shift_id, Shift.master_id == master_id, Shift.status == ShiftStatus.CLOSED)
            .values(status=ShiftStatus.PAID)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            raise Conflict("Зміна вже виплачена або ще не закрита", code="already_paid")
    p = Payout(master_id=master.id, shift_id=shift_id, amount=amount, method=method, note=note, tag=tag,
               paid_at=clock.now(), confirmed_by=actor.user_id or 0)
    s.add(p)
    try:
        s.flush()
    except IntegrityError:
        raise Conflict("Зміна вже виплачена", code="already_paid") from None
    ledger(s, actor, "payout", "payout", p.id, master_id=master.id, amount=amount, master_amount=-amount,
           details={"shift_id": shift_id, "method": method, "note": note, "tag": tag})
    audit(s, actor, "payout", p.id, "create", None, payout_public(p))
    notifications.enqueue(s, master.telegram_chat_id, f"💸 Виплату підтверджено: {fmt(amount)}")
    return p


def master_balance(s: Session, master_id: int) -> dict:
    accrued = s.scalar(select(func.coalesce(func.sum(Order.master_earning), 0)).where(
        Order.master_id == master_id, Order.status.in_(OrderStatus.ACCRUING))) or 0
    paid = s.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(
        Payout.master_id == master_id, or_(Payout.tag.is_(None), Payout.tag != "product_bonus"))) or 0
    return {"accrued": int(accrued), "paid": int(paid),
            "balance": int(accrued) - int(paid)}


def list_payouts(s: Session, actor: Actor, *, master_id: int | None = None, limit: int = 200) -> list[Payout]:
    if actor.is_master:
        master_id = actor.user_id
    else:
        actor.require(Role.DIRECTOR)
    q = select(Payout)
    if master_id:
        q = q.where(Payout.master_id == master_id)
    return list(s.scalars(q.order_by(Payout.paid_at.desc()).limit(limit)))


def needing_materials(s: Session, actor: Actor, days: int = 14) -> list[Order]:
    from ..models import OrderMaterial, Service

    q = (select(Order).join(Service, Order.service_id == Service.id)
         .where(Service.needs_materials.is_(True), Order.closed_at >= clock.now() - timedelta(days=days),
                ~select(OrderMaterial.id).where(OrderMaterial.order_id == Order.id).exists()))
    if actor.is_master:
        q = q.where(Order.master_id == actor.user_id)
    elif not actor.is_director:
        raise Forbidden("Недостатньо прав")
    return list(s.scalars(q.order_by(Order.closed_at.desc())))
