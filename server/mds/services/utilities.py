"""Комунальні витрати та генератор (детально — docs/FINANCE.md, розділ 7).

Електроенергія: щомісячні показники лічильника → кВт·год × тариф = вартість. Тариф зберігається
в записі місяця. Підтвердження директором створює витрату «Комунальні» (одна на місяць).

Генератор: перемикач ON/OFF фіксує час роботи (мотогодини). Вартість години =
витрата (л/год) × ціна бензину + амортизація. Параметри — знімок на момент запуску:
зміна ціни бензину не перераховує минулі запуски. Директор може виправити запуск (з причиною).

Розподіл між майстрами (налаштування utilities_charge_mode):
- none — не списувати;
- month_end — після завершення місяця директор розподіляє (електроенергія + генератор) × частку
  між майстрами пропорційно виручці або порівну;
- fixed — фіксована сума автоматично при закритті зміни (за зміну або раз на місяць).
Подвійне списання неможливе: кожне списання має унікальний dedupe_key, а розподіл місяця
відмовляє, якщо в місяці вже є списання. Скасування — сторно (від'ємний рядок), історія лишається.

Гроші — копійки, кількості — тисячні (кВт·год, л, мотогодини × 1000). Без float.
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
    Expense,
    ExpenseCategory,
    GeneratorRun,
    MasterCharge,
    MeterReading,
    Order,
    Role,
    Shift,
    ShiftStatus,
    User,
)
from . import notifications
from .common import Actor, audit, get_setting, ledger
from .expenses import create_expense, update_expense

MONTHS = ["січень", "лютий", "березень", "квітень", "травень", "червень", "липень", "серпень",
          "вересень", "жовтень", "листопад", "грудень"]


def _r(a: int, b: int) -> int:
    """a × b / 1000 з округленням (для тисячних одиниць)."""
    return (a * b + 500) // 1000


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _month_bounds(m: date) -> tuple[datetime, datetime]:
    a = month_start(m)
    return datetime.combine(a, time.min), datetime.combine(month_end(a) + timedelta(days=1), time.min)


def _fmt(k: int) -> str:
    g, kop = divmod(abs(k), 100)
    return ("−" if k < 0 else "") + f"{g:,}".replace(",", " ") + (f",{kop:02d}" if kop else "") + " ₴"


def _milli(v, what: str) -> int:
    from .stock import to_milli

    try:
        return to_milli(v)
    except ValidationError:
        raise ValidationError(f"{what}: невірне число") from None


# =================================================================== електроенергія
def reading_public(r: MeterReading) -> dict:
    return {
        "id": r.id, "month": r.month.isoformat(), "read_on": r.read_on.isoformat(),
        "prev": r.prev_milli / 1000, "curr": r.curr_milli / 1000, "used": r.used_milli / 1000,
        "tariff": r.tariff, "cost": r.cost, "paid_amount": r.paid_amount,
        "paid_on": r.paid_on.isoformat() if r.paid_on else None,
        "difference": (r.paid_amount - r.cost) if r.paid_amount is not None else None,
        "confirmed": r.confirmed_at is not None, "expense_id": r.expense_id, "note": r.note,
    }


def _calc_reading(prev_milli: int, curr_milli: int, tariff: int) -> tuple[int, int]:
    if prev_milli < 0 or curr_milli < 0:
        raise ValidationError("Показники не можуть бути від'ємними")
    if curr_milli < prev_milli:
        raise ValidationError("Поточний показник менший за попередній")
    if tariff <= 0:
        raise ValidationError("Вкажіть ціну за 1 кВт·год")
    used = curr_milli - prev_milli
    return used, _r(used, tariff)


def last_reading(s: Session, before: date | None = None) -> MeterReading | None:
    q = select(MeterReading)
    if before:
        q = q.where(MeterReading.month < before)
    return s.scalar(q.order_by(MeterReading.month.desc()).limit(1))


def create_reading(s: Session, actor: Actor, *, month: date, read_on: date, curr, tariff: int, prev=None,
                   paid_amount: int | None = None, paid_on: date | None = None, note: str | None = None) -> MeterReading:
    """Внести показники за місяць. prev за замовчуванням — поточний показник попереднього місяця."""
    actor.require(Role.DIRECTOR)
    m = month_start(month)
    if s.scalar(select(MeterReading.id).where(MeterReading.month == m)):
        raise Conflict(f"Показники за {MONTHS[m.month - 1]} {m.year} вже внесено — відредагуйте їх")
    if prev is None:
        last = last_reading(s, m)
        prev_milli = last.curr_milli if last else None
        if prev_milli is None:
            raise ValidationError("Вкажіть попередній показник (перший запис)")
    else:
        prev_milli = _milli(prev, "Попередній показник")
    curr_milli = _milli(curr, "Поточний показник")
    used, cost = _calc_reading(prev_milli, curr_milli, int(tariff))
    if paid_amount is not None and paid_amount < 0:
        raise ValidationError("Сплачена сума не може бути від'ємною")
    r = MeterReading(month=m, read_on=read_on, prev_milli=prev_milli, curr_milli=curr_milli, used_milli=used,
                     tariff=int(tariff), cost=cost, paid_amount=paid_amount, paid_on=paid_on, note=note,
                     created_by=actor.user_id or 0)
    s.add(r)
    try:
        s.flush()
    except IntegrityError:
        raise Conflict("Показники за цей місяць уже внесено") from None
    audit(s, actor, "meter", r.id, "create", None, reading_public(r))
    return r


def update_reading(s: Session, actor: Actor, reading_id: int, data: dict, reason: str | None = None) -> MeterReading:
    """Виправити показники/тариф/оплату. Якщо місяць уже підтверджено — витрата виправляється
    сторно + новим записом (через expenses.update_expense), потрібна причина."""
    actor.require(Role.DIRECTOR)
    r = s.get(MeterReading, reading_id)
    if r is None:
        raise NotFound("Показники не знайдено")
    before = reading_public(r)
    prev = _milli(data["prev"], "Попередній показник") if data.get("prev") is not None else r.prev_milli
    curr = _milli(data["curr"], "Поточний показник") if data.get("curr") is not None else r.curr_milli
    tariff = int(data["tariff"]) if data.get("tariff") is not None else r.tariff
    used, cost = _calc_reading(prev, curr, tariff)
    if r.expense_id and cost != r.cost and not (reason or "").strip():
        raise ValidationError("Місяць уже підтверджено — вкажіть причину виправлення")
    r.prev_milli, r.curr_milli, r.used_milli, r.tariff, r.cost = prev, curr, used, tariff, cost
    for k in ("read_on", "paid_on", "note"):
        if k in data:
            setattr(r, k, data[k])
    if "paid_amount" in data:
        if data["paid_amount"] is not None and data["paid_amount"] < 0:
            raise ValidationError("Сплачена сума не може бути від'ємною")
        r.paid_amount = data["paid_amount"]
    if r.expense_id and before["cost"] != cost:
        update_expense(s, actor, r.expense_id, {"amount": cost, "description": _reading_desc(r)}, reason)
    s.flush()
    audit(s, actor, "meter", r.id, "update", before, reading_public(r), reason)
    return r


def _reading_desc(r: MeterReading) -> str:
    return (f"Електроенергія за {MONTHS[r.month.month - 1]} {r.month.year}: "
            f"{r.used_milli / 1000:g} кВт·год × {_fmt(r.tariff)}")


def confirm_reading(s: Session, actor: Actor, reading_id: int) -> MeterReading:
    """Підтвердити місяць: вартість стає витратою «Комунальні» (лише один раз)."""
    actor.require(Role.DIRECTOR)
    r = s.get(MeterReading, reading_id)
    if r is None:
        raise NotFound("Показники не знайдено")
    if r.expense_id:
        raise Conflict("Цей місяць уже підтверджено")
    e = create_expense(s, actor, category=ExpenseCategory.UTILITIES, amount=r.cost,
                       spent_on=month_end(r.month), description=_reading_desc(r))
    r.expense_id, r.confirmed_at, r.confirmed_by = e.id, clock.now(), actor.user_id
    s.flush()
    audit(s, actor, "meter", r.id, "confirm", None, {"expense_id": e.id, "cost": r.cost})
    return r


def list_readings(s: Session, actor: Actor) -> list[dict]:
    actor.require(Role.DIRECTOR)
    return [reading_public(r) for r in s.scalars(select(MeterReading).order_by(MeterReading.month.desc()))]


# =================================================================== генератор
def generator_settings(s: Session) -> dict:
    lph = int(get_setting(s, "generator_fuel_lph_milli") or 0)
    price = int(get_setting(s, "generator_fuel_price") or 0)
    amort = int(get_setting(s, "generator_amort_per_hour") or 0)
    return {"fuel_lph_milli": lph, "fuel_price": price, "amort_per_hour": amort,
            "hour_cost": _r(lph, price) + amort,
            "hours_initial_milli": int(get_setting(s, "generator_hours_initial_milli") or 0)}


def _costs(hours_milli: int, lph_milli: int, price: int, amort: int) -> dict:
    fuel = _r(hours_milli, lph_milli)
    fuel_cost = _r(fuel, price)
    amort_cost = _r(hours_milli, amort)
    return {"fuel_milli": fuel, "fuel_cost": fuel_cost, "amort_cost": amort_cost, "total_cost": fuel_cost + amort_cost}


def run_public(g: GeneratorRun, now: datetime | None = None) -> dict:
    live = g.ended_at is None
    hours = _elapsed_milli(g.started_at, now or clock.now()) if live else g.hours_milli
    c = _costs(hours, g.fuel_lph_milli, g.fuel_price, g.amort_per_hour) if live else {
        "fuel_milli": g.fuel_milli, "fuel_cost": g.fuel_cost, "amort_cost": g.amort_cost, "total_cost": g.total_cost}
    return {
        "id": g.id, "started_at": g.started_at.isoformat(), "ended_at": g.ended_at.isoformat() if g.ended_at else None,
        "running": live, "hours": hours / 1000, "fuel_l": c["fuel_milli"] / 1000, "fuel_cost": c["fuel_cost"],
        "amort_cost": c["amort_cost"], "total_cost": c["total_cost"],
        "fuel_lph": g.fuel_lph_milli / 1000, "fuel_price": g.fuel_price, "amort_per_hour": g.amort_per_hour,
        "hour_cost": _r(g.fuel_lph_milli, g.fuel_price) + g.amort_per_hour,
        "power_outage": g.power_outage, "corrected": g.corrected, "note": g.note,
    }


def _elapsed_milli(a: datetime, b: datetime) -> int:
    return max(int((b - a).total_seconds()), 0) * 1000 // 3600


def current_run(s: Session) -> GeneratorRun | None:
    return s.scalar(select(GeneratorRun).where(GeneratorRun.running.is_(True)))


def start_generator(s: Session, actor: Actor, *, power_outage: bool = True, note: str | None = None) -> GeneratorRun:
    """Генератор увімкнено. Дозволено персоналу (світло зникло — увімкнути може будь-хто на місці)."""
    actor.require(Role.DIRECTOR, Role.ADMIN, Role.MASTER)
    if current_run(s):
        raise Conflict("Генератор уже працює")
    st = generator_settings(s)
    g = GeneratorRun(started_at=clock.now(), running=True, fuel_lph_milli=st["fuel_lph_milli"],
                     fuel_price=st["fuel_price"], amort_per_hour=st["amort_per_hour"],
                     power_outage=bool(power_outage), note=note, started_by=actor.user_id)
    s.add(g)
    try:
        s.flush()
    except IntegrityError:
        raise Conflict("Генератор уже працює") from None
    audit(s, actor, "generator", g.id, "start", None, {"power_outage": g.power_outage, "hour_cost": st["hour_cost"]})
    return g


def stop_generator(s: Session, actor: Actor) -> GeneratorRun:
    actor.require(Role.DIRECTOR, Role.ADMIN, Role.MASTER)
    g = current_run(s)
    if g is None:
        raise Conflict("Генератор не працює")
    g.ended_at = clock.now()
    g.hours_milli = _elapsed_milli(g.started_at, g.ended_at)
    for k, v in _costs(g.hours_milli, g.fuel_lph_milli, g.fuel_price, g.amort_per_hour).items():
        setattr(g, k, v)
    g.running, g.ended_by = None, actor.user_id
    s.flush()
    audit(s, actor, "generator", g.id, "stop", None, run_public(g))
    return g


def add_run(s: Session, actor: Actor, *, started_at: datetime, hours, power_outage: bool = True,
            note: str | None = None) -> GeneratorRun:
    """Директор додає запуск вручну (забули увімкнути перемикач). Параметри — поточні налаштування."""
    actor.require(Role.DIRECTOR)
    hm = _milli(hours, "Мотогодини")
    if hm <= 0 or hm > 24 * 31 * 1000:
        raise ValidationError("Мотогодини: більше 0 і не більше місяця")
    st = generator_settings(s)
    g = GeneratorRun(started_at=started_at, ended_at=started_at + timedelta(seconds=hm * 3600 // 1000),
                     running=None, hours_milli=hm, fuel_lph_milli=st["fuel_lph_milli"], fuel_price=st["fuel_price"],
                     amort_per_hour=st["amort_per_hour"], power_outage=bool(power_outage), corrected=True,
                     note=note or "Додано вручну", started_by=actor.user_id, ended_by=actor.user_id)
    for k, v in _costs(hm, g.fuel_lph_milli, g.fuel_price, g.amort_per_hour).items():
        setattr(g, k, v)
    s.add(g)
    s.flush()
    audit(s, actor, "generator", g.id, "add", None, run_public(g), note)
    return g


def correct_run(s: Session, actor: Actor, run_id: int, data: dict, reason: str) -> GeneratorRun:
    """Ручне коригування запуску: мотогодини, витрата, ціна бензину, амортизація або загальна сума."""
    actor.require(Role.DIRECTOR)
    if not (reason or "").strip():
        raise ValidationError("Вкажіть причину коригування")
    g = s.get(GeneratorRun, run_id)
    if g is None:
        raise NotFound("Запуск генератора не знайдено")
    if g.ended_at is None:
        raise Conflict("Спершу вимкніть генератор")
    before = run_public(g)
    if data.get("hours") is not None:
        g.hours_milli = _milli(data["hours"], "Мотогодини")
        if g.hours_milli < 0:
            raise ValidationError("Мотогодини не можуть бути від'ємними")
    if data.get("fuel_lph") is not None:
        g.fuel_lph_milli = _milli(data["fuel_lph"], "Витрата бензину")
    for k in ("fuel_price", "amort_per_hour"):
        if data.get(k) is not None:
            if int(data[k]) < 0:
                raise ValidationError("Значення не може бути від'ємним")
            setattr(g, k, int(data[k]))
    for k, v in _costs(g.hours_milli, g.fuel_lph_milli, g.fuel_price, g.amort_per_hour).items():
        setattr(g, k, v)
    if data.get("fuel_cost") is not None or data.get("amort_cost") is not None:
        # ручна сума (напр. за чеком АЗС) — перекриває розрахунок
        if data.get("fuel_cost") is not None:
            g.fuel_cost = int(data["fuel_cost"])
        if data.get("amort_cost") is not None:
            g.amort_cost = int(data["amort_cost"])
        if g.fuel_cost < 0 or g.amort_cost < 0:
            raise ValidationError("Сума не може бути від'ємною")
        g.total_cost = g.fuel_cost + g.amort_cost
    if "power_outage" in data and data["power_outage"] is not None:
        g.power_outage = bool(data["power_outage"])
    g.corrected = True
    s.flush()
    audit(s, actor, "generator", g.id, "correct", before, run_public(g), reason)
    return g


def set_motohours(s: Session, actor: Actor, current) -> dict:
    """Звірка з лічильником генератора: встановити поточні мотогодини (зсув початкових)."""
    from .common import set_setting

    actor.require(Role.DIRECTOR)
    target = _milli(current, "Мотогодини")
    if target < 0:
        raise ValidationError("Мотогодини не можуть бути від'ємними")
    used = int(s.scalar(select(func.coalesce(func.sum(GeneratorRun.hours_milli), 0))
                        .where(GeneratorRun.ended_at.is_not(None))) or 0)
    set_setting(s, actor, "generator_hours_initial_milli", target - used)
    return generator_state(s, actor)


def generator_state(s: Session, actor: Actor) -> dict:
    actor.require(Role.DIRECTOR, Role.ADMIN, Role.MASTER)
    now = clock.now()
    st = generator_settings(s)
    run = current_run(s)
    used = int(s.scalar(select(func.coalesce(func.sum(GeneratorRun.hours_milli), 0))
                        .where(GeneratorRun.ended_at.is_not(None))) or 0)
    live = _elapsed_milli(run.started_at, now) if run else 0
    out = {"settings": st, "running": run_public(run, now) if run else None,
           "motohours": (st["hours_initial_milli"] + used + live) / 1000,
           "configured": st["hour_cost"] > 0}
    if actor.role == Role.DIRECTOR:
        out["forecast"] = generator_forecast(s, actor, now.date())
    return out


def generator_forecast(s: Session, actor: Actor, today: date) -> dict:
    """Прогноз на місяць: середні мотогодини за минулі дні × днів у місяці × поточна вартість години."""
    actor.require(Role.DIRECTOR)
    st = generator_settings(s)
    a, b = _month_bounds(today)
    hours = int(s.scalar(select(func.coalesce(func.sum(GeneratorRun.hours_milli), 0)).where(
        GeneratorRun.started_at >= a, GeneratorRun.started_at < b, GeneratorRun.ended_at.is_not(None))) or 0)
    run = current_run(s)
    if run and run.started_at >= a:
        hours += _elapsed_milli(run.started_at, clock.now())
    days_total = (month_end(today) - month_start(today)).days + 1
    days = max(today.day, 1)
    proj_hours = hours * days_total // days
    return {"hour_cost": st["hour_cost"], "hours_so_far": hours / 1000, "projected_hours": proj_hours / 1000,
            "projected_cost": _r(proj_hours, st["hour_cost"]), "days_elapsed": days, "days_total": days_total}


def list_runs(s: Session, actor: Actor, month: date) -> list[dict]:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    a, b = _month_bounds(month)
    now = clock.now()
    return [run_public(g, now) for g in s.scalars(select(GeneratorRun).where(
        GeneratorRun.started_at >= a, GeneratorRun.started_at < b).order_by(GeneratorRun.started_at.desc()))]


def generator_totals(s: Session, start: datetime, end: datetime) -> dict:
    """Підсумки завершених запусків, що почались у [start, end)."""
    row = s.execute(select(
        func.count(GeneratorRun.id), func.coalesce(func.sum(GeneratorRun.hours_milli), 0),
        func.coalesce(func.sum(GeneratorRun.fuel_milli), 0), func.coalesce(func.sum(GeneratorRun.fuel_cost), 0),
        func.coalesce(func.sum(GeneratorRun.amort_cost), 0), func.coalesce(func.sum(GeneratorRun.total_cost), 0),
    ).where(GeneratorRun.started_at >= start, GeneratorRun.started_at < end, GeneratorRun.ended_at.is_not(None))).one()
    return {"runs": int(row[0]), "hours_milli": int(row[1]), "fuel_milli": int(row[2]), "fuel_cost": int(row[3]),
            "amort_cost": int(row[4]), "total_cost": int(row[5])}


# =================================================================== списання з майстрів
def charges_total(s: Session, *, master_id: int | None = None, d_from: date | None = None, d_to: date | None = None) -> int:
    q = select(func.coalesce(func.sum(MasterCharge.amount), 0))
    if master_id:
        q = q.where(MasterCharge.master_id == master_id)
    if d_from:
        q = q.where(MasterCharge.charged_on >= d_from)
    if d_to:
        q = q.where(MasterCharge.charged_on <= d_to)
    return int(s.scalar(q) or 0)


def _charge(s: Session, actor: Actor, *, master_id: int, kind: str, amount: int, charged_on: date,
            dedupe_key: str, shift_id: int | None = None, basis: dict | None = None) -> MasterCharge | None:
    """Одне списання. Повторне (той самий dedupe_key) — не створюється (повертає None)."""
    if amount <= 0:
        return None
    if s.scalar(select(MasterCharge.id).where(MasterCharge.dedupe_key == dedupe_key)):
        return None
    c = MasterCharge(master_id=master_id, kind=kind, charged_on=charged_on, period=month_start(charged_on),
                     shift_id=shift_id, amount=amount, dedupe_key=dedupe_key, basis=basis, created_by=actor.user_id)
    s.add(c)
    try:
        with s.begin_nested():
            s.flush()
    except IntegrityError:
        return None
    ledger(s, actor, "master_charge", "master_charge", c.id, master_id=master_id, amount=0,
           master_amount=-amount, salon_amount=amount, details={"kind": kind, **(basis or {})})
    audit(s, actor, "master_charge", c.id, "create", None,
          {"master_id": master_id, "amount": amount, "kind": kind, "charged_on": charged_on})
    return c


def shift_charge(s: Session, master_id: int, sh: Shift) -> tuple[int, str | None, str | None]:
    """Скільки списати при закритті зміни (режим fixed). Повертає (сума, kind, dedupe_key)."""
    if get_setting(s, "utilities_charge_mode") != "fixed":
        return 0, None, None
    amount = int(get_setting(s, "utilities_fixed_amount") or 0)
    if amount <= 0:
        return 0, None, None
    day = (sh.closed_at or clock.now()).date()
    if get_setting(s, "utilities_fixed_per") == "shift":
        return amount, "utilities_shift", f"util-shift:{sh.id}"
    key = f"util-fixed-month:{day:%Y-%m}:{master_id}"
    if s.scalar(select(MasterCharge.id).where(MasterCharge.dedupe_key == key)):
        return 0, None, None
    return amount, "utilities_fixed_month", key


def charge_on_shift_close(s: Session, actor: Actor, sh: Shift) -> int:
    amount, kind, key = shift_charge(s, sh.master_id, sh)
    if not amount:
        return 0
    c = _charge(s, actor, master_id=sh.master_id, kind=kind, amount=amount, charged_on=sh.closed_at.date(),
                dedupe_key=key, shift_id=sh.id, basis={"mode": "fixed", "per": get_setting(s, "utilities_fixed_per")})
    return c.amount if c else 0


def month_costs(s: Session, m: date) -> dict:
    a, b = _month_bounds(m)
    r = s.scalar(select(MeterReading).where(MeterReading.month == month_start(m)))
    g = generator_totals(s, a, b)
    elec = r.cost if r and r.expense_id else 0
    return {"electricity": elec, "electricity_confirmed": bool(r and r.expense_id), "electricity_reading": bool(r),
            "generator": g["total_cost"], "total": elec + g["total_cost"]}


def _largest_remainder(total: int, weights: dict[int, int]) -> dict[int, int]:
    """Поділ total пропорційно вагам без втрати копійок (метод найбільших залишків)."""
    wsum = sum(weights.values())
    if wsum <= 0 or total <= 0:
        return {k: 0 for k in weights}
    base = {k: total * w // wsum for k, w in weights.items()}
    rest = total - sum(base.values())
    order = sorted(weights, key=lambda k: (-(total * weights[k] % wsum), k))
    for k in order[:rest]:
        base[k] += 1
    return base


def allocation_preview(s: Session, actor: Actor, m: date) -> dict:
    """Що буде списано з кожного майстра за місяць (режим month_end), без запису."""
    actor.require(Role.DIRECTOR)
    m = month_start(m)
    costs = month_costs(s, m)
    share = int(get_setting(s, "utilities_charge_share_bp") or 0)
    to_split = costs["total"] * share // 10000
    a, b = _month_bounds(m)
    split = get_setting(s, "utilities_split")
    masters = {u.id: u.full_name for u in s.scalars(select(User).where(User.role == Role.MASTER))}
    rev = dict(s.execute(select(Order.master_id, func.sum(Order.amount))
                         .where(Order.closed_at >= a, Order.closed_at < b).group_by(Order.master_id)).all())
    if split == "equal":
        worked = {mid for (mid,) in s.execute(select(Shift.master_id).where(
            Shift.opened_at >= a, Shift.opened_at < b, Shift.status != ShiftStatus.OPEN).distinct())}
        weights = {mid: 1 for mid in worked | {k for k, v in rev.items() if v}}
    else:
        weights = {k: int(v) for k, v in rev.items() if v}
    alloc = _largest_remainder(to_split, weights)
    existing = charges_total(s, d_from=m, d_to=month_end(m))
    return {"month": m.isoformat(), "costs": costs, "share_bp": share, "to_split": to_split, "split": split,
            "already_charged": existing,
            "rows": [{"master_id": k, "master_name": masters.get(k, f"#{k}"), "revenue": int(rev.get(k) or 0),
                      "amount": v} for k, v in sorted(alloc.items(), key=lambda x: -x[1])]}


def allocate_month(s: Session, actor: Actor, m: date) -> dict:
    """Розподілити витрати місяця між майстрами (режим month_end). Один раз на місяць."""
    actor.require(Role.DIRECTOR)
    m = month_start(m)
    if get_setting(s, "utilities_charge_mode") != "month_end":
        raise Conflict("Розподіл наприкінці місяця вимкнено в налаштуваннях")
    if month_end(m) >= clock.now().date():
        raise Conflict("Розподілити можна після завершення місяця")
    if charges_total(s, d_from=m, d_to=month_end(m)) != 0:
        raise Conflict("За цей місяць уже є списання з майстрів — спершу скасуйте їх")
    p = allocation_preview(s, actor, m)
    if p["to_split"] <= 0 or not p["rows"]:
        raise ValidationError("Немає що розподіляти: немає витрат або виручки майстрів за місяць")
    created = []
    for row in p["rows"]:
        c = _charge(s, actor, master_id=row["master_id"], kind="utilities_month", amount=row["amount"],
                    charged_on=month_end(m), dedupe_key=f"util-month:{m:%Y-%m}:{row['master_id']}",
                    basis={"mode": "month_end", "split": p["split"], "share_bp": p["share_bp"],
                           "month_total": p["costs"]["total"]})
        if c:
            created.append(c)
            u = s.get(User, row["master_id"])
            notifications.enqueue(s, u.telegram_chat_id if u else None,
                                  f"💡 Комунальні та генератор за {MONTHS[m.month - 1]}: ваша частка {_fmt(c.amount)} "
                                  "(врахована у вашому балансі).")
    return {**p, "created": len(created)}


def reverse_month(s: Session, actor: Actor, m: date, reason: str) -> int:
    """Скасувати списання місяця: рядки-сторно; після цього місяць можна розподілити знову."""
    actor.require(Role.DIRECTOR)
    if not (reason or "").strip():
        raise ValidationError("Вкажіть причину скасування")
    m = month_start(m)
    rows = list(s.scalars(select(MasterCharge).where(
        MasterCharge.period == m, MasterCharge.reversed.is_(False), MasterCharge.amount > 0)))
    for c in rows:
        c.reversed = True
        c.dedupe_key = None  # звільняємо ключ — можна розподілити заново
        r = MasterCharge(master_id=c.master_id, kind="reversal", charged_on=c.charged_on, period=c.period,
                         shift_id=c.shift_id, amount=-c.amount, reverses_id=c.id, basis={"reason": reason},
                         created_by=actor.user_id)
        s.add(r)
        s.flush()
        ledger(s, actor, "master_charge_reversal", "master_charge", c.id, master_id=c.master_id, amount=0,
               master_amount=c.amount, salon_amount=-c.amount, details={"reason": reason})
        audit(s, actor, "master_charge", c.id, "reverse", {"amount": c.amount}, {"amount": 0}, reason)
    return len(rows)


def charges_list(s: Session, actor: Actor, *, m: date | None = None, master_id: int | None = None) -> list[dict]:
    if actor.is_master:
        master_id = actor.user_id
    else:
        actor.require(Role.DIRECTOR)
    q = select(MasterCharge)
    if m:
        q = q.where(MasterCharge.period == month_start(m))
    if master_id:
        q = q.where(MasterCharge.master_id == master_id)
    return [{"id": c.id, "master_id": c.master_id, "master_name": c.master.full_name, "kind": c.kind,
             "charged_on": c.charged_on.isoformat(), "amount": c.amount, "reversed": c.reversed,
             "shift_id": c.shift_id} for c in s.scalars(q.order_by(MasterCharge.id.desc()).limit(500))]


# =================================================================== аналітика
def month_summary(s: Session, actor: Actor, m: date) -> dict:
    """Комунальні та генератор за місяць: витрати, мотогодини, бензин, списання, вплив на прибуток."""
    actor.require(Role.DIRECTOR)
    m = month_start(m)
    a, b = _month_bounds(m)
    r = s.scalar(select(MeterReading).where(MeterReading.month == m))
    g = generator_totals(s, a, b)
    charges = {}
    for c in s.scalars(select(MasterCharge).where(MasterCharge.charged_on >= m, MasterCharge.charged_on <= month_end(m))):
        row = charges.setdefault(c.master_id, {"master_id": c.master_id, "master_name": c.master.full_name, "amount": 0})
        row["amount"] += c.amount
    charged = sum(x["amount"] for x in charges.values())
    elec = r.cost if r else 0
    other_util = int(s.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(
        Expense.category == ExpenseCategory.UTILITIES, Expense.is_deleted.is_(False),
        Expense.spent_on >= m, Expense.spent_on <= month_end(m),
        Expense.id.not_in(select(MeterReading.expense_id).where(MeterReading.expense_id.is_not(None))))) or 0)
    total = elec + g["total_cost"]
    return {
        "month": m.isoformat(),
        "electricity": {"kwh": r.used_milli / 1000 if r else 0, "tariff": r.tariff if r else None, "cost": elec,
                        "paid": r.paid_amount if r else None, "confirmed": bool(r and r.expense_id), "has_reading": bool(r)},
        "generator": {"runs": g["runs"], "hours": g["hours_milli"] / 1000, "fuel_l": g["fuel_milli"] / 1000,
                      "fuel_cost": g["fuel_cost"], "amort_cost": g["amort_cost"], "total": g["total_cost"]},
        "other_utilities": other_util,
        "total": total,
        "charged_to_masters": charged,
        "by_master": sorted(charges.values(), key=lambda x: -x["amount"]),
        # вплив на чистий прибуток салону: витрати мінус те, що компенсували майстри
        "net_impact": -(total - charged),
        "mode": get_setting(s, "utilities_charge_mode"),
    }
