"""Оплата карткою через «Термінал» ПриватБанку на планшеті (NFC) з автоматичним закриттям замовлення.

1. start_payment — перевіряє запис і суму, створює CardPayment(pending) і токен операції в банку,
   повертає deeplink для планшета. Повторний виклик для того самого запису повертає ту саму незавершену оплату.
2. Планшет відкриває «Термінал», клієнт прикладає картку.
3. check_payment — сервер САМ питає банк (check.php). Лише response_code "00" → paid і закриття замовлення
   (orders.close_order, спосіб «card», ключ ідемпотентності card-<id>) в одній транзакції.
   Повторна перевірка не задвоює ні оплату, ні замовлення; відповідь планшета на результат не впливає.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from .. import clock
from ..errors import Conflict, Forbidden, NotFound, ValidationError
from ..models import Appointment, ApptStatus, CardPayment
from . import card_terminal_providers as tp
from . import orders
from .common import Actor, audit, get_setting

log = logging.getLogger("mds.card_terminal")
MIN_AMOUNT = 100  # банк: мінімум 1,00 грн


def mode(s) -> str:
    return get_setting(s, "card_terminal_mode") or "off"


def public(p: CardPayment, deeplink: str | None = None) -> dict:
    return {"id": p.id, "appointment_id": p.appointment_id, "order_id": p.order_id, "amount": p.amount,
            "status": p.status, "mode": p.mode, "rrn": p.rrn, "approval_code": p.approval_code,
            "masked_pan": p.masked_pan, "payment_system": p.payment_system, "message": p.message,
            "created_at": p.created_at, "deeplink": deeplink}


def _appointment(s, actor: Actor, appt_id: int) -> Appointment:
    a = s.get(Appointment, appt_id)
    if a is None:
        raise NotFound(f"Запис #{appt_id} не знайдено")
    if not (actor.is_staff_manager or actor.user_id == a.master_id):
        raise Forbidden("Можна приймати оплату лише за власні записи")
    return a


def _provider(s):
    try:
        return tp.get_provider(mode(s))
    except tp.TerminalError as e:
        raise ValidationError(str(e)) from None


def start_payment(s, actor: Actor, appt_id: int, *, amount: int, materials: list | None = None,
                  client_name: str | None = None) -> dict:
    a = _appointment(s, actor, appt_id)
    if a.status not in ApptStatus.CLOSABLE:
        raise Conflict("Замовлення вже закрито або запис скасовано", code="already_closed")
    if not isinstance(amount, int) or amount < MIN_AMOUNT:
        raise ValidationError("Оплата карткою — від 1,00 грн")
    if amount > 10_000_000:
        raise ValidationError("Завелика сума для оплати карткою")
    md = mode(s)
    existing = s.scalar(select(CardPayment).where(CardPayment.appointment_id == appt_id,
                                                  CardPayment.status.in_(("pending", "paid"))))
    if existing is not None:
        if existing.status == "paid":
            raise Conflict("Цей запис уже оплачено карткою — перевірте оплату", code="already_paid")
        if existing.amount == amount and existing.mode == md:
            return public(existing, tp.deeplink(existing.jwt))  # повторне натискання — та сама операція
        raise Conflict("Є незавершена оплата на іншу суму — спершу перевірте або скасуйте її", code="pending_payment")
    prov = _provider(s)
    try:
        r = prov.create_pay(amount, purpose=f"MDS запис {a.id}")
    except tp.TerminalError as e:
        log.warning("terminal create failed: %s", e)
        raise Conflict(str(e)) from None
    p = CardPayment(appointment_id=a.id, amount=amount, status="pending", mode=md, jwt=r["jwt"], rid=r.get("rid"),
                    close_params={"materials": materials or [], "client_name": client_name},
                    created_by=actor.user_id, created_at=clock.now(), updated_at=clock.now())
    s.add(p)
    s.flush()
    audit(s, actor, "card_payment", p.id, "start", None, {"appointment_id": a.id, "amount": amount, "mode": md})
    return public(p, tp.deeplink(p.jwt))


def _get(s, actor: Actor, payment_id: int) -> CardPayment:
    p = s.get(CardPayment, payment_id)
    if p is None:
        raise NotFound("Оплату не знайдено")
    _appointment(s, actor, p.appointment_id)  # перевірка прав
    return p


def check_payment(s, actor: Actor, payment_id: int) -> dict:
    p = _get(s, actor, payment_id)
    if p.status in ("paid", "failed"):
        return public(p)
    prov = tp.get_provider(p.mode)
    try:
        r = prov.check(p.jwt)
    except tp.TerminalUnavailable as e:
        raise Conflict(f"Не вдалося перевірити оплату: {e}. Спробуйте ще раз.") from None
    except tp.TerminalError as e:
        raise Conflict(str(e)) from None
    pay = r.get("pay")
    p.updated_at = clock.now()
    if not pay or not pay.get("response_code"):
        p.message = "Оплату ще не завершено на терміналі"
        return public(p, tp.deeplink(p.jwt))
    p.transaction_id = pay.get("transaction_id")
    p.rrn, p.approval_code = pay.get("rrn") or None, pay.get("approval_code") or None
    p.masked_pan, p.payment_system = pay.get("masked_pan"), pay.get("payment_system")
    p.response_code, p.message = str(pay.get("response_code")), (pay.get("user_message") or pay.get("code") or "")[:255]
    p.receipt = pay.get("receipt")
    if p.response_code != "00":
        p.status = "failed"
        audit(s, actor, "card_payment", p.id, "failed", None, {"response_code": p.response_code, "message": p.message})
        return public(p)
    p.status = "paid"
    cp = p.close_params or {}
    o = orders.close_order(s, actor, p.appointment_id, amount=p.amount, payment_method="card",
                           materials=cp.get("materials") or None, idempotency_key=f"card-{p.id}",
                           client_name=cp.get("client_name"))
    p.order_id = o.id
    audit(s, actor, "card_payment", p.id, "paid", None,
          {"order_id": o.id, "rrn": p.rrn, "approval_code": p.approval_code, "amount": p.amount})
    return public(p)


def cancel_payment(s, actor: Actor, payment_id: int) -> dict:
    """Скасувати незавершену оплату (клієнт передумав). Спершу перевіряємо в банку, що гроші НЕ списано."""
    p = _get(s, actor, payment_id)
    if p.status != "pending":
        return public(p)
    res = check_payment(s, actor, payment_id)
    if res["status"] != "pending":
        return res
    p.status, p.message, p.updated_at = "failed", "Скасовано в MDS до оплати", clock.now()
    audit(s, actor, "card_payment", p.id, "cancel", None, None)
    return public(p)


def list_for_appointment(s, actor: Actor, appt_id: int) -> list[dict]:
    _appointment(s, actor, appt_id)
    return [public(p) for p in s.scalars(select(CardPayment).where(CardPayment.appointment_id == appt_id)
                                         .order_by(CardPayment.id))]


def status(s, actor: Actor) -> dict:
    return {"mode": mode(s), "live_configured": tp.configured(), "terminal_package": tp.TERMINAL_PACKAGE}
