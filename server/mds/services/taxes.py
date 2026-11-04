"""Податки ФОП і офіційна (юридична) зарплата працівників.

ВАЖЛИВО:
- Ставки, мінімальна зарплата, бази й строки сплати НЕ зашиті в код: це записи tax_rates з датами дії,
  які вводить директор/бухгалтер. Нова ставка з датою автоматично завершує попередню (історія не змінюється).
- Офіційна зарплата — окремий юридичний розрахунок. Управлінський поділ 50/50 (orders/finance.py) НЕ змінюється
  і не підміняє нарахування зарплати.
- Нарахування лише додаються: виправлення — сторно (від'ємний рядок) + нове нарахування.
- Гроші — копійки, ставки — bp, округлення до копійки «половина вгору».

Ключі ставок (key): min_wage (amount — мінімальна зарплата), single_tax, military_fop, esv_fop (податки ФОП),
pdfo, military_payroll, esv_employer (зарплата). База (base): min_wage | income | fixed | salary | salary_min_wage.

Кадрові дані працівника (EmployeeProfile): РНОКПП, посада, прийом/звільнення, основне місце роботи. Мінімальна база
ЄСВ (не менше мінімальної зарплати) застосовується лише за основним місцем роботи і за повний місяць роботи —
інакше база = фактичне нарахування (уточнюйте з бухгалтером особливі випадки: лікарняні, відпустки без збереження).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, NotFound, ValidationError
from ..models import (
    EmployeeProfile, Order, PayrollAccrual, PayrollPayment, Role, TaxAccrual, TaxPayment, TaxRate, User,
)
from . import notifications
from .common import Actor, audit

FOP_KEYS = ("single_tax", "military_fop", "esv_fop")
PAYROLL_KEYS = ("pdfo", "military_payroll", "esv_employer")
ALL_KEYS = ("min_wage",) + FOP_KEYS + PAYROLL_KEYS
KEY_UA = {"min_wage": "Мінімальна зарплата", "single_tax": "Єдиний податок", "military_fop": "Військовий збір (ФОП)",
          "esv_fop": "ЄСВ (ФОП)", "pdfo": "ПДФО", "military_payroll": "Військовий збір (зарплата)",
          "esv_employer": "ЄСВ роботодавця"}
BASES = ("min_wage", "income", "fixed", "salary", "salary_min_wage")


def pct(amount: int, bp: int) -> int:
    """amount × bp / 10000 з округленням до копійки (половина вгору; для від'ємних — симетрично)."""
    sign = -1 if amount < 0 else 1
    return sign * ((abs(amount) * bp + 5000) // 10000)


def month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def period_bounds(d: date, period: str) -> tuple[date, date]:
    if period == "quarter":
        qm = (d.month - 1) // 3 * 3 + 1
        a = date(d.year, qm, 1)
        b = _add_months(a, 2)
    else:
        a = month_start(d)
        b = a
    return a, b.replace(day=calendar.monthrange(b.year, b.month)[1])


# =================================================================== ставки
def rate_public(r: TaxRate) -> dict:
    return {"id": r.id, "key": r.key, "title": KEY_UA.get(r.key, r.key), "rate_bp": r.rate_bp, "amount": r.amount,
            "base": r.base, "period": r.period, "due_day": r.due_day, "due_from": r.due_from,
            "due_offset_months": r.due_offset_months, "valid_from": r.valid_from.isoformat(),
            "valid_to": r.valid_to.isoformat() if r.valid_to else None, "note": r.note}


def rate_for(s: Session, key: str, on: date) -> TaxRate | None:
    return s.scalar(select(TaxRate).where(TaxRate.key == key, TaxRate.valid_from <= on,
                                          (TaxRate.valid_to.is_(None)) | (TaxRate.valid_to >= on))
                    .order_by(TaxRate.valid_from.desc()).limit(1))


def add_rate(s: Session, actor: Actor, *, key: str, valid_from: date, rate_bp: int | None = None, amount: int | None = None,
             base: str = "fixed", period: str = "month", due_day: int | None = None, due_from: str = "end",
             due_offset_months: int = 0, valid_to: date | None = None, note: str | None = None) -> TaxRate:
    """Нова ставка з датою дії. Попередня відкрита ставка цього ключа завершується днем раніше."""
    actor.require(Role.DIRECTOR)
    if key not in ALL_KEYS:
        raise ValidationError("Невідомий вид ставки")
    if base not in BASES or period not in ("month", "quarter") or due_from not in ("start", "end"):
        raise ValidationError("Невірні параметри ставки")
    if key == "min_wage":
        if not amount or amount <= 0:
            raise ValidationError("Вкажіть суму мінімальної зарплати")
    elif rate_bp is None and amount is None:
        raise ValidationError("Вкажіть ставку (%) або суму")
    if rate_bp is not None and not (0 <= rate_bp <= 10000):
        raise ValidationError("Ставка — від 0 до 100%")
    if due_day is not None and not (1 <= due_day <= 31):
        raise ValidationError("День сплати — від 1 до 31")
    if valid_to and valid_to < valid_from:
        raise ValidationError("Дата завершення раніша за початок")
    later = s.scalar(select(TaxRate).where(TaxRate.key == key, TaxRate.valid_from >= valid_from))
    if later:
        raise Conflict("Вже є ставка з такою або пізнішою датою початку — виправте її або вкажіть іншу дату")
    prev = s.scalar(select(TaxRate).where(TaxRate.key == key, TaxRate.valid_from < valid_from,
                                          (TaxRate.valid_to.is_(None)) | (TaxRate.valid_to >= valid_from)))
    if prev:
        before = rate_public(prev)
        prev.valid_to = valid_from - timedelta(days=1)
        audit(s, actor, "tax_rate", prev.id, "close", before, rate_public(prev), "Нова ставка з " + valid_from.isoformat())
    r = TaxRate(key=key, rate_bp=rate_bp, amount=amount, base=base, period=period, due_day=due_day, due_from=due_from,
                due_offset_months=due_offset_months, valid_from=valid_from, valid_to=valid_to, note=note,
                created_by=actor.user_id)
    s.add(r)
    s.flush()
    audit(s, actor, "tax_rate", r.id, "create", None, rate_public(r))
    return r


def list_rates(s: Session, actor: Actor) -> list[dict]:
    actor.require(Role.DIRECTOR)
    return [rate_public(r) for r in s.scalars(select(TaxRate).order_by(TaxRate.key, TaxRate.valid_from.desc()))]


def _min_wage(s: Session, on: date) -> int:
    r = rate_for(s, "min_wage", on)
    if not r:
        raise ValidationError(f"Не задано мінімальну зарплату на {on:%d.%m.%Y}")
    return r.amount


# =================================================================== податки ФОП
def _income(s: Session, a: date, b: date) -> int:
    """Дохід ФОП за період — сума закритих замовлень (поточні редакції, після повернень)."""
    return int(s.scalar(select(func.coalesce(func.sum(Order.amount), 0)).where(
        Order.closed_at >= datetime.combine(a, time.min), Order.closed_at < datetime.combine(b + timedelta(days=1), time.min))) or 0)


def _calc_fop(s: Session, key: str, on: date) -> dict:
    r = rate_for(s, key, on)
    if not r:
        raise ValidationError(f"Не задано ставку «{KEY_UA[key]}» на {on:%d.%m.%Y}")
    a, b = period_bounds(on, r.period)
    if r.base == "min_wage":
        base = _min_wage(s, a)
        months = 3 if r.period == "quarter" else 1
        amount = pct(base, r.rate_bp or 0) * months
    elif r.base == "income":
        base = _income(s, a, b)
        amount = pct(base, r.rate_bp or 0)
    else:
        base = r.amount or 0
        amount = base if r.rate_bp is None else pct(base, r.rate_bp)
    ref = a if r.due_from == "start" else b
    due_month = _add_months(ref.replace(day=1), r.due_offset_months)
    due = due_month.replace(day=min(r.due_day or calendar.monthrange(due_month.year, due_month.month)[1],
                                    calendar.monthrange(due_month.year, due_month.month)[1]))
    return {"rate": r, "start": a, "end": b, "base": base, "amount": amount, "due": due}


def accrue_fop(s: Session, actor: Actor, key: str, on: date) -> TaxAccrual:
    """Нарахувати податок ФОП за період, що містить дату on (один раз)."""
    actor.require(Role.DIRECTOR)
    if key not in FOP_KEYS:
        raise ValidationError("Невідомий податок ФОП")
    c = _calc_fop(s, key, on)
    dk = f"fop:{key}:{c['start']:%Y-%m-%d}"
    if s.scalar(select(TaxAccrual.id).where(TaxAccrual.dedupe_key == dk)):
        raise Conflict(f"«{KEY_UA[key]}» за цей період уже нараховано — виправте нарахування")
    t = TaxAccrual(key=key, period_start=c["start"], period_end=c["end"], base_amount=c["base"], rate_bp=c["rate"].rate_bp,
                   amount=c["amount"], due_date=c["due"], dedupe_key=dk, rate_id=c["rate"].id, created_by=actor.user_id)
    s.add(t)
    try:
        s.flush()
    except IntegrityError:
        raise Conflict("Вже нараховано") from None
    audit(s, actor, "tax_accrual", t.id, "create", None, accrual_public(t))
    return t


def correct_fop(s: Session, actor: Actor, accrual_id: int, reason: str, amount: int | None = None) -> TaxAccrual:
    """Виправлення: сторно старого + нове (перерахунок за поточними ставками або вказана сума)."""
    actor.require(Role.DIRECTOR)
    if not (reason or "").strip():
        raise ValidationError("Вкажіть причину виправлення")
    old = s.get(TaxAccrual, accrual_id)
    if old is None or old.amount < 0:
        raise NotFound("Нарахування не знайдено")
    if old.reversed:
        raise Conflict("Нарахування вже виправлено")
    c = _calc_fop(s, old.key, old.period_start)
    dk = old.dedupe_key
    old.reversed, old.dedupe_key = True, None
    s.add(TaxAccrual(key=old.key, period_start=old.period_start, period_end=old.period_end, base_amount=-old.base_amount,
                     rate_bp=old.rate_bp, amount=-old.amount, due_date=old.due_date, reverses_id=old.id,
                     rate_id=old.rate_id, note=f"Сторно: {reason}", created_by=actor.user_id))
    s.flush()
    new = TaxAccrual(key=old.key, period_start=old.period_start, period_end=old.period_end, base_amount=c["base"],
                     rate_bp=c["rate"].rate_bp, amount=c["amount"] if amount is None else amount, due_date=c["due"],
                     dedupe_key=dk, rate_id=c["rate"].id, note=reason, created_by=actor.user_id)
    s.add(new)
    s.flush()
    audit(s, actor, "tax_accrual", new.id, "correct", accrual_public(old), accrual_public(new), reason)
    return new


def pay_tax(s: Session, actor: Actor, *, key: str, period_start: date, amount: int, paid_on: date, note: str | None = None) -> TaxPayment:
    actor.require(Role.DIRECTOR)
    if key not in FOP_KEYS or amount <= 0:
        raise ValidationError("Невірний платіж")
    p = TaxPayment(key=key, period_start=period_start, amount=amount, paid_on=paid_on, note=note, created_by=actor.user_id)
    s.add(p)
    s.flush()
    audit(s, actor, "tax_payment", p.id, "create", None, {"key": key, "period_start": period_start, "amount": amount})
    return p


def accrual_public(t: TaxAccrual) -> dict:
    return {"id": t.id, "key": t.key, "title": KEY_UA.get(t.key, t.key), "period_start": t.period_start.isoformat(),
            "period_end": t.period_end.isoformat(), "base_amount": t.base_amount, "rate_bp": t.rate_bp, "amount": t.amount,
            "due_date": t.due_date.isoformat(), "reversed": t.reversed, "reverses_id": t.reverses_id, "note": t.note}


def fop_summary(s: Session, actor: Actor, year: int) -> list[dict]:
    """По кожному податку і періоду: нараховано (з урахуванням сторно), сплачено, борг, строк, прострочено."""
    actor.require(Role.DIRECTOR)
    a, b = date(year, 1, 1), date(year, 12, 31)
    rows: dict[tuple, dict] = {}
    for t in s.scalars(select(TaxAccrual).where(TaxAccrual.period_start >= a, TaxAccrual.period_start <= b)):
        r = rows.setdefault((t.key, t.period_start), {"key": t.key, "title": KEY_UA[t.key], "period_start": t.period_start.isoformat(),
                                                      "period_end": t.period_end.isoformat(), "accrued": 0, "paid": 0,
                                                      "due_date": t.due_date.isoformat()})
        r["accrued"] += t.amount
        if not t.reversed and t.amount > 0:
            r["due_date"] = t.due_date.isoformat()
            r["accrual_id"] = t.id
    for p in s.scalars(select(TaxPayment).where(TaxPayment.period_start >= a, TaxPayment.period_start <= b)):
        r = rows.setdefault((p.key, p.period_start), {"key": p.key, "title": KEY_UA[p.key], "period_start": p.period_start.isoformat(),
                                                      "period_end": None, "accrued": 0, "paid": 0, "due_date": None})
        r["paid"] += p.amount
    today = clock.now().date()
    out = []
    for r in sorted(rows.values(), key=lambda x: (x["period_start"], x["key"])):
        r["debt"] = r["accrued"] - r["paid"]
        r["overdue"] = bool(r["debt"] > 0 and r["due_date"] and date.fromisoformat(r["due_date"]) < today)
        out.append(r)
    return out


def tax_reminders(s: Session, days_before: int = 5) -> int:
    """Нагадати директору про строки сплати (один раз на нарахування)."""
    today = clock.now().date()
    n = 0
    for t in s.scalars(select(TaxAccrual).where(TaxAccrual.reversed.is_(False), TaxAccrual.amount > 0,
                                                TaxAccrual.reminded_at.is_(None),
                                                TaxAccrual.due_date <= today + timedelta(days=days_before))):
        paid = int(s.scalar(select(func.coalesce(func.sum(TaxPayment.amount), 0)).where(
            TaxPayment.key == t.key, TaxPayment.period_start == t.period_start)) or 0)
        if paid >= t.amount:
            t.reminded_at = clock.now()
            continue
        left = t.amount - paid
        notifications.notify_directors(s, f"🗓 Податки: «{KEY_UA[t.key]}» за період з {t.period_start:%d.%m.%Y} — "
                                          f"до сплати {left / 100:.2f} ₴, строк {t.due_date:%d.%m.%Y}.")
        t.reminded_at = clock.now()
        n += 1
    return n


# =================================================================== офіційна зарплата
def _payroll_rates(s: Session, on: date) -> dict:
    out = {}
    for k in PAYROLL_KEYS:
        r = rate_for(s, k, on)
        if not r or r.rate_bp is None:
            raise ValidationError(f"Не задано ставку «{KEY_UA[k]}» на {on:%d.%m.%Y}")
        out[k] = r
    return out


def _full_month(p: EmployeeProfile | None, m: date) -> bool:
    if p is None:
        return True
    end = m.replace(day=calendar.monthrange(m.year, m.month)[1])
    return (p.hired_on is None or p.hired_on <= m) and (p.fired_on is None or p.fired_on >= end)


def calc_payroll(s: Session, gross: int, month: date, user_id: int | None = None) -> dict:
    """Розрахунок: ПДФО і військовий збір утримуються із зарплати; ЄСВ — нарахування роботодавця зверху.
    Мінімальна база ЄСВ — лише за основним місцем роботи і повний місяць (див. EmployeeProfile)."""
    if gross < 0:
        raise ValidationError("Сума не може бути від'ємною")
    m = month_start(month)
    r = _payroll_rates(s, m)
    pdfo = pct(gross, r["pdfo"].rate_bp)
    military = pct(gross, r["military_payroll"].rate_bp)
    prof = s.scalar(select(EmployeeProfile).where(EmployeeProfile.user_id == user_id)) if user_id else None
    min_rule = r["esv_employer"].base == "salary_min_wage" and gross > 0 and (prof is None or prof.main_job) \
        and _full_month(prof, m)
    esv_base = max(gross, _min_wage(s, m)) if min_rule else gross
    esv = pct(esv_base, r["esv_employer"].rate_bp)
    return {"gross": gross, "pdfo": pdfo, "military": military, "net": gross - pdfo - military, "esv_base": esv_base,
            "esv_employer": esv, "min_base_applied": min_rule and esv_base > gross,
            "rates": {k: v.rate_bp for k, v in r.items()} | {"esv_base_rule": r["esv_employer"].base}}


def payroll_public(p: PayrollAccrual) -> dict:
    return {"id": p.id, "user_id": p.user_id, "user_name": p.user.full_name, "month": p.month.isoformat(), "gross": p.gross,
            "pdfo": p.pdfo, "military": p.military, "net": p.net, "esv_employer": p.esv_employer, "esv_base": p.esv_base,
            "rates": p.rates, "reversed": p.reversed, "reverses_id": p.reverses_id, "note": p.note}


def accrue_salary(s: Session, actor: Actor, *, user_id: int, month: date, gross: int, note: str | None = None) -> PayrollAccrual:
    actor.require(Role.DIRECTOR)
    u = s.get(User, user_id)
    if u is None or u.role == Role.DIRECTOR:
        raise ValidationError("Оберіть працівника")
    m = month_start(month)
    prof = s.scalar(select(EmployeeProfile).where(EmployeeProfile.user_id == user_id))
    if prof is None or not prof.tin or prof.hired_on is None:
        raise ValidationError("Спершу заповніть кадрові дані працівника: РНОКПП і дату прийому")
    end = m.replace(day=calendar.monthrange(m.year, m.month)[1])
    if prof.hired_on > end or (prof.fired_on and prof.fired_on < m):
        raise ValidationError("У цьому місяці працівник не був прийнятий на роботу")
    dk = f"payroll:{user_id}:{m:%Y-%m}"
    if s.scalar(select(PayrollAccrual.id).where(PayrollAccrual.dedupe_key == dk)):
        raise Conflict("Зарплату за цей місяць уже нараховано — виправте нарахування")
    c = calc_payroll(s, gross, m, user_id)
    p = PayrollAccrual(user_id=user_id, month=m, gross=gross, pdfo=c["pdfo"], military=c["military"], net=c["net"],
                       esv_employer=c["esv_employer"], esv_base=c["esv_base"], rates=c["rates"], dedupe_key=dk, note=note,
                       created_by=actor.user_id)
    s.add(p)
    try:
        s.flush()
    except IntegrityError:
        raise Conflict("Вже нараховано") from None
    audit(s, actor, "payroll", p.id, "create", None, payroll_public(p))
    return p


def correct_salary(s: Session, actor: Actor, accrual_id: int, *, gross: int, reason: str) -> PayrollAccrual:
    actor.require(Role.DIRECTOR)
    if not (reason or "").strip():
        raise ValidationError("Вкажіть причину виправлення")
    old = s.get(PayrollAccrual, accrual_id)
    if old is None or old.gross < 0:
        raise NotFound("Нарахування не знайдено")
    if old.reversed:
        raise Conflict("Нарахування вже виправлено")
    dk = old.dedupe_key
    old.reversed, old.dedupe_key = True, None
    s.add(PayrollAccrual(user_id=old.user_id, month=old.month, gross=-old.gross, pdfo=-old.pdfo, military=-old.military,
                         net=-old.net, esv_employer=-old.esv_employer, esv_base=-old.esv_base, rates=old.rates,
                         reverses_id=old.id, note=f"Сторно: {reason}", created_by=actor.user_id))
    s.flush()
    c = calc_payroll(s, gross, old.month, old.user_id)
    new = PayrollAccrual(user_id=old.user_id, month=old.month, gross=gross, pdfo=c["pdfo"], military=c["military"],
                         net=c["net"], esv_employer=c["esv_employer"], esv_base=c["esv_base"], rates=c["rates"],
                         dedupe_key=dk, note=reason, created_by=actor.user_id)
    s.add(new)
    s.flush()
    audit(s, actor, "payroll", new.id, "correct", payroll_public(old), payroll_public(new), reason)
    return new


def salary_debt(s: Session, user_id: int, month: date) -> int:
    m = month_start(month)
    net = int(s.scalar(select(func.coalesce(func.sum(PayrollAccrual.net), 0)).where(
        PayrollAccrual.user_id == user_id, PayrollAccrual.month == m)) or 0)
    paid = int(s.scalar(select(func.coalesce(func.sum(PayrollPayment.amount), 0)).where(
        PayrollPayment.user_id == user_id, PayrollPayment.month == m)) or 0)
    return net - paid


def pay_salary(s: Session, actor: Actor, *, user_id: int, month: date, amount: int, paid_on: date, note: str | None = None) -> PayrollPayment:
    """Виплата офіційної зарплати (часткова або повна). Більше за борг — заборонено."""
    actor.require(Role.DIRECTOR)
    if amount <= 0:
        raise ValidationError("Сума виплати має бути більшою за 0")
    debt = salary_debt(s, user_id, month)
    if amount > debt:
        raise Conflict(f"Виплата більша за заборгованість ({debt / 100:.2f} ₴)")
    p = PayrollPayment(user_id=user_id, month=month_start(month), amount=amount, paid_on=paid_on, note=note,
                       created_by=actor.user_id)
    s.add(p)
    s.flush()
    audit(s, actor, "payroll_payment", p.id, "create", None, {"user_id": user_id, "month": month_start(month), "amount": amount})
    return p


def payroll_summary(s: Session, actor: Actor, month: date) -> dict:
    actor.require(Role.DIRECTOR)
    m = month_start(month)
    rows: dict[int, dict] = {}
    for p in s.scalars(select(PayrollAccrual).where(PayrollAccrual.month == m)):
        r = rows.setdefault(p.user_id, {"user_id": p.user_id, "user_name": p.user.full_name, "gross": 0, "pdfo": 0,
                                        "military": 0, "net": 0, "esv_employer": 0, "paid": 0})
        for k in ("gross", "pdfo", "military", "net", "esv_employer"):
            r[k] += getattr(p, k)
        if not p.reversed and p.gross >= 0:
            r["accrual_id"] = p.id
    for p in s.scalars(select(PayrollPayment).where(PayrollPayment.month == m)):
        r = rows.setdefault(p.user_id, {"user_id": p.user_id, "user_name": s.get(User, p.user_id).full_name, "gross": 0,
                                        "pdfo": 0, "military": 0, "net": 0, "esv_employer": 0, "paid": 0})
        r["paid"] += p.amount
    out = []
    for r in rows.values():
        r["debt"] = r["net"] - r["paid"]
        out.append(r)
    tot = {k: sum(r[k] for r in out) for k in ("gross", "pdfo", "military", "net", "esv_employer", "paid", "debt")}
    # до бюджету: ПДФО + військовий збір + ЄСВ роботодавця
    tot["to_budget"] = tot["pdfo"] + tot["military"] + tot["esv_employer"]
    tot["employer_cost"] = tot["gross"] + tot["esv_employer"]
    return {"month": m.isoformat(), "rows": sorted(out, key=lambda x: x["user_name"]), "totals": tot}


def payroll_history(s: Session, actor: Actor, user_id: int) -> list[dict]:
    actor.require(Role.DIRECTOR)
    return [payroll_public(p) for p in s.scalars(select(PayrollAccrual).where(PayrollAccrual.user_id == user_id)
                                                  .order_by(PayrollAccrual.month.desc(), PayrollAccrual.id))]


# =================================================================== кадрові дані і звіти по найманих
def employee_public(u: User, p: EmployeeProfile | None) -> dict:
    return {"user_id": u.id, "full_name": u.full_name, "role": u.role, "is_active": u.is_active,
            "tin": p.tin if p else None, "position": p.position if p else None,
            "hired_on": p.hired_on.isoformat() if p and p.hired_on else None,
            "fired_on": p.fired_on.isoformat() if p and p.fired_on else None,
            "main_job": p.main_job if p else True, "note": p.note if p else None, "is_employee": bool(p and p.hired_on)}


def list_employees(s: Session, actor: Actor) -> list[dict]:
    actor.require(Role.DIRECTOR)
    profs = {p.user_id: p for p in s.scalars(select(EmployeeProfile))}
    return [employee_public(u, profs.get(u.id)) for u in s.scalars(select(User).where(User.role != Role.DIRECTOR)
                                                                     .order_by(User.full_name))]


def set_employee(s: Session, actor: Actor, user_id: int, data: dict) -> dict:
    """Кадрові дані (директор). РНОКПП — 10 цифр; звільнення не раніше прийому."""
    import re

    actor.require(Role.DIRECTOR)
    u = s.get(User, user_id)
    if u is None or u.role == Role.DIRECTOR:
        raise NotFound("Працівника не знайдено")
    p = s.scalar(select(EmployeeProfile).where(EmployeeProfile.user_id == user_id))
    before = employee_public(u, p)
    if p is None:
        p = EmployeeProfile(user_id=user_id)
        s.add(p)
    tin = (data.get("tin") or "").strip() or None
    if tin and not re.fullmatch(r"[0-9]{10}", tin):
        raise ValidationError("РНОКПП — 10 цифр")
    hired, fired = data.get("hired_on"), data.get("fired_on")
    if hired and fired and fired < hired:
        raise ValidationError("Дата звільнення раніша за дату прийому")
    p.tin, p.position = tin, ((data.get("position") or "").strip() or None)
    p.hired_on, p.fired_on = hired, fired
    p.main_job = bool(data.get("main_job", True))
    p.note = (data.get("note") or "").strip() or None
    p.updated_at = clock.now()
    s.flush()
    audit(s, actor, "employee", user_id, "update", before, employee_public(u, p))
    return employee_public(u, p)


def payroll_quarter(s: Session, actor: Actor, year: int, quarter: int) -> dict:
    """Квартальна відомість для Податкового розрахунку: по працівнику і місяцю — нараховано, ПДФО, ВЗ,
    база і сума ЄСВ (за місяцем нарахування) і виплачено (за датою виплати)."""
    actor.require(Role.DIRECTOR)
    if quarter not in (1, 2, 3, 4):
        raise ValidationError("Квартал — від 1 до 4")
    months = [date(year, 3 * (quarter - 1) + i, 1) for i in (1, 2, 3)]
    a, b = months[0], months[2].replace(day=calendar.monthrange(year, months[2].month)[1])
    profs = {p.user_id: p for p in s.scalars(select(EmployeeProfile))}
    rows: dict[tuple, dict] = {}

    def row(uid: int, m: date) -> dict:
        u = s.get(User, uid)
        p = profs.get(uid)
        return rows.setdefault((uid, m), {"user_id": uid, "full_name": u.full_name, "tin": p.tin if p else None,
                                          "month": m.isoformat(), "gross": 0, "pdfo": 0, "military": 0, "net": 0,
                                          "esv_base": 0, "esv_employer": 0, "paid": 0})
    for x in s.scalars(select(PayrollAccrual).where(PayrollAccrual.month >= a, PayrollAccrual.month <= b)):
        r = row(x.user_id, x.month)
        for k in ("gross", "pdfo", "military", "net", "esv_base", "esv_employer"):
            r[k] += getattr(x, k)
    for x in s.scalars(select(PayrollPayment).where(PayrollPayment.paid_on >= a, PayrollPayment.paid_on <= b)):
        row(x.user_id, month_start(x.paid_on))["paid"] += x.amount
    out = sorted(rows.values(), key=lambda r: (r["full_name"], r["month"]))
    keys = ("gross", "pdfo", "military", "net", "esv_base", "esv_employer", "paid")
    tot = {k: sum(r[k] for r in out) for k in keys}
    tot["to_budget"] = tot["pdfo"] + tot["military"] + tot["esv_employer"]
    return {"year": year, "quarter": quarter, "rows": out, "totals": tot,
            "employees": len({r["user_id"] for r in out})}


def payroll_quarter_csv(s: Session, actor: Actor, year: int, quarter: int) -> tuple[str, str]:
    import csv
    import io

    d = payroll_quarter(s, actor, year, quarter)
    uah = lambda k: f"{'-' if k < 0 else ''}{abs(k) // 100},{abs(k) % 100:02d}"  # noqa: E731
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Працівник", "РНОКПП", "Місяць", "Нараховано, грн", "ПДФО, грн", "Військовий збір, грн", "На руки, грн",
                "База ЄСВ, грн", "ЄСВ роботодавця, грн", "Виплачено, грн"])
    for r in d["rows"]:
        w.writerow([r["full_name"], r["tin"] or "", r["month"][:7], uah(r["gross"]), uah(r["pdfo"]), uah(r["military"]),
                    uah(r["net"]), uah(r["esv_base"]), uah(r["esv_employer"]), uah(r["paid"])])
    t = d["totals"]
    w.writerow([])
    w.writerow(["Разом", "", "", uah(t["gross"]), uah(t["pdfo"]), uah(t["military"]), uah(t["net"]), uah(t["esv_base"]),
                uah(t["esv_employer"]), uah(t["paid"])])
    w.writerow(["До бюджету (ПДФО + ВЗ + ЄСВ)", "", "", uah(t["to_budget"])])
    return "\ufeff" + buf.getvalue(), f"zarplata-{year}-Q{quarter}.csv"


# =================================================================== календар податків і звітності
def _workday(d: date) -> date:
    """Строк, що припадає на вихідний, переноситься на наступний робочий день (свята не враховано)."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def tax_calendar(s: Session, actor: Actor, year: int) -> list[dict]:
    """Строки сплати і звітності ФОП на рік (за групою з налаштувань) + звіти по найманих, якщо є.
    Суми — за ставками з датами («Ставки»), статус — за нарахуваннями/сплатами («Податки ФОП»).
    Загальні правила ПКУ; особливі випадки (воєнний стан, пільги) — уточнюйте з бухгалтером."""
    from .common import get_setting

    actor.require(Role.DIRECTOR)
    group = int(get_setting(s, "fop_group") or 1)
    today = clock.now().date()
    rows = {(r["key"], r["period_start"]): r for r in fop_summary(s, actor, year)}
    ev: list[dict] = []

    def add(kind: str, title: str, due: date, period_from: date, period_to: date, key: str | None = None, note: str = ""):
        amount, status = None, "planned"
        if key:
            r = rows.get((key, period_from.isoformat()))
            if r:
                amount = r["accrued"]
                status = "paid" if r["debt"] <= 0 else ("overdue" if due < today else "due")
            else:
                try:
                    amount = _calc_fop(s, key, period_from)["amount"]
                except ValidationError:
                    amount = None
                status = "check" if due < today else "planned"
        elif due < today:
            status = "check"
        ev.append({"kind": kind, "key": key, "title": title, "due": due.isoformat(), "period_from": period_from.isoformat(),
                   "period_to": period_to.isoformat(), "amount": amount, "status": status, "note": note})

    def due_day(key: str, default: int = 20) -> int:
        r = rate_for(s, key, date(year, 1, 1))
        return (r.due_day if r and r.due_day else default)

    if group in (1, 2):
        for m in range(1, 13):
            a = date(year, m, 1)
            b = a.replace(day=calendar.monthrange(year, m)[1])
            add("pay", "Єдиний податок (аванс за місяць)", _workday(a.replace(day=min(due_day("single_tax"), b.day))), a, b, "single_tax")
            add("pay", "Військовий збір ФОП (за місяць)", _workday(a.replace(day=min(due_day("military_fop"), b.day))), a, b, "military_fop")
        add("report", f"Річна декларація платника єдиного податку за {year} (з Додатком 1 — ЄСВ)",
            _workday(date(year + 1, 1, 1) + timedelta(days=59)), date(year, 1, 1), date(year, 12, 31),
            note="Подає бухгалтер: дані — «Для бухгалтера»")
    else:
        for q in range(1, 5):
            a, b = date(year, 3 * q - 2, 1), date(year, 3 * q, calendar.monthrange(year, 3 * q)[1])
            add("report", f"Декларація платника єдиного податку (3 група) за {q} квартал", _workday(b + timedelta(days=40)), a, b,
                note="Єдиний податок і військовий збір — протягом 10 днів після граничного строку декларації")
    for q in range(1, 5):
        a, b = date(year, 3 * q - 2, 1), date(year, 3 * q, calendar.monthrange(year, 3 * q)[1])
        add("pay", f"ЄСВ ФОП за себе за {q} квартал", _workday(date(year + (q == 4), (3 * q) % 12 + 1, 20)), a, b, "esv_fop")
    hired = s.scalar(select(func.count(EmployeeProfile.id)).where(
        EmployeeProfile.hired_on.isnot(None), EmployeeProfile.hired_on <= date(year, 12, 31),
        (EmployeeProfile.fired_on.is_(None)) | (EmployeeProfile.fired_on >= date(year, 1, 1)))) or 0
    if hired:
        for q in range(1, 5):
            a, b = date(year, 3 * q - 2, 1), date(year, 3 * q, calendar.monthrange(year, 3 * q)[1])
            add("report", f"Податковий розрахунок по найманих (ПДФО, ВЗ, ЄСВ) за {q} квартал",
                _workday(b + timedelta(days=40)), a, b,
                note="ПДФО, ВЗ і ЄСВ із зарплати сплачуються в день виплати зарплати")
    return sorted(ev, key=lambda e: (e["due"], e["kind"]))
