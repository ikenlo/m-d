"""Закриття замовлень, зміни майстрів, виплати, виправлення директором."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, Forbidden, NotFound, ValidationError
from ..models import (
    Appointment,
    ApptStatus,
    Material,
    Order,
    OrderMaterial,
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
from .users import effective_terms


def order_public(o: Order) -> dict:
    return {
        "id": o.id,
        "appointment_id": o.appointment_id,
        "master_id": o.master_id,
        "master_name": o.master.full_name,
        "client_id": o.client_id,
        "client_name": o.client.name,
        "service_id": o.service_id,
        "service_name": o.service.name,
        "shift_id": o.shift_id,
        "amount": o.amount,
        "materials_cost": o.materials_cost,
        "materials": [
            {"material_id": m.material_id, "name": m.name, "qty": m.qty / 1000, "unit_cost": m.unit_cost, "total": m.total}
            for m in o.materials
        ],
        "payment_method": o.payment_method,
        "share_bp": o.share_bp,
        "materials_mode": o.materials_mode,
        "master_earning": o.master_earning,
        "salon_earning": o.salon_earning,
        "closed_at": o.closed_at.isoformat(),
        "revision": o.revision,
    }


def _order_snapshot(o: Order) -> dict:
    return {
        "amount": o.amount,
        "materials_cost": o.materials_cost,
        "materials": [{"name": m.name, "qty": m.qty, "unit_cost": m.unit_cost, "total": m.total} for m in o.materials],
        "payment_method": o.payment_method,
        "share_bp": o.share_bp,
        "materials_mode": o.materials_mode,
        "master_earning": o.master_earning,
        "salon_earning": o.salon_earning,
    }


def _build_materials(s: Session, items: list[dict]) -> list[OrderMaterial]:
    """items: [{material_id?, name?, qty, unit_cost?}]

    Матеріал зі складу (material_id): qty — у базових одиницях матеріалу (г / мл / шт),
    собівартість рахується від ціни упаковки. Довільний матеріал (без material_id):
    qty — штуки, unit_cost — ціна за штуку; на склад не впливає."""
    out = []
    for it in items or []:
        qty_milli = round(float(it.get("qty", 1)) * 1000)
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
        out.append(OrderMaterial(material_id=mat.id if mat else None, name=name, qty=qty_milli, unit_cost=unit_cost, total=total))
    return out


# ------------------------------------------------------------------- зміни

def current_shift(s: Session, master_id: int) -> Shift | None:
    return s.scalar(select(Shift).where(Shift.master_id == master_id, Shift.status == ShiftStatus.OPEN))


def open_shift(s: Session, actor: Actor, master_id: int) -> Shift:
    if not (actor.is_staff_manager or actor.user_id == master_id):
        raise Forbidden("Можна відкрити лише власну зміну")
    master: User = get_or_404(s, User, master_id, "Майстра")
    if master.role != Role.MASTER:
        raise ValidationError("Зміни ведуться лише для майстрів")
    # серіалізуємо відкриття зміни для майстра
    s.execute(update(User).where(User.id == master_id).values(booking_lock=User.booking_lock + 1))
    sh = current_shift(s, master_id)
    if sh:
        return sh
    sh = Shift(master_id=master_id, status=ShiftStatus.OPEN, opened_at=clock.now())
    s.add(sh)
    s.flush()
    audit(s, actor, "shift", sh.id, "open")
    return sh


# --------------------------------------------------------- закриття замовлення

def close_order(
    s: Session,
    actor: Actor,
    appt_id: int,
    *,
    amount: int,
    payment_method: str,
    materials: list[dict] | None = None,
    idempotency_key: str | None = None,
) -> Order:
    """Закрити замовлення: фіксує виручку, рахує частки, пише журнал.

    Повторний виклик з тим самим idempotency_key повертає вже створене
    замовлення (захист від подвійного натискання / повтору запиту).
    """
    if idempotency_key:
        existing = s.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
        if existing:
            if existing.appointment_id != appt_id:
                raise Conflict("Ключ ідемпотентності вже використаний для іншого запису")
            return existing
    a: Appointment | None = s.get(Appointment, appt_id)
    if a is None:
        raise NotFound(f"Запис #{appt_id} не знайдено")
    if not (actor.is_staff_manager or actor.user_id == a.master_id):
        raise Forbidden("Можна закривати лише власні замовлення")
    if payment_method not in PaymentMethod.ALL:
        raise ValidationError("Оберіть спосіб оплати")
    if amount is None or amount < 0:
        raise ValidationError("Вкажіть фактичну вартість")

    # Атомарний перехід статусу: лише один запит зможе перевести запис у COMPLETED.
    res = s.execute(
        update(Appointment)
        .where(Appointment.id == appt_id, Appointment.status.in_(ApptStatus.CLOSABLE))
        .values(status=ApptStatus.COMPLETED, updated_at=clock.now())
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        raise Conflict("Замовлення вже закрито або запис скасовано", code="already_closed")
    s.refresh(a)

    master: User = a.master
    share_bp, mode = effective_terms(s, master)
    mats = _build_materials(s, materials or [])
    mat_total = sum(m.total for m in mats)
    split = compute_split(amount, mat_total, share_bp, mode)
    shift = open_shift(s, actor, master.id)

    o = Order(
        appointment_id=a.id,
        idempotency_key=idempotency_key,
        master_id=master.id,
        client_id=a.client_id,
        service_id=a.service_id,
        shift_id=shift.id,
        amount=amount,
        materials_cost=mat_total,
        payment_method=payment_method,
        share_bp=share_bp,
        materials_mode=mode,
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
        details={"payment_method": payment_method, "share_bp": share_bp, "materials_mode": mode, "appointment_id": a.id},
    )
    audit(s, actor, "order", o.id, "close", None, _order_snapshot(o))
    return o


def correct_order(s: Session, actor: Actor, order_id: int, data: dict, reason: str) -> Order:
    """Виправлення закритого замовлення директором.

    Журнал: сторно старих сум + проведення нових; історія — в audit_log.
    Якщо зміна вже виплачена, різниця автоматично потрапляє в баланс майстра.
    """
    actor.require(Role.DIRECTOR)
    if not reason or not reason.strip():
        raise ValidationError("Вкажіть причину виправлення")
    o: Order = get_or_404(s, Order, order_id, "Замовлення")
    before = _order_snapshot(o)

    amount = int(data.get("amount", o.amount))
    payment_method = data.get("payment_method", o.payment_method)
    if payment_method not in PaymentMethod.ALL:
        raise ValidationError("Невідомий спосіб оплати")
    share_bp = int(data.get("share_bp", o.share_bp))
    mode = data.get("materials_mode", o.materials_mode)
    if "materials" in data:
        from .stock import apply_order, reverse_order

        reverse_order(s, actor, o)
        o.materials = _build_materials(s, data["materials"])
        s.flush()
        apply_order(s, actor, o)
    mat_total = sum(m.total for m in o.materials)
    split = compute_split(amount, mat_total, share_bp, mode)

    ledger(
        s, actor, "order_reversal", "order", o.id,
        master_id=o.master_id, amount=-o.amount, master_amount=-o.master_earning,
        salon_amount=-o.salon_earning, materials_amount=-o.materials_cost,
        details={"revision": o.revision, "reason": reason},
    )
    o.amount, o.payment_method, o.share_bp, o.materials_mode = amount, payment_method, share_bp, mode
    o.materials_cost, o.master_earning, o.salon_earning = mat_total, split.master, split.salon
    o.revision += 1
    s.flush()
    ledger(
        s, actor, "order_corrected", "order", o.id,
        master_id=o.master_id, amount=amount, master_amount=split.master,
        salon_amount=split.salon, materials_amount=mat_total,
        details={"revision": o.revision, "reason": reason, "payment_method": payment_method},
    )
    audit(s, actor, "order", o.id, "correct", before, _order_snapshot(o), reason)
    notifications.enqueue(
        s, o.master.telegram_chat_id,
        f"✏️ Директор виправив замовлення #{o.id} ({o.service.name}).\n"
        f"Ваш заробіток: {fmt(before['master_earning'])} → {fmt(o.master_earning)}\nПричина: {reason}",
    )
    return o


def list_orders(
    s: Session, actor: Actor, *, date_from: date, date_to: date, master_id: int | None = None
) -> list[Order]:
    if actor.is_master:
        master_id = actor.user_id
    else:
        actor.require(Role.DIRECTOR)
    q = select(Order).where(
        Order.closed_at >= datetime.combine(date_from, time.min),
        Order.closed_at < datetime.combine(date_to + timedelta(days=1), time.min),
    )
    if master_id:
        q = q.where(Order.master_id == master_id)
    return list(s.scalars(q.order_by(Order.closed_at.desc())))


# ------------------------------------------------------------ звіт зміни

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
    by_service: dict[str, dict] = {}
    by_payment: dict[str, int] = {}
    for o in orders:
        row = by_service.setdefault(o.service.name, {"service": o.service.name, "count": 0, "revenue": 0})
        row["count"] += 1
        row["revenue"] += o.amount
        by_payment[o.payment_method] = by_payment.get(o.payment_method, 0) + o.amount
    master_earning = sum(o.master_earning for o in orders)
    revenue = sum(o.amount for o in orders)
    # Розрахунок готівки: майстер бере заробіток із готівки клієнтів, решту готівки лишає в касі;
    # якщо готівки не вистачило (клієнти платили карткою) — різницю доплачує директор.
    cash = by_payment.get("cash", 0)
    takes_cash = bool(get_setting(s, "master_takes_cash"))
    take = min(max(master_earning, 0), cash) if takes_cash else 0
    director_pays = master_earning - take
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
        "materials_cost": sum(o.materials_cost for o in orders),
        "master_earning": master_earning,
        "salon_earning": sum(o.salon_earning for o in orders),
        "by_payment": by_payment,
        "cancellations": cancellations,
        "cash": {
            "from_clients": cash,
            "noncash": revenue - cash,
            "master_takes": take,
            "leave_in_till": cash - take,
            "director_pays": director_pays,
            "mode": "take_from_till" if takes_cash else "director_pays_all",
        },
        "payout_due": director_pays,
        "orders": [order_public(o) for o in orders],
    }


def shift_public(s: Session, sh: Shift) -> dict:
    payout = s.scalar(select(Payout).where(Payout.shift_id == sh.id))
    cash_payout = s.scalar(select(Payout).where(Payout.cash_shift_id == sh.id))
    report = sh.report if sh.status != ShiftStatus.OPEN and sh.report else shift_report(s, sh)
    return {
        "id": sh.id,
        "master_id": sh.master_id,
        "master_name": sh.master.full_name,
        "status": sh.status,
        "opened_at": sh.opened_at.isoformat(),
        "closed_at": sh.closed_at.isoformat() if sh.closed_at else None,
        "payout_due": sh.payout_due if sh.payout_due is not None else report["payout_due"],
        "report": report,
        "payout": payout_public(payout) if payout else None,
        "cash_payout": payout_public(cash_payout) if cash_payout else None,
    }


def close_shift(s: Session, actor: Actor, master_id: int) -> Shift:
    if not (actor.is_director or actor.user_id == master_id):
        raise Forbidden("Можна закрити лише власну зміну")
    s.execute(update(User).where(User.id == master_id).values(booking_lock=User.booking_lock + 1))
    sh = current_shift(s, master_id)
    if sh is None:
        raise Conflict("Немає відкритої зміни")
    sh.closed_at = clock.now()
    sh.status = ShiftStatus.CLOSED
    s.flush()
    report = shift_report(s, sh)
    sh.report = {k: v for k, v in report.items() if k != "orders"} | {"order_ids": [o["id"] for o in report["orders"]]}
    sh.payout_due = report["payout_due"]
    take = report["cash"]["master_takes"]
    if take > 0:
        # майстер забрав свій заробіток із каси — це фактична виплата
        p = Payout(master_id=master_id, cash_shift_id=sh.id, amount=take, method=PaymentMethod.CASH,
                   note=f"Взято з каси при закритті зміни #{sh.id}", paid_at=sh.closed_at, confirmed_by=actor.user_id or master_id)
        s.add(p)
        s.flush()
        ledger(s, actor, "payout", "payout", p.id, master_id=master_id, amount=take, master_amount=-take,
               details={"shift_id": sh.id, "method": "cash", "from_till": True})
    if sh.payout_due <= 0:
        sh.status = ShiftStatus.PAID  # усе вже отримано з каси — директору доплачувати нічого
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
        f"  • взяв готівкою з каси: {fmt(report['cash']['master_takes'])}\n"
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
    return list(s.scalars(q.order_by(Shift.opened_at.desc()).limit(limit)))


def get_shift(s: Session, actor: Actor, shift_id: int) -> Shift:
    sh: Shift = get_or_404(s, Shift, shift_id, "Зміну")
    if not actor.is_director and actor.user_id != sh.master_id:
        raise Forbidden("Недостатньо прав")
    return sh


# ---------------------------------------------------------------- виплати

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
) -> Payout:
    """Директор підтверджує фактичну виплату майстру (за зміну або довільну)."""
    actor.require(Role.DIRECTOR)
    if amount <= 0:
        raise ValidationError("Сума виплати має бути більшою за 0")
    if method not in PaymentMethod.ALL:
        raise ValidationError("Невідомий спосіб виплати")
    master: User = get_or_404(s, User, master_id, "Майстра")
    if shift_id is not None:
        # атомарно: лише одна виплата може перевести зміну в PAID
        res = s.execute(
            update(Shift)
            .where(Shift.id == shift_id, Shift.master_id == master_id, Shift.status == ShiftStatus.CLOSED)
            .values(status=ShiftStatus.PAID)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            raise Conflict("Зміна вже виплачена або ще не закрита", code="already_paid")
    p = Payout(master_id=master.id, shift_id=shift_id, amount=amount, method=method, note=note,
               paid_at=clock.now(), confirmed_by=actor.user_id or 0)
    s.add(p)
    try:
        s.flush()
    except IntegrityError:
        raise Conflict("Зміна вже виплачена", code="already_paid") from None
    ledger(s, actor, "payout", "payout", p.id, master_id=master.id, amount=amount, master_amount=-amount,
           details={"shift_id": shift_id, "method": method, "note": note})
    audit(s, actor, "payout", p.id, "create", None, payout_public(p))
    notifications.enqueue(s, master.telegram_chat_id, f"💸 Виплату підтверджено: {fmt(amount)}")
    return p


def master_balance(s: Session, master_id: int) -> dict:
    """Нараховано за весь час − виплачено = борг салону перед майстром."""
    accrued = s.scalar(select(func.coalesce(func.sum(Order.master_earning), 0)).where(Order.master_id == master_id)) or 0
    paid = s.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(Payout.master_id == master_id)) or 0
    return {"accrued": int(accrued), "paid": int(paid), "balance": int(accrued) - int(paid)}


def list_payouts(s: Session, actor: Actor, *, master_id: int | None = None, limit: int = 200) -> list[Payout]:
    if actor.is_master:
        master_id = actor.user_id
    else:
        actor.require(Role.DIRECTOR)
    q = select(Payout)
    if master_id:
        q = q.where(Payout.master_id == master_id)
    return list(s.scalars(q.order_by(Payout.paid_at.desc()).limit(limit)))
