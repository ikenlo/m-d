from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, update

from .. import clock
from ..errors import Conflict, DomainError, Forbidden, NotFound, ValidationError
from ..models import Appointment, ApptStatus, CardPayment
from . import card_terminal_providers as tp
from . import orders, terminal
from .common import Actor, audit, get_setting

log = logging.getLogger("mds.card_terminal")
MIN_AMOUNT = 100


AUTO_CLOSE_ENABLED = False
OPEN = ("pending", "processing", "unknown", "paid")
STALE = timedelta(seconds=terminal.PAY_TIMEOUT + terminal.CONNECT_TIMEOUT + 60)


def mode(s) -> str:
    from ..config import is_production

    pos = terminal.settings_public(s)
    if pos["enabled"] and pos["ip"]:
        return "ecr"
    md = get_setting(s, "card_terminal_mode") or "off"
    if md == "live" and not AUTO_CLOSE_ENABLED:
        return "off"
    if md == "mock" and is_production():
        return "off"
    return md


def public(p: CardPayment, deeplink: str | None = None) -> dict:
    return {"id": p.id, "appointment_id": p.appointment_id, "order_id": p.order_id, "amount": p.amount,
            "status": p.status, "mode": p.mode, "rrn": p.rrn, "approval_code": p.approval_code,
            "masked_pan": p.masked_pan, "payment_system": p.payment_system, "message": p.message,
            "created_at": p.created_at, "deeplink": deeplink}


def _appointment(s, actor: Actor, appt_id: int) -> Appointment:
    a = s.get(Appointment, appt_id)
    if a is None:
        raise NotFound(f"Запис #{appt_id} не знайдено")
    if not (actor.is_director or actor.user_id == a.master_id):
        raise Forbidden("Можна приймати оплату лише за власні записи")
    return a


def _provider(s):
    try:
        return tp.get_provider(mode(s))
    except tp.TerminalError as e:
        raise ValidationError(str(e)) from None


def _expire_stale(s, p: CardPayment) -> None:
    if p.mode == "ecr" and p.status == "processing" and p.updated_at < clock.now() - STALE:
        p.status, p.message, p.updated_at = "unknown", "Немає відповіді від термінала — перевірте чек на терміналі", clock.now()
        audit(s, Actor.system(), "card_payment", p.id, "unknown", None, {"reason": "stale"})


def start_payment(s, actor: Actor, appt_id: int, *, amount: int, materials: list | None = None,
                  client_name: str | None = None, products: list | None = None, price: int | None = None) -> dict:
    a = _appointment(s, actor, appt_id)
    if a.status not in ApptStatus.CLOSABLE:
        raise Conflict("Замовлення вже закрито або запис скасовано", code="already_closed")
    if not isinstance(amount, int) or amount < MIN_AMOUNT:
        raise ValidationError("Оплата карткою — від 1,00 грн")
    if amount > 10_000_000:
        raise ValidationError("Завелика сума для оплати карткою")
    md = mode(s)
    existing = s.scalar(select(CardPayment).where(CardPayment.appointment_id == appt_id, CardPayment.status.in_(OPEN)))
    if existing is not None:
        _expire_stale(s, existing)
        if existing.status == "paid":
            raise Conflict("Цей запис уже оплачено карткою — перевірте оплату", code="already_paid")
        if existing.status == "processing":
            raise Conflict("Оплата вже виконується на терміналі", code="payment_in_progress")
        if existing.status == "unknown":
            raise Conflict("Попередня оплата без відповіді термінала — перевірте чек і закрийте вручну або скасуйте її",
                           code="payment_unknown")
        if existing.amount == amount and existing.mode == md:
            return public(existing, tp.deeplink(existing.jwt) if existing.jwt else None)
        raise Conflict("Є незавершена оплата на іншу суму — спершу перевірте або скасуйте її", code="pending_payment")
    close_params = {"materials": materials or [], "client_name": client_name, "products": products or [], "price": price}
    if md == "ecr":
        p = CardPayment(appointment_id=a.id, amount=amount, status="pending", mode="ecr", close_params=close_params,
                        created_by=actor.user_id, created_at=clock.now(), updated_at=clock.now())
        s.add(p)
        s.flush()
        audit(s, actor, "card_payment", p.id, "start", None, {"appointment_id": a.id, "amount": amount, "mode": "ecr"})
        return public(p)
    prov = _provider(s)
    try:
        r = prov.create_pay(amount, purpose=f"MDS запис {a.id}")
    except tp.TerminalError as e:
        log.warning("terminal create failed: %s", e)
        raise Conflict(str(e)) from None
    p = CardPayment(appointment_id=a.id, amount=amount, status="pending", mode=md, jwt=r["jwt"], rid=r.get("rid"),
                    close_params=close_params,
                    created_by=actor.user_id, created_at=clock.now(), updated_at=clock.now())
    s.add(p)
    s.flush()
    audit(s, actor, "card_payment", p.id, "start", None, {"appointment_id": a.id, "amount": amount, "mode": md})
    return public(p, tp.deeplink(p.jwt))


def _get(s, actor: Actor, payment_id: int) -> CardPayment:
    p = s.get(CardPayment, payment_id)
    if p is None:
        raise NotFound("Оплату не знайдено")
    _appointment(s, actor, p.appointment_id)
    return p


def check_payment(s, actor: Actor, payment_id: int) -> dict:
    p = _get(s, actor, payment_id)
    if p.mode == "ecr":
        _expire_stale(s, p)
        return public(p)
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
                           client_name=cp.get("client_name"), price=cp.get("price"), products=cp.get("products") or None)
    p.order_id = o.id
    audit(s, actor, "card_payment", p.id, "paid", None,
          {"order_id": o.id, "rrn": p.rrn, "approval_code": p.approval_code, "amount": p.amount})
    return public(p)


def cancel_payment(s, actor: Actor, payment_id: int) -> dict:
    p = _get(s, actor, payment_id)
    if p.mode == "ecr":
        return _ecr_cancel(s, actor, p)
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


# POS-термінал (ECR по TCP). Оплата триває до хвилини, тому транзакцію БД на цей час не тримаємо:
# 1) pending → processing (умовний UPDATE), 2) обмін з терміналом без сесії, 3) результат і закриття замовлення.

def ecr_pay(db, actor: Actor, payment_id: int) -> dict:
    with db.session() as s:
        if mode(s) != "ecr":
            raise DomainError("POS-термінал вимкнено або не задано IP-адресу", code="terminal_disabled")
        p = _get(s, actor, payment_id)
        if p.mode != "ecr":
            raise Conflict("Ця оплата не для POS-термінала")
        _expire_stale(s, p)
        if p.status != "pending":
            if p.status == "processing":
                raise Conflict("Оплата вже виконується на терміналі", code="payment_in_progress")
            return public(p)
        a = s.get(Appointment, p.appointment_id)
        if a.status not in ApptStatus.CLOSABLE:
            raise Conflict("Замовлення вже закрито або запис скасовано", code="already_closed")
        client = terminal.PrivatBankTerminalClient.from_settings(s)
        res = s.execute(update(CardPayment).where(CardPayment.id == p.id, CardPayment.status == "pending")
                        .values(status="processing", message="Очікування оплати на терміналі", updated_at=clock.now()))
        if res.rowcount != 1:
            raise Conflict("Оплата вже виконується на терміналі", code="payment_in_progress")
        amount, ref = p.amount, f"MDS запис {p.appointment_id} / оплата {p.id}"

    r = client.process_payment(amount, ref)

    with db.session() as s:
        p = s.get(CardPayment, payment_id)
        p.updated_at = clock.now()
        p.response_code = r.get("response_code")
        if r["success"]:
            p.status, p.message = "paid", "Оплачено на терміналі"
            p.rrn, p.approval_code = r.get("rrn"), r.get("approval_code")
            p.masked_pan, p.payment_system = r.get("masked_pan"), r.get("payment_system")
            audit(s, actor, "card_payment", p.id, "paid", None,
                  {"rrn": p.rrn, "approval_code": p.approval_code, "amount": p.amount, "mode": "ecr"})
        elif r.get("uncertain"):
            p.status, p.message = "unknown", f"{r['error']} — перевірте чек на терміналі"
            audit(s, actor, "card_payment", p.id, "unknown", None, {"error": r["error"]})
        else:
            p.status, p.message = "failed", r["error"][:255]
            audit(s, actor, "card_payment", p.id, "failed", None, {"response_code": p.response_code, "error": r["error"]})
        out = public(p)
    if out["status"] != "paid":
        out["error"] = r["error"]
        return out

    # Гроші вже списано: оплату збережено окремо, тож навіть якщо закриття не вдасться, RRN не загубиться.
    try:
        with db.session() as s:
            p = s.get(CardPayment, payment_id)
            cp = p.close_params or {}
            o = orders.close_order(s, actor, p.appointment_id, amount=p.amount, payment_method="card",
                                   materials=cp.get("materials") or None, idempotency_key=f"card-{p.id}",
                                   client_name=cp.get("client_name"), price=cp.get("price"),
                                   products=cp.get("products") or None)
            p.order_id = o.id
            audit(s, actor, "card_payment", p.id, "order_closed", None, {"order_id": o.id, "rrn": p.rrn})
            return public(p)
    except DomainError as e:
        log.warning("POS: оплату %s проведено, але замовлення не закрито: %s", payment_id, e.message)
        with db.session() as s:
            p = s.get(CardPayment, payment_id)
            p.message = f"Оплачено (RRN {p.rrn}), але замовлення не закрито: {e.message}"[:255]
            out = public(p)
        out["error"] = out["message"]
        return out


def _ecr_cancel(s, actor: Actor, p: CardPayment) -> dict:
    _expire_stale(s, p)
    if p.status == "pending":
        p.status, p.message, p.updated_at = "failed", "Скасовано в MDS до оплати", clock.now()
        audit(s, actor, "card_payment", p.id, "cancel", None, None)
        return public(p)
    if p.status == "processing":
        sent = False
        try:
            sent = terminal.PrivatBankTerminalClient.from_settings(s).interrupt()
        except ValidationError:
            pass
        audit(s, actor, "card_payment", p.id, "cancel_request", None, {"sent": sent})
        out = public(p)
        out["message"] = ("Скасування надіслано на термінал. Якщо оплата все ж пройде — замовлення закриється само"
                          if sent else "Термінал недоступний: скасуйте операцію на самому терміналі")
        return out
    if p.status == "unknown":
        p.status, p.updated_at = "failed", clock.now()
        p.message = "Скасовано без підтвердження термінала — перевірте, чи не було списання"
        audit(s, actor, "card_payment", p.id, "cancel", None, {"was": "unknown"})
    return public(p)
