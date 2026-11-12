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
    MeterReading,
    Role,
)
from .common import Actor, audit, get_setting
from .expenses import create_expense, update_expense

MONTHS = ["січень", "лютий", "березень", "квітень", "травень", "червень", "липень", "серпень",
          "вересень", "жовтень", "листопад", "грудень"]


def _r(a: int, b: int) -> int:
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


def _hours(milli: int) -> str:
    return f"{milli / 1000:g}".replace(".", ",")


def _milli(v, what: str) -> int:
    from .stock import to_milli

    try:
        return to_milli(v)
    except ValidationError:
        raise ValidationError(f"{what}: невірне число") from None


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
    actor.require(Role.DIRECTOR, Role.MASTER)
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
    actor.require(Role.DIRECTOR, Role.MASTER)
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


def record_hours(s: Session, actor: Actor, *, on: date, current=None, hours=None, note: str | None = None) -> GeneratorRun:
    actor.require(Role.DIRECTOR)
    if (current is None) == (hours is None):
        raise ValidationError("Вкажіть показник лічильника мотогодин або кількість відпрацьованих годин")
    if on > clock.now().date():
        raise ValidationError("Дата не може бути в майбутньому")
    if current is not None:
        target = _milli(current, "Показник лічильника")
        was = int(round(generator_state(s, actor)["motohours"] * 1000))
        hm = target - was
        if hm <= 0:
            raise ValidationError(f"Показник має бути більший за попередній ({_hours(was)} мотогод.)")
        note = note or f"Лічильник: {_hours(was)} → {_hours(target)} мотогод."
    else:
        hm = _milli(hours, "Мотогодини")
    if hm <= 0 or hm > 24 * 31 * 1000:
        raise ValidationError("Мотогодини: більше 0 і не більше місяця")
    st = generator_settings(s)
    start = datetime.combine(on, datetime.min.time()).replace(hour=9)
    g = GeneratorRun(started_at=start, ended_at=start + timedelta(seconds=hm * 3600 // 1000), running=None, hours_milli=hm,
                     fuel_lph_milli=st["fuel_lph_milli"], fuel_price=st["fuel_price"], amort_per_hour=st["amort_per_hour"],
                     power_outage=False, note=note, started_by=actor.user_id, ended_by=actor.user_id)
    for k, v in _costs(hm, g.fuel_lph_milli, g.fuel_price, g.amort_per_hour).items():
        setattr(g, k, v)
    s.add(g)
    s.flush()
    audit(s, actor, "generator", g.id, "hours", None, run_public(g), note)
    return g


def generator_history(s: Session, actor: Actor, months: int = 6) -> list[dict]:
    actor.require(Role.DIRECTOR)
    months = max(1, min(int(months), 24))
    cur = month_start(clock.now().date())
    out = []
    for i in range(months - 1, -1, -1):
        y, mth = divmod(cur.year * 12 + cur.month - 1 - i, 12)
        m = date(y, mth + 1, 1)
        a, b = _month_bounds(m)
        g = generator_totals(s, a, b)
        out.append({"month": m.isoformat(), "runs": g["runs"], "hours": g["hours_milli"] / 1000, "fuel_l": g["fuel_milli"] / 1000,
                    "fuel_cost": g["fuel_cost"], "amort_cost": g["amort_cost"], "total": g["total_cost"]})
    return out


def correct_run(s: Session, actor: Actor, run_id: int, data: dict, reason: str) -> GeneratorRun:
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
    actor.require(Role.DIRECTOR, Role.MASTER)
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
    actor.require(Role.DIRECTOR)
    a, b = _month_bounds(month)
    now = clock.now()
    return [run_public(g, now) for g in s.scalars(select(GeneratorRun).where(
        GeneratorRun.started_at >= a, GeneratorRun.started_at < b).order_by(GeneratorRun.started_at.desc()))]


def generator_totals(s: Session, start: datetime, end: datetime) -> dict:
    row = s.execute(select(
        func.count(GeneratorRun.id), func.coalesce(func.sum(GeneratorRun.hours_milli), 0),
        func.coalesce(func.sum(GeneratorRun.fuel_milli), 0), func.coalesce(func.sum(GeneratorRun.fuel_cost), 0),
        func.coalesce(func.sum(GeneratorRun.amort_cost), 0), func.coalesce(func.sum(GeneratorRun.total_cost), 0),
    ).where(GeneratorRun.started_at >= start, GeneratorRun.started_at < end, GeneratorRun.ended_at.is_not(None))).one()
    return {"runs": int(row[0]), "hours_milli": int(row[1]), "fuel_milli": int(row[2]), "fuel_cost": int(row[3]),
            "amort_cost": int(row[4]), "total_cost": int(row[5])}


def month_summary(s: Session, actor: Actor, m: date) -> dict:
    actor.require(Role.DIRECTOR)
    m = month_start(m)
    a, b = _month_bounds(m)
    r = s.scalar(select(MeterReading).where(MeterReading.month == m))
    g = generator_totals(s, a, b)
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
        "net_impact": -total,
    }
