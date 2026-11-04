"""Фіскалізація (ПРРО): чеки, касові зміни, X/Z-звіти, повернення, звірка.

Принципи (docs/FISCALIZATION.md):
1. Закриття замовлення не залежить від ПРРО. Чек ставиться в чергу (fiscal_receipts, status=queued)
   у ТІЙ САМІЙ транзакції, що й замовлення, і надсилається окремо (одразу після запису + фоновий цикл).
   ПРРО недоступний → замовлення збережено, чек у черзі, повтор з паузою.
2. Чек фіскалізовано лише після підтвердження провайдера (status=done, є фіскальний номер).
3. Дублікати неможливі: один чек продажу на замовлення (dedupe_key «sell:<order>», UNIQUE);
   провайдеру передається наш UUID чека; надсилає лише той, хто атомарно «захопив» чек (UPDATE … WHERE status IN …);
   якщо відповідь могла загубитися (повторна спроба) — спершу get_receipt(uuid), і лише якщо чека немає — новий запит.
4. Перезапуск сервера: «зависле» надсилання (sending довше STALE_SECONDS) повертається в чергу,
   а наступна спроба починається з перевірки в провайдера.
5. Фіскальний журнал (fiscal_receipts) окремий від фінансового (ledger), звірка — reconcile().
   Повернення: чек повернення + виправлення замовлення (сторно в ledger) в одній транзакції.
"""

from __future__ import annotations

import uuid as uuidlib
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..db import Database
from ..errors import Conflict, Forbidden, NotFound, ValidationError
from ..models import FiscalReceipt, FiscalReport, FiscalShift, Order, Role
from . import notifications
from .common import Actor, audit, get_setting
from .fiscal_providers import (
    Provider,
    ProviderLost,
    ProviderRejected,
    ProviderUnavailable,
    ReceiptResult,
    get_provider,
)

STALE_SECONDS = 120
MAX_BACKOFF = 15 * 60
ALERT_AFTER_ATTEMPTS = 5
OPEN = ("queued", "sending", "error", "pending")  # ще не фіскалізовано


def enabled(s: Session) -> bool:
    return bool(get_setting(s, "fiscal_enabled"))


def provider_for(s: Session) -> Provider:
    return get_provider(get_setting(s, "fiscal_provider") or "fake")


def receipt_public(r: FiscalReceipt) -> dict:
    return {
        "id": r.id, "uuid": r.uuid, "kind": r.kind, "order_id": r.order_id, "master_id": r.master_id,
        "amount": r.amount, "payment_method": r.payment_method, "status": r.status, "attempts": r.attempts,
        "last_error": r.last_error, "fiscal_code": r.fiscal_code, "fiscal_date": r.fiscal_date, "tax_url": r.tax_url,
        "provider": r.provider, "related_id": r.related_id, "reason": r.reason,
        "created_at": r.created_at.isoformat(), "done_at": r.done_at.isoformat() if r.done_at else None,
        "next_attempt_at": r.next_attempt_at.isoformat() if r.next_attempt_at else None,
    }


def _goods(o: Order, amount: int) -> list[dict]:
    return [{"code": f"S{o.service_id}", "name": o.service.name, "price": amount, "quantity": 1000}]


# =================================================================== черга
def enqueue_sell(s: Session, actor: Actor, o: Order) -> FiscalReceipt | None:
    """Поставити чек продажу в чергу (у транзакції закриття замовлення). Повторно — не створює."""
    if not enabled(s) or o.amount <= 0:
        return None
    key = f"sell:{o.id}"
    existing = s.scalar(select(FiscalReceipt).where(FiscalReceipt.dedupe_key == key))
    if existing:
        return existing
    r = FiscalReceipt(uuid=str(uuidlib.uuid4()), dedupe_key=key, kind="sell", order_id=o.id, master_id=o.master_id,
                      amount=o.amount, payment_method="cash" if o.payment_method == "cash" else "card",
                      goods=_goods(o, o.amount), status="queued", provider=get_setting(s, "fiscal_provider") or "fake",
                      next_attempt_at=clock.now(), created_by=actor.user_id)
    s.add(r)
    s.flush()
    audit(s, actor, "fiscal_receipt", r.id, "queue", None, {"order_id": o.id, "amount": o.amount, "kind": "sell"})
    return r


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(10 * 2 ** max(attempts - 1, 0), MAX_BACKOFF))


def recover_stale(db: Database) -> int:
    """Після перезапуску/збою: «зависле» надсилання → повторити (з перевіркою в провайдера)."""
    cut = clock.now() - timedelta(seconds=STALE_SECONDS)
    with db.session() as s:
        res = s.execute(update(FiscalReceipt).where(FiscalReceipt.status == "sending", FiscalReceipt.sending_since < cut)
                        .values(status="error", next_attempt_at=clock.now(), last_error="Перервано (перезапуск) — перевірка")
                        .execution_options(synchronize_session=False))
        return res.rowcount or 0


def process_receipt(db: Database, receipt_id: int, provider: Provider | None = None) -> str | None:
    """Одна спроба фіскалізації. Повертає новий статус або None (чек зайнятий/не час)."""
    now = clock.now()
    with db.session() as s:
        res = s.execute(
            update(FiscalReceipt)
            .where(FiscalReceipt.id == receipt_id, FiscalReceipt.status.in_(("queued", "error")),
                   or_(FiscalReceipt.next_attempt_at.is_(None), FiscalReceipt.next_attempt_at <= now))
            .values(status="sending", attempts=FiscalReceipt.attempts + 1, sending_since=now)
            .execution_options(synchronize_session=False))
        if res.rowcount != 1:
            return None
        r = s.get(FiscalReceipt, receipt_id)
        s.refresh(r)
        provider = provider or provider_for(s)
        auto_open = bool(get_setting(s, "fiscal_auto_open_shift"))
        job = {"uuid": r.uuid, "goods": r.goods, "pm": r.payment_method, "amount": r.amount, "ret": r.kind == "return",
               "rel": s.get(FiscalReceipt, r.related_id).provider_receipt_id if r.related_id else None,
               "check_first": r.attempts > 1}
    outcome, result, error = _send(db, provider, job, auto_open)
    return _apply(db, receipt_id, outcome, result, error)


def _send(db: Database, provider: Provider, job: dict, auto_open: bool):
    try:
        result = provider.get_receipt(job["uuid"]) if job["check_first"] else None
        if result is None:
            result = provider.sell(job["uuid"], job["goods"], job["pm"], job["amount"], is_return=job["ret"],
                                   related_provider_id=job["rel"])
        return ("done" if result.status == "done" else "pending" if result.status == "pending" else "rejected"), result, result.error
    except ProviderLost as e:
        return "error", None, f"{e} — наступна спроба спершу перевірить чек"
    except ProviderUnavailable as e:
        return "error", None, str(e)
    except ProviderRejected as e:
        if auto_open and "зміну не відкрито" in str(e).lower():
            try:
                open_shift(db, Actor.system(), provider=provider)
                return "error_now", None, "Фіскальну зміну відкрито автоматично — повтор"
            except (ProviderUnavailable, ProviderRejected, Conflict) as e2:
                return "error", None, f"Не вдалося відкрити зміну: {e2}"
        return "rejected", None, str(e)


def _apply(db: Database, receipt_id: int, outcome: str, result: ReceiptResult | None, error: str | None) -> str:
    now = clock.now()
    with db.session() as s:
        r = s.get(FiscalReceipt, receipt_id)
        r.sending_since = None
        if outcome == "done":
            r.status, r.done_at, r.last_error = "done", now, None
            r.provider_receipt_id = result.provider_id or r.uuid
            r.fiscal_code, r.fiscal_date, r.tax_url = result.fiscal_code, result.fiscal_date, result.tax_url
            sh = s.scalar(select(FiscalShift).where(FiscalShift.active.is_(True)))
            r.fiscal_shift_id = sh.id if sh else None
            audit(s, Actor.system(), "fiscal_receipt", r.id, "done", None,
                  {"fiscal_code": r.fiscal_code, "order_id": r.order_id, "amount": r.amount})
        elif outcome == "pending":
            r.status, r.provider_receipt_id = "pending", (result.provider_id if result else None) or r.uuid
            r.next_attempt_at = now + timedelta(seconds=30)
        elif outcome == "rejected":
            r.status, r.last_error = "rejected", error or "Відмова провайдера"
            notifications.notify_directors(s, f"🧾 ПРРО відхилив чек #{r.id} (замовлення #{r.order_id}, {r.amount / 100:.2f} ₴): "
                                              f"{r.last_error[:200]}. Перевірте розділ «Фіскалізація».")
            audit(s, Actor.system(), "fiscal_receipt", r.id, "rejected", None, {"error": r.last_error})
        else:  # error / error_now
            r.status, r.last_error = "error", error
            r.next_attempt_at = now if outcome == "error_now" else now + _backoff(r.attempts)
            if r.attempts == ALERT_AFTER_ATTEMPTS:
                notifications.notify_directors(s, f"🧾 ПРРО недоступний: чек #{r.id} не фіскалізовано після "
                                                  f"{r.attempts} спроб. Замовлення збережено, повтори тривають.")
        return r.status


def poll_pending(db: Database, provider: Provider | None = None) -> int:
    """Чеки, прийняті провайдером, але ще без фіскального номера → перевірити статус."""
    with db.session() as s:
        ids = list(s.scalars(select(FiscalReceipt.id).where(FiscalReceipt.status == "pending",
                                                            FiscalReceipt.next_attempt_at <= clock.now())))
        provider = provider or provider_for(s)
        uuids = {i: s.get(FiscalReceipt, i).uuid for i in ids}
    done = 0
    for rid, u in uuids.items():
        try:
            res = provider.get_receipt(u)
        except (ProviderUnavailable, ProviderLost):
            continue
        with db.session() as s:
            r = s.get(FiscalReceipt, rid)
            if r.status != "pending":
                continue
            if res and res.status == "done":
                r.status, r.done_at, r.fiscal_code, r.fiscal_date, r.tax_url = "done", clock.now(), res.fiscal_code, res.fiscal_date, res.tax_url
                done += 1
            elif res and res.status == "error":
                r.status, r.last_error = "rejected", res.error or "ДПС відхилила чек"
            else:
                r.next_attempt_at = clock.now() + timedelta(seconds=60)
    return done


def process_pending(db: Database, provider: Provider | None = None, limit: int = 100) -> dict:
    """Фоновий цикл: відновлення після збою, надсилання черги, перевірка відкладених."""
    with db.session() as s:
        if not enabled(s):
            return {"recovered": 0, "processed": 0}
    recovered = recover_stale(db)
    with db.session() as s:
        ids = list(s.scalars(select(FiscalReceipt.id).where(
            FiscalReceipt.status.in_(("queued", "error")),
            or_(FiscalReceipt.next_attempt_at.is_(None), FiscalReceipt.next_attempt_at <= clock.now()))
            .order_by(FiscalReceipt.id).limit(limit)))
    out = {}
    for rid in ids:
        st = process_receipt(db, rid, provider)
        if st:
            out[st] = out.get(st, 0) + 1
    polled = poll_pending(db, provider)
    return {"recovered": recovered, "processed": sum(out.values()), "by_status": out, "polled_done": polled}


def retry(db: Database, actor: Actor, receipt_id: int, provider: Provider | None = None) -> str | None:
    """Повторити зараз (відхилений або з помилкою). Майстер — лише свої чеки."""
    with db.session() as s:
        r = s.get(FiscalReceipt, receipt_id)
        if r is None:
            raise NotFound("Чек не знайдено")
        if actor.is_master:
            if r.master_id != actor.user_id:
                raise Forbidden("Можна повторити лише власний чек")
        else:
            actor.require(Role.DIRECTOR, Role.ADMIN)
        if r.status not in ("rejected", "error", "queued"):
            raise Conflict("Чек не потребує повтору")
        r.status, r.next_attempt_at = "error" if r.attempts else "queued", clock.now()
        audit(s, actor, "fiscal_receipt", r.id, "retry", None, None)
    return process_receipt(db, receipt_id, provider)


# =================================================================== повернення
def refund(db: Database, actor: Actor, order_id: int, amount: int, reason: str, *,
           provider: Provider | None = None, adjust_order: bool = True) -> dict:
    """Повернення коштів (повне або часткове) — директор.
    В одній транзакції: чек повернення в чергу + виправлення замовлення (сторно в ledger)."""
    actor.require(Role.DIRECTOR)
    if not (reason or "").strip():
        raise ValidationError("Вкажіть причину повернення")
    if amount is None or amount <= 0:
        raise ValidationError("Сума повернення має бути більшою за 0")
    from .orders import correct_order

    with db.session() as s:
        o = s.get(Order, order_id)
        if o is None:
            raise NotFound("Замовлення не знайдено")
        sell = s.scalar(select(FiscalReceipt).where(FiscalReceipt.dedupe_key == f"sell:{o.id}"))
        if sell is None or sell.status != "done":
            raise Conflict("Чек продажу ще не фіскалізовано — повернення неможливе")
        returned = int(s.scalar(select(func.coalesce(func.sum(FiscalReceipt.amount), 0)).where(
            FiscalReceipt.related_id == sell.id, FiscalReceipt.kind == "return",
            FiscalReceipt.status != "rejected")) or 0)
        if returned + amount > sell.amount:
            raise ValidationError(f"Можна повернути не більше {(sell.amount - returned) / 100:.2f} ₴")
        n = int(s.scalar(select(func.count(FiscalReceipt.id)).where(FiscalReceipt.related_id == sell.id)) or 0) + 1
        r = FiscalReceipt(uuid=str(uuidlib.uuid4()), dedupe_key=f"return:{o.id}:{n}", kind="return", order_id=o.id,
                          related_id=sell.id, master_id=o.master_id, amount=amount, payment_method=sell.payment_method,
                          goods=_goods(o, amount), status="queued", provider=sell.provider, reason=reason,
                          next_attempt_at=clock.now(), created_by=actor.user_id)
        s.add(r)
        try:
            s.flush()
        except IntegrityError:
            raise Conflict("Повернення вже оформлюється") from None
        if adjust_order:
            correct_order(s, actor, o.id, {"amount": o.amount - amount}, f"Повернення коштів: {reason}")
        audit(s, actor, "fiscal_receipt", r.id, "refund", None, {"order_id": o.id, "amount": amount}, reason)
        rid = r.id
    status = process_receipt(db, rid, provider)
    return {"receipt_id": rid, "status": status}


# =================================================================== зміни і звіти
def current_shift(s: Session) -> FiscalShift | None:
    return s.scalar(select(FiscalShift).where(FiscalShift.active.is_(True)))


def shift_public(sh: FiscalShift | None) -> dict | None:
    if sh is None:
        return None
    return {"id": sh.id, "status": sh.status, "provider": sh.provider, "opened_at": sh.opened_at.isoformat(),
            "closed_at": sh.closed_at.isoformat() if sh.closed_at else None, "z_report": sh.z_report,
            "last_error": sh.last_error}


def open_shift(db: Database, actor: Actor, *, provider: Provider | None = None) -> dict:
    """Відкрити фіскальну зміну. Директор/адміністратор (або система — автовідкриття)."""
    if actor.role != "system":
        actor.require(Role.DIRECTOR, Role.ADMIN)
    with db.session() as s:
        if current_shift(s):
            raise Conflict("Фіскальну зміну вже відкрито")
        provider = provider or provider_for(s)
        sh = FiscalShift(uuid=str(uuidlib.uuid4()), provider=provider.name, status="opening", active=True,
                         opened_at=clock.now(), opened_by=actor.user_id)
        s.add(sh)
        try:
            s.flush()
        except IntegrityError:
            raise Conflict("Фіскальну зміну вже відкрито") from None
        sid, su = sh.id, sh.uuid
    try:
        res = provider.open_shift(su)
    except (ProviderUnavailable, ProviderLost, ProviderRejected) as e:
        with db.session() as s:
            sh = s.get(FiscalShift, sid)
            sh.status, sh.active, sh.last_error = "error", None, str(e)
        raise
    with db.session() as s:
        sh = s.get(FiscalShift, sid)
        sh.status, sh.provider_shift_id = "opened", str(res.get("id") or su)
        audit(s, actor, "fiscal_shift", sid, "open", None, {"provider": provider.name})
        return shift_public(sh)


def close_shift(db: Database, actor: Actor, *, force: bool = False, provider: Provider | None = None) -> dict:
    """Закрити зміну (Z-звіт). Якщо є нефіскалізовані чеки — відмова (force — лише директор)."""
    actor.require(Role.DIRECTOR, Role.ADMIN)
    with db.session() as s:
        sh = current_shift(s)
        if sh is None:
            raise Conflict("Фіскальну зміну не відкрито")
        waiting = int(s.scalar(select(func.count(FiscalReceipt.id)).where(FiscalReceipt.status.in_(OPEN))) or 0)
        if waiting and not force:
            raise Conflict(f"Є нефіскалізовані чеки ({waiting}) — спершу дочекайтесь або повторіть їх")
        if force and not actor.is_director:
            raise Forbidden("Закрити зміну з нефіскалізованими чеками може лише директор")
        provider = provider or provider_for(s)
        sid = sh.id
        sh.status = "closing"
    try:
        res = provider.close_shift()
    except (ProviderUnavailable, ProviderLost, ProviderRejected) as e:
        with db.session() as s:
            sh = s.get(FiscalShift, sid)
            sh.status, sh.last_error = "opened", str(e)
        raise
    with db.session() as s:
        sh = s.get(FiscalShift, sid)
        z = res.get("z_report") or res
        sh.status, sh.active, sh.closed_at, sh.closed_by, sh.z_report = "closed", None, clock.now(), actor.user_id, z
        s.add(FiscalReport(kind="Z", fiscal_shift_id=sid, data=z, created_by=actor.user_id))
        audit(s, actor, "fiscal_shift", sid, "close", None, {"z_report": z}, "примусово" if force else None)
        return shift_public(sh)


def x_report(db: Database, actor: Actor, *, provider: Provider | None = None) -> dict:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    with db.session() as s:
        sh = current_shift(s)
        if sh is None:
            raise Conflict("Фіскальну зміну не відкрито")
        provider = provider or provider_for(s)
        sid = sh.id
    data = provider.x_report()
    with db.session() as s:
        s.add(FiscalReport(kind="X", fiscal_shift_id=sid, data=data, created_by=actor.user_id))
        audit(s, actor, "fiscal_shift", sid, "x_report", None, None)
    return data


# =================================================================== перегляд і звірка
def receipts_list(s: Session, actor: Actor, *, d_from: date | None = None, d_to: date | None = None,
                  status: str | None = None, order_id: int | None = None, limit: int = 300) -> list[dict]:
    q = select(FiscalReceipt)
    if actor.is_master:
        q = q.where(FiscalReceipt.master_id == actor.user_id)
    else:
        actor.require(Role.DIRECTOR, Role.ADMIN)
    if d_from:
        q = q.where(FiscalReceipt.created_at >= datetime.combine(d_from, time.min))
    if d_to:
        q = q.where(FiscalReceipt.created_at < datetime.combine(d_to + timedelta(days=1), time.min))
    if status:
        q = q.where(FiscalReceipt.status == status)
    if order_id:
        q = q.where(FiscalReceipt.order_id == order_id)
    return [receipt_public(r) for r in s.scalars(q.order_by(FiscalReceipt.id.desc()).limit(min(limit, 1000)))]


def order_statuses(s: Session, order_ids: list[int]) -> dict[int, dict]:
    """Стан фіскалізації для списку замовлень (для майстра/директора в інтерфейсі)."""
    if not order_ids:
        return {}
    out: dict[int, dict] = {}
    for r in s.scalars(select(FiscalReceipt).where(FiscalReceipt.order_id.in_(order_ids), FiscalReceipt.kind == "sell")):
        out[r.order_id] = {"receipt_id": r.id, "status": r.status, "fiscal_code": r.fiscal_code, "tax_url": r.tax_url}
    return out


def status_summary(s: Session, actor: Actor) -> dict:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    counts = dict(s.execute(select(FiscalReceipt.status, func.count(FiscalReceipt.id)).group_by(FiscalReceipt.status)).all())
    return {"enabled": enabled(s), "provider": get_setting(s, "fiscal_provider"), "shift": shift_public(current_shift(s)),
            "counts": {k: int(v) for k, v in counts.items()},
            "attention": int(counts.get("rejected", 0)) + int(counts.get("error", 0))}


def reconcile(s: Session, actor: Actor, d_from: date, d_to: date, *, provider: Provider | None = None) -> dict:
    """Звірка внутрішнього обліку (замовлення) з фіскальними чеками за період.

    Для кожного замовлення: сума замовлення (поточна редакція) = продаж − повернення (лише фіскалізовані).
    deep=provider: ще й перевірити в провайдера, що «done»-чеки там справді є.
    """
    actor.require(Role.DIRECTOR)
    a = datetime.combine(d_from, time.min)
    b = datetime.combine(d_to + timedelta(days=1), time.min)
    orders = list(s.scalars(select(Order).where(Order.closed_at >= a, Order.closed_at < b)))
    by_order: dict[int, list[FiscalReceipt]] = {}
    for r in s.scalars(select(FiscalReceipt).where(FiscalReceipt.order_id.in_([o.id for o in orders] or [0]))):
        by_order.setdefault(r.order_id, []).append(r)
    issues = []
    tot = {"orders": 0, "orders_amount": 0, "fiscal_sales": 0, "fiscal_returns": 0, "cash_internal": 0, "card_internal": 0,
           "cash_fiscal": 0, "card_fiscal": 0}
    for o in orders:
        rs = by_order.get(o.id, [])
        done_sell = sum(r.amount for r in rs if r.kind == "sell" and r.status == "done")
        done_ret = sum(r.amount for r in rs if r.kind == "return" and r.status == "done")
        open_ = [r for r in rs if r.status in OPEN or r.status == "rejected"]
        tot["orders"] += 1
        # сума замовлення до виправлень через повернення: поточна + повернене
        tot["orders_amount"] += o.amount
        pm_cash = o.payment_method == "cash"
        tot["cash_internal" if pm_cash else "card_internal"] += o.amount
        tot["fiscal_sales"] += done_sell
        tot["fiscal_returns"] += done_ret
        for r in rs:
            if r.status == "done":
                k = "cash_fiscal" if r.payment_method == "cash" else "card_fiscal"
                tot[k] += -r.amount if r.kind == "return" else r.amount
        if o.amount > 0 and not rs:
            issues.append({"order_id": o.id, "type": "missing", "text": "Немає фіскального чека", "amount": o.amount})
        elif open_:
            issues.append({"order_id": o.id, "type": "not_done", "text": f"Чек не фіскалізовано ({open_[0].status})",
                           "amount": o.amount, "receipt_id": open_[0].id})
        elif done_sell - done_ret != o.amount:
            issues.append({"order_id": o.id, "type": "mismatch",
                           "text": f"Сума замовлення {o.amount / 100:.2f} ₴ ≠ чеки {(done_sell - done_ret) / 100:.2f} ₴",
                           "amount": o.amount, "fiscal": done_sell - done_ret})
    provider_missing = []
    if provider is not None:
        for rs in by_order.values():
            for r in rs:
                if r.status == "done":
                    try:
                        got = provider.get_receipt(r.uuid)
                    except (ProviderUnavailable, ProviderLost):
                        provider_missing.append({"receipt_id": r.id, "text": "ПРРО недоступний для перевірки"})
                        continue
                    if got is None or got.status != "done":
                        provider_missing.append({"receipt_id": r.id, "text": "Чека немає в ПРРО або він не фіскалізований"})
    tot["fiscal_net"] = tot["fiscal_sales"] - tot["fiscal_returns"]
    return {"from": d_from.isoformat(), "to": d_to.isoformat(), "totals": tot, "issues": issues,
            "provider_issues": provider_missing, "ok": not issues and not provider_missing}
