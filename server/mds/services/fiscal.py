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
from ..models import FiscalOperation, FiscalReceipt, FiscalReport, FiscalShift, Order, Role
from . import notifications
from .common import Actor, audit, get_setting
from .fiscal_providers import (
    MODES,
    Provider,
    ProviderLost,
    ProviderRejected,
    ProviderUnavailable,
    ReceiptResult,
    get_provider,
    production_allowed,
)

STALE_SECONDS = 120
MAX_BACKOFF = 15 * 60
ALERT_AFTER_ATTEMPTS = 5
OPEN = ("queued", "sending", "error", "pending")  # ще не фіскалізовано


def enabled(s: Session) -> bool:
    return bool(get_setting(s, "fiscal_enabled"))


def mode(s: Session) -> str:
    """mock — імітація; test — тестова каса Checkbox; production — бойовий (лише з окремим дозволом сервера)."""
    m = get_setting(s, "fiscal_mode") or "mock"
    return m if m in MODES else "mock"


def is_nonfiscal(m: str) -> bool:
    return m != "production"


def provider_for(s: Session) -> Provider:
    return get_provider(mode(s))


# ------------------------------------------------------------------- налаштування (окремо від загальних)
FISCAL_KEYS = ("fiscal_enabled", "fiscal_mode", "fiscal_auto_open_shift")


def config(s: Session, actor: Actor) -> dict:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    return {"enabled": enabled(s), "mode": mode(s), "auto_open_shift": bool(get_setting(s, "fiscal_auto_open_shift")),
            "production_allowed": production_allowed(), "nonfiscal": is_nonfiscal(mode(s))}


def update_config(s: Session, actor: Actor, data: dict) -> dict:
    """Налаштування ПРРО — лише директор. Бойовий режим — лише якщо власник сервера дозволив його окремо."""
    from .common import set_setting

    actor.require(Role.DIRECTOR)
    if "mode" in data:
        if data["mode"] not in MODES:
            raise ValidationError("Режим: mock (імітація), test (тестова каса) або production (бойовий)")
        if data["mode"] == "production" and not production_allowed():
            raise Forbidden("Бойовий режим заблоковано до окремого дозволу (MDS_FISCAL_PRODUCTION_ALLOWED на сервері)")
        if data["mode"] != mode(s):
            waiting = int(s.scalar(select(func.count(FiscalReceipt.id)).where(FiscalReceipt.status.in_(OPEN))) or 0)
            if waiting:
                raise Conflict(f"Є чеки в обробці ({waiting}) — перемкніть режим після їх завершення")
        set_setting(s, actor, "fiscal_mode", data["mode"])
    for k, key in (("enabled", "fiscal_enabled"), ("auto_open_shift", "fiscal_auto_open_shift")):
        if k in data:
            if not isinstance(data[k], bool):
                raise ValidationError(f"{k}: так або ні")
            set_setting(s, actor, key, data[k])
    return config(s, actor)


# ------------------------------------------------------------------- журнал операцій
def _secrets(provider: Provider | None) -> list[str]:
    import os

    vals = [v for k, v in os.environ.items() if k.startswith(("MDS_CHECKBOX", "MDS_FISCAL")) and len(v) >= 4]
    if provider is not None and hasattr(provider, "secrets"):
        vals += provider.secrets()
    return vals


def redact(text: str | None, provider: Provider | None = None) -> str | None:
    """Прибрати з тексту ключі/PIN/токени (журнали й повідомлення не містять секретів)."""
    if not text:
        return text
    for v in sorted(set(_secrets(provider)), key=len, reverse=True):
        text = text.replace(v, "***")
    return text[:500]


def log_op(db: Database, m: str, op: str, *, ok: bool, result: str | None = None, error: str | None = None,
           started: float | None = None, receipt_id: int | None = None, shift_id: int | None = None,
           user_id: int | None = None, provider: Provider | None = None) -> None:
    import time as _t

    ms = int((_t.monotonic() - started) * 1000) if started else 0
    with db.session() as s:
        s.add(FiscalOperation(mode=m, op=op, ok=ok, result=result, error=redact(error, provider), duration_ms=ms,
                              receipt_id=receipt_id, shift_id=shift_id, nonfiscal=is_nonfiscal(m), user_id=user_id))


def operations(s: Session, actor: Actor, limit: int = 100) -> list[dict]:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    return [{"id": o.id, "created_at": o.created_at.isoformat(), "mode": o.mode, "op": o.op, "ok": o.ok, "result": o.result,
             "error": o.error, "duration_ms": o.duration_ms, "receipt_id": o.receipt_id, "nonfiscal": o.nonfiscal}
            for o in s.scalars(select(FiscalOperation).order_by(FiscalOperation.id.desc()).limit(min(limit, 500)))]


def receipt_public(r: FiscalReceipt) -> dict:
    return {
        "id": r.id, "uuid": r.uuid, "kind": r.kind, "order_id": r.order_id, "master_id": r.master_id,
        "amount": r.amount, "payment_method": r.payment_method, "status": r.status, "attempts": r.attempts,
        "last_error": r.last_error, "fiscal_code": r.fiscal_code, "fiscal_date": r.fiscal_date, "tax_url": r.tax_url,
        "provider": r.provider, "related_id": r.related_id, "reason": r.reason, "nonfiscal": r.nonfiscal,
        "goods": r.goods,
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
                      goods=_goods(o, o.amount), status="queued", provider=mode(s), nonfiscal=is_nonfiscal(mode(s)),
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
        m = r.provider if r.provider in MODES else mode(s)
        auto_open = bool(get_setting(s, "fiscal_auto_open_shift"))
        job = {"uuid": r.uuid, "goods": r.goods, "pm": r.payment_method, "amount": r.amount, "ret": r.kind == "return",
               "rel": s.get(FiscalReceipt, r.related_id).provider_receipt_id if r.related_id else None,
               "check_first": r.attempts > 1, "order_ref": f"MDS-{r.order_id}" if r.order_id else f"MDS-TEST-{r.id}"}
    import time as _t

    started = _t.monotonic()
    try:
        provider = provider or get_provider(m)
    except ProviderRejected as e:  # напр. бойовий режим заблоковано
        provider = None
        outcome, result, error = "rejected", None, str(e)
    else:
        outcome, result, error = _send(db, provider, job, auto_open)
    log_op(db, m, "return" if job["ret"] else "sell", ok=outcome in ("done", "pending"), result=outcome, error=error,
           started=started, receipt_id=receipt_id, provider=provider)
    return _apply(db, receipt_id, outcome, result, redact(error, provider))


def _send(db: Database, provider: Provider, job: dict, auto_open: bool):
    try:
        result = provider.get_receipt(job["uuid"]) if job["check_first"] else None
        if result is None:
            result = provider.sell(job["uuid"], job["goods"], job["pm"], job["amount"], is_return=job["ret"],
                                   related_provider_id=job["rel"], order_ref=job["order_ref"])
        return ("done" if result.status == "done" else "pending" if result.status == "pending" else "rejected"), result, result.error
    except ProviderLost as e:
        return "error", None, f"{e} — наступна спроба спершу перевірить чек"
    except ProviderUnavailable as e:
        return "error", None, str(e)
    except ProviderRejected as e:
        if auto_open and "зміну не відкрито" in str(e).lower():
            try:
                _resync_closed_shift(db)
                open_shift(db, Actor.system(), provider=provider)
                return "error_now", None, "Фіскальну зміну відкрито автоматично — повтор"
            except (ProviderUnavailable, ProviderRejected, Conflict) as e2:
                return "error", None, f"Не вдалося відкрити зміну: {e2}"
        return "rejected", None, str(e)


def _resync_closed_shift(db: Database) -> None:
    """ПРРО повідомив «зміну не відкрито», а в MDS вона ще активна (автозакриття/закриття поза MDS) —
    позначаємо нашу зміну закритою на боці ПРРО, щоб можна було відкрити нову."""
    with db.session() as s:
        sh = current_shift(s)
        if sh is not None:
            sh.status, sh.active, sh.closed_at = "closed", None, clock.now()
            sh.last_error = "Зміну закрито на боці ПРРО (розсинхрон) — відкрито нову"
            audit(s, Actor.system(), "fiscal_shift", sh.id, "resync_closed", None, {"reason": sh.last_error})


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
            if r.order_id:
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
                          goods=_goods(o, amount), status="queued", provider=sell.provider, nonfiscal=sell.nonfiscal, reason=reason,
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
    import time as _t

    started = _t.monotonic()
    try:
        res = provider.open_shift(su)
    except (ProviderUnavailable, ProviderLost, ProviderRejected) as e:
        with db.session() as s:
            sh = s.get(FiscalShift, sid)
            sh.status, sh.active, sh.last_error = "error", None, redact(str(e), provider)
            m = mode(s)
        log_op(db, m, "open_shift", ok=False, error=str(e), started=started, shift_id=sid, user_id=actor.user_id, provider=provider)
        raise
    with db.session() as s:
        m = mode(s)
    log_op(db, m, "open_shift", ok=True, result="opened", started=started, shift_id=sid, user_id=actor.user_id)
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
    import time as _t

    started = _t.monotonic()
    try:
        res = provider.close_shift()
    except (ProviderUnavailable, ProviderLost, ProviderRejected) as e:
        with db.session() as s:
            sh = s.get(FiscalShift, sid)
            sh.status, sh.last_error = "opened", redact(str(e), provider)
            m = mode(s)
        log_op(db, m, "close_shift", ok=False, error=str(e), started=started, shift_id=sid, user_id=actor.user_id, provider=provider)
        raise
    with db.session() as s:
        m = mode(s)
    log_op(db, m, "close_shift", ok=True, result="closed", started=started, shift_id=sid, user_id=actor.user_id)
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
    import time as _t

    started = _t.monotonic()
    try:
        data = provider.x_report()
    except (ProviderUnavailable, ProviderLost, ProviderRejected) as e:
        with db.session() as s:
            m = mode(s)
        log_op(db, m, "x_report", ok=False, error=str(e), started=started, shift_id=sid, user_id=actor.user_id, provider=provider)
        raise
    with db.session() as s:
        log_op(db, mode(s), "x_report", ok=True, result="ok", started=started, shift_id=sid, user_id=actor.user_id)
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
        out[r.order_id] = {"receipt_id": r.id, "status": r.status, "fiscal_code": r.fiscal_code, "tax_url": r.tax_url,
                           "nonfiscal": r.nonfiscal}
    return out


def status_summary(s: Session, actor: Actor) -> dict:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    counts = dict(s.execute(select(FiscalReceipt.status, func.count(FiscalReceipt.id)).group_by(FiscalReceipt.status)).all())
    last = get_setting(s, "fiscal_selftest_last")
    return {"enabled": enabled(s), "mode": mode(s), "nonfiscal": is_nonfiscal(mode(s)), "production_allowed": production_allowed(),
            "provider": mode(s), "shift": shift_public(current_shift(s)), "selftest": last,
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


# =================================================================== тестова панель
def check_connection(db: Database, actor: Actor) -> dict:
    """Перевірка з'єднання з провайдером поточного режиму (без створення документів)."""
    import time as _t

    actor.require(Role.DIRECTOR, Role.ADMIN)
    with db.session() as s:
        m = mode(s)
    started = _t.monotonic()
    provider = None
    try:
        provider = get_provider(m)
        info = provider.ping()
        log_op(db, m, "ping", ok=True, result="ok", started=started, user_id=actor.user_id)
        return {"ok": True, "mode": m, "nonfiscal": is_nonfiscal(m), "shift": info.get("shift")}
    except (ProviderUnavailable, ProviderLost, ProviderRejected) as e:
        log_op(db, m, "ping", ok=False, error=str(e), started=started, user_id=actor.user_id, provider=provider)
        return {"ok": False, "mode": m, "nonfiscal": is_nonfiscal(m), "error": redact(str(e), provider)}


def test_receipt(db: Database, actor: Actor, amount: int, payment_method: str = "cash") -> dict:
    """Тестовий чек без замовлення — лише в режимах mock/test. Позначається як НЕФІСКАЛЬНИЙ."""
    actor.require(Role.DIRECTOR, Role.ADMIN)
    if amount is None or amount <= 0 or amount > 10_000_000:
        raise ValidationError("Сума тестового чека — від 0,01 до 100 000 ₴")
    if payment_method not in ("cash", "card"):
        raise ValidationError("Оплата: готівка або картка")
    with db.session() as s:
        m = mode(s)
        if not is_nonfiscal(m):
            raise Forbidden("У бойовому режимі тестові чеки заборонені")
        u = str(uuidlib.uuid4())
        r = FiscalReceipt(uuid=u, dedupe_key=f"test:{u}", kind="sell", order_id=None, master_id=None, amount=amount,
                          payment_method=payment_method, status="queued", provider=m, nonfiscal=True,
                          goods=[{"code": "TEST", "name": "ТЕСТОВИЙ ЧЕК — НЕФІСКАЛЬНИЙ", "price": amount, "quantity": 1000}],
                          reason="Тестовий чек (панель)", next_attempt_at=clock.now(), created_by=actor.user_id)
        s.add(r)
        s.flush()
        audit(s, actor, "fiscal_receipt", r.id, "test", None, {"amount": amount, "mode": m})
        rid = r.id
    # одразу, без фонового циклу (працює й коли автоматичну фіскалізацію вимкнено); повтор — якщо щойно відкрито зміну
    for _ in range(2):
        st = process_receipt(db, rid)
        with db.session() as s:
            r = s.get(FiscalReceipt, rid)
            if st != "error" or (r.next_attempt_at and r.next_attempt_at > clock.now()):
                break
    with db.session() as s:
        return receipt_public(s.get(FiscalReceipt, rid))


def selftest(db: Database, actor: Actor) -> dict:
    """Самоперевірка модуля на ОКРЕМІЙ тимчасовій базі з імітацією ПРРО — робочі дані не змінюються.
    Результат зберігається для панелі (fiscal_selftest_last)."""
    import tempfile
    import time as _t
    from pathlib import Path

    from ..models import User
    from .catalog import create_service
    from .common import set_setting
    from .fiscal_providers import FakePRROProvider
    from .orders import close_order
    from .users import create_user

    actor.require(Role.DIRECTOR, Role.ADMIN)
    results: list[dict] = []
    t0 = _t.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        tdb = Database(f"sqlite:///{Path(tmp) / 'selftest.db'}")
        tdb.create_all()
        fp = FakePRROProvider()
        sys_ = Actor.system()
        with tdb.session() as s:
            d = create_user(s, sys_, username="st_dir", password="Selftest123", full_name="Тест Директор", role=Role.DIRECTOR)
            mst = create_user(s, sys_, username="st_master", password="Selftest123", full_name="Тест Майстер", role=Role.MASTER)
            da = Actor.of(d)
            svc = create_service(s, da, {"name": "Тестова послуга", "duration_min": 60, "price": 50000})
            set_setting(s, da, "fiscal_enabled", True)
            ids = {"d": d.id, "m": mst.id, "svc": svc.id}
        with tdb.session() as s:
            da, ma = Actor.of(s.get(User, ids["d"])), Actor.of(s.get(User, ids["m"]))

        def check(name, fn):
            try:
                detail = fn()
                results.append({"name": name, "ok": True, "detail": detail or ""})
            except Exception as e:  # noqa: BLE001 — самоперевірка фіксує будь-який збій
                results.append({"name": name, "ok": False, "detail": f"{type(e).__name__}: {e}"[:300]})

        def make_order(amount, pm, hour, key=None):
            from datetime import datetime as dt

            from .bookings import create_appointment

            now = clock.now()
            start = dt(now.year, now.month, now.day, 0, 0) + timedelta(days=1, hours=hour)
            with tdb.session() as s:
                a = create_appointment(s, da, master_id=ids["m"], service_id=ids["svc"], starts_at=start,
                                       client_name="Тестовий клієнт", client_phone=f"0670000{hour:03d}")
                aid = a.id
            with tdb.session() as s:
                return close_order(s, ma, aid, amount=amount, payment_method=pm, idempotency_key=key).id

        def receipt_of(oid, kind="sell"):
            with tdb.session() as s:
                return s.scalar(select(FiscalReceipt).where(FiscalReceipt.order_id == oid, FiscalReceipt.kind == kind)
                                .order_by(FiscalReceipt.id.desc()))

        def t_open():
            open_shift(tdb, da, provider=fp)
            assert fp.state.shift["status"] == "OPENED"
        check("Відкриття касової зміни", t_open)
        orders_: dict = {}

        def t_sell():
            orders_["cash"] = make_order(60000, "cash", 1)
            orders_["card"] = make_order(45000, "card", 3)
            process_pending(tdb, fp)
            rc, rk = receipt_of(orders_["cash"]), receipt_of(orders_["card"])
            assert rc.status == "done" and rk.status == "done" and rc.fiscal_code and rc.tax_url
            assert rc.amount == 60000 and rk.payment_method == "card" and rc.goods[0]["name"] == "Тестова послуга"
            assert fp.last_payload["order_ref"] == f"MDS-{orders_['card']}"
            return f"чеки {rc.fiscal_code}, {rk.fiscal_code} (НЕФІСКАЛЬНІ)"
        check("Чеки за готівку і картку, сума й назва послуги", t_sell)

        def t_reject():
            o = make_order(1000, "cash", 5)
            fp.fail_next = ["reject"]
            process_pending(tdb, fp)
            assert receipt_of(o).status == "rejected"
            assert retry(tdb, da, receipt_of(o).id, fp) == "done"
        check("Відхилення і повтор", t_reject)

        def t_unavailable():
            fp.down = True
            o = make_order(2000, "card", 7)
            process_pending(tdb, fp)
            r = receipt_of(o)
            assert r.status == "error"
            fp.down = False
            with tdb.session() as s:
                s.get(FiscalReceipt, r.id).next_attempt_at = clock.now()
            process_pending(tdb, fp)
            assert receipt_of(o).status == "done"
        check("Недоступність ПРРО і відновлення", t_unavailable)

        def t_lost():
            o = make_order(3000, "cash", 9)
            fp.fail_next = ["lost"]
            process_pending(tdb, fp)
            r = receipt_of(o)
            with tdb.session() as s:
                s.get(FiscalReceipt, r.id).next_attempt_at = clock.now()
            process_pending(tdb, fp)
            assert receipt_of(o).status == "done"
            assert sum(1 for c in fp.calls if c[0] == "sell" and c[1] == r.uuid) == 1
        check("Втрата відповіді — без дубліката", t_lost)

        def t_double():
            o = make_order(4000, "cash", 11, key="st-double")
            with tdb.session() as s:
                aid = s.get(Order, o).appointment_id
            with tdb.session() as s:
                assert close_order(s, ma, aid, amount=4000, payment_method="cash", idempotency_key="st-double").id == o
            process_pending(tdb, fp)
            with tdb.session() as s:
                n = s.scalar(select(func.count(FiscalReceipt.id)).where(FiscalReceipt.order_id == o))
            assert n == 1
        check("Подвійне закриття замовлення — один чек", t_double)

        def t_refund():
            res = refund(tdb, da, orders_["cash"], 10000, "самоперевірка", provider=fp)
            assert res["status"] == "done"
            with tdb.session() as s:
                assert s.get(Order, orders_["cash"]).amount == 50000
        check("Повернення (часткове)", t_refund)

        def t_reconcile():
            with tdb.session() as s:
                today = clock.now().date()
                rec = reconcile(s, da, today, today + timedelta(days=1), provider=fp)
            assert rec["ok"], rec["issues"]
            return f"облік {rec['totals']['orders_amount'] / 100:.2f} ₴ = чеки {rec['totals']['fiscal_net'] / 100:.2f} ₴"
        check("Звірка сум MDS і чеків", t_reconcile)

        def t_close():
            z = close_shift(tdb, da, provider=fp)
            assert z["status"] == "closed" and z["z_report"]["returns"] == 10000
        check("Закриття зміни (Z-звіт)", t_close)
    out = {"at": clock.now().isoformat(timespec="seconds"), "passed": sum(r["ok"] for r in results),
           "failed": sum(not r["ok"] for r in results), "duration_ms": int((_t.monotonic() - t0) * 1000),
           "results": results, "isolated": True}
    with db.session() as s:
        set_setting(s, actor, "fiscal_selftest_last", out)
        m = mode(s)
    log_op(db, m, "selftest", ok=out["failed"] == 0, result=f"{out['passed']}/{len(results)}", user_id=actor.user_id)
    return out
