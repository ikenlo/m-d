"""Банк (ПриватБанк «Автоклієнт») — читання виписок і звірка оплат карткою. Нічого не змінює в обліку.

- Права: лише директор.
- Режими (`privat_mode`): off (за замовч.) | mock (імітація) | live (реальний банк — заблоковано без
  `MDS_BANK_LIVE_ALLOWED=yes` на сервері). Рахунок — `privat_account` (IBAN).
- Звірка за день: замовлення з оплатою «картка» ↔ операції еквайрингу (POS-термінал) за той самий день,
  далі — очікуване зарахування (сума «до зарахування») ↔ надходження у виписці рахунку на дату зарахування.
- У БД нічого не записується (крім стандартного аудиту зміни налаштувань). Ledger не змінюється.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ValidationError
from ..models import Order, PaymentMethod, Role
from . import bank_providers as bp
from .common import Actor, get_setting

log = logging.getLogger("mds.bank")

MAX_STATEMENT_DAYS = 31
MAX_ACQ_DAYS = 7
MATCH_WINDOW_MIN = 120  # операція на терміналі ± 2 год від закриття замовлення


def _mode(s: Session) -> str:
    return get_setting(s, "privat_mode") or "off"


def _account(s: Session) -> str:
    acc = (get_setting(s, "privat_account") or "").strip()
    if not acc:
        raise ValidationError("Не вказано рахунок банку (налаштування «Рахунок ПриватБанку»)")
    return acc


def _provider(s: Session) -> bp.BankProvider:
    try:
        return bp.get_provider(_mode(s))
    except bp.BankForbiddenOperation as e:
        from ..errors import Forbidden

        raise Forbidden(str(e)) from None
    except bp.BankError as e:
        raise ValidationError(str(e)) from None


def _call(fn, *args):
    """Помилки банку → зрозумілі доменні помилки (без токена)."""
    from ..errors import Conflict, Forbidden

    try:
        return fn(*args)
    except bp.BankAuthError as e:
        log.warning("bank auth error: %s", e)
        raise Forbidden(f"Банк відхилив доступ: {e}") from None
    except bp.BankForbiddenOperation as e:
        raise Forbidden(str(e)) from None
    except bp.BankUnavailable as e:
        log.warning("bank unavailable: %s", e)
        raise Conflict(f"Банк тимчасово недоступний: {e}") from None
    except bp.BankError as e:
        log.warning("bank rejected: %s", e)
        raise ValidationError(f"Банк відхилив запит: {e}") from None


def _range(start: date, end: date, max_days: int) -> None:
    if end < start:
        raise ValidationError("Кінцева дата раніше за початкову")
    if (end - start).days + 1 > max_days:
        raise ValidationError(f"Період — не більше {max_days} днів")


# ------------------------------------------------------------------ стан і читання
def status(s: Session, actor: Actor) -> dict:
    actor.require(Role.DIRECTOR)
    acc = (get_setting(s, "privat_account") or "").strip()
    return {
        "mode": _mode(s),
        "account": acc,
        "live_allowed": bp.live_allowed(),
        "token_configured": bp.token_configured(),  # лише так/ні, значення ніколи не повертається
        "read_only": True,
    }


def check_connection(s: Session, actor: Actor) -> dict:
    actor.require(Role.DIRECTOR)
    p = _provider(s)
    st = _call(p.ensure_working)
    return {"ok": True, "provider": p.name, "phase": st.get("phase"), "today": st.get("today"),
            "lastday": st.get("lastday"), "date_final_statement": st.get("date_final_statement")}


def balance(s: Session, actor: Actor, on: date) -> list[dict]:
    actor.require(Role.DIRECTOR)
    p = _provider(s)
    return [bp.as_dict(b) for b in _call(p.balances, _account(s), on, on)]


def statement(s: Session, actor: Actor, start: date, end: date) -> dict:
    actor.require(Role.DIRECTOR)
    _range(start, end, MAX_STATEMENT_DAYS)
    p = _provider(s)
    rows = _call(p.transactions, _account(s), start, end)
    booked = [t for t in rows if t.booked]
    return {
        "start": start, "end": end,
        "income": sum(t.amount for t in booked if t.direction == "in"),
        "outcome": sum(t.amount for t in booked if t.direction == "out"),
        "not_booked": sum(1 for t in rows if not t.booked),
        "items": [bp.as_dict(t) for t in rows],
    }


def acquiring(s: Session, actor: Actor, start: date, end: date) -> dict:
    actor.require(Role.DIRECTOR)
    _range(start, end, MAX_ACQ_DAYS)
    p = _provider(s)
    acc = _account(s)
    items: list[bp.AcqTxn] = []
    d = start
    while d <= end:  # обмеження банку: період ≤ 1 дня → запит на кожен день
        items.extend(_call(p.acquiring, acc, d))
        d += timedelta(days=1)
    items = bp._dedupe(items)
    return {"start": start, "end": end, **_acq_totals(items), "items": [bp.as_dict(a) for a in items]}


def _acq_totals(items: list[bp.AcqTxn]) -> dict:
    return {
        "sales": sum(a.amount for a in items if a.amount > 0),
        "refunds": sum(-a.amount for a in items if a.amount < 0),
        "fee": sum(a.fee + a.extra_fee for a in items),
        "net": sum(a.net for a in items),
        "count": len(items),
    }


# ------------------------------------------------------------------ звірка
def _parse_at(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


def reconcile_day(s: Session, actor: Actor, day: date, *, check_settlement: bool = True) -> dict:
    """Звірка карткових оплат за день. Лише читання; результат — звіт для директора."""
    actor.require(Role.DIRECTOR)
    p = _provider(s)
    acc = _account(s)
    acq = _call(p.acquiring, acc, day)

    lo, hi = datetime.combine(day, datetime.min.time()), datetime.combine(day + timedelta(days=1), datetime.min.time())
    orders = list(s.scalars(select(Order).where(Order.payment_method == PaymentMethod.CARD, Order.closed_at >= lo,
                                                Order.closed_at < hi).order_by(Order.closed_at)))

    # 1) зіставлення продажів: та сама сума, найближчий час у межах вікна; кожне замовлення — один раз
    free = {o.id: o for o in orders}
    matched, unmatched_acq = [], []
    sales = sorted((a for a in acq if a.amount > 0), key=lambda a: a.at or "")
    window = timedelta(minutes=MATCH_WINDOW_MIN)
    for a in sales:
        at = _parse_at(a.at)
        best, best_gap = None, None
        for o in free.values():
            if o.amount != a.amount:
                continue
            # у прикладі специфікації буває час 00:00:00 — тоді час не порівнюємо (лише сума і день)
            gap = abs(o.closed_at - at) if at and at.time() != datetime.min.time() else timedelta(0)
            if gap <= window and (best_gap is None or gap < best_gap):
                best, best_gap = o, gap
        if best is None:
            unmatched_acq.append(a)
            continue
        free.pop(best.id)
        matched.append({"order_id": best.id, "amount": best.amount, "closed_at": best.closed_at,
                        "bank_at": a.at, "rrn": a.rrn, "fee": a.fee + a.extra_fee, "net": a.net,
                        "gap_min": int(best_gap.total_seconds() // 60)})
    refunds = [a for a in acq if a.amount < 0]

    orders_total = sum(o.amount for o in orders)
    totals = _acq_totals(acq)
    problems = []
    for o in free.values():
        problems.append({"type": "no_bank_operation", "order_id": o.id, "amount": o.amount,
                         "text": f"Замовлення #{o.id} — картка {o.amount / 100:.2f} ₴, операції в терміналі не знайдено"})
    for a in unmatched_acq:
        problems.append({"type": "no_order", "rrn": a.rrn, "amount": a.amount, "at": a.at,
                         "text": f"Оплата в терміналі {a.amount / 100:.2f} ₴ ({a.at}) без замовлення з оплатою карткою"})
    for a in refunds:
        problems.append({"type": "refund", "rrn": a.rrn, "amount": a.amount, "at": a.at,
                         "text": f"Повернення/сторно в терміналі {a.amount / 100:.2f} ₴ ({a.at}) — перевірте вручну"})

    # 2) зарахування: сума «до зарахування» по датах TranRetPos ↔ надходження у виписці рахунку
    settlement = []
    if check_settlement and acq:
        by_date: dict[str, int] = {}
        for a in acq:
            if a.settle_date:
                by_date[a.settle_date] = by_date.get(a.settle_date, 0) + a.net
        for sd, expected in sorted(by_date.items()):
            sdate = date.fromisoformat(sd)
            credits = [t for t in _call(p.transactions, acc, sdate, sdate) if t.direction == "in" and t.booked]
            exact = [t for t in credits if t.amount == expected]
            settlement.append({
                "date": sd, "expected_net": expected, "found": bool(exact),
                "candidates": [{"key": t.key, "amount": t.amount, "purpose": t.purpose} for t in (exact or credits)[:10]],
            })
            if not exact:
                problems.append({"type": "settlement_not_found", "date": sd, "amount": expected,
                                 "text": f"Зарахування {expected / 100:.2f} ₴ на {sd} у виписці не знайдено одним платежем"})

    return {
        "day": day, "provider": p.name,
        "orders_card_total": orders_total, "orders_card_count": len(orders),
        "bank": totals,
        "difference": totals["sales"] - totals["refunds"] - orders_total,
        "matched": matched, "settlement": settlement, "problems": problems,
        "ok": not problems,
    }
