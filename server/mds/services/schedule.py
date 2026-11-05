"""Графік роботи майстрів.

Порядок визначення робочих годин майстра на дату:
  1. запис на цю дату (schedule_days) — робочий день з годинами або вихідний;
  2. особистий тижневий шаблон майстра (users.schedule);
  3. графік салону (налаштування «schedule»).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import Forbidden, ValidationError
from ..models import Role, ScheduleDay, User
from .common import Actor, audit, get_or_404, get_setting

MAX_RANGE_DAYS = 62


def _hm(v: str) -> str:
    try:
        h, m = str(v).split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValidationError("Час у форматі ГГ:ХХ, напр. 09:00") from None
    return f"{h:02d}:{m:02d}"


def day_span(s: Session, master: User, day: date, override: ScheduleDay | None = None, *, lookup: bool = True):
    """(start, end, source) або (None, None, source) для вихідного."""
    if override is None and lookup:
        override = s.scalar(select(ScheduleDay).where(ScheduleDay.master_id == master.id, ScheduleDay.day == day))
    if override is not None:
        if override.is_off:
            return None, None, "day"
        return override.start, override.end, "day"
    if master.schedule is not None:
        span, src = master.schedule.get(str(day.weekday())), "master"
    else:
        span, src = (get_setting(s, "schedule") or {}).get(str(day.weekday())), "salon"
    return (span[0], span[1], src) if span else (None, None, src)


def set_day(s: Session, actor: Actor, master_id: int, day: date, *, off: bool = False,
            start: str | None = None, end: str | None = None) -> ScheduleDay:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    master: User = get_or_404(s, User, master_id, "Майстра")
    if master.role != Role.MASTER:
        raise ValidationError("Графік ведеться лише для майстрів")
    if not off:
        start, end = _hm(start), _hm(end)
        if start >= end:
            raise ValidationError("Початок має бути раніше кінця")
    row = s.scalar(select(ScheduleDay).where(ScheduleDay.master_id == master_id, ScheduleDay.day == day))
    before = {"off": row.is_off, "start": row.start, "end": row.end} if row else None
    if row is None:
        row = ScheduleDay(master_id=master_id, day=day)
        s.add(row)
    row.is_off, row.start, row.end = off, (None if off else start), (None if off else end)
    s.flush()
    audit(s, actor, "schedule", master_id, "set_day", before, {"day": day, "off": off, "start": row.start, "end": row.end})
    return row


def reset_day(s: Session, actor: Actor, master_id: int, day: date) -> None:
    """Повернути день до тижневого шаблону."""
    actor.require(Role.DIRECTOR, Role.ADMIN)
    row = s.scalar(select(ScheduleDay).where(ScheduleDay.master_id == master_id, ScheduleDay.day == day))
    if row:
        audit(s, actor, "schedule", master_id, "reset_day", {"day": day, "off": row.is_off, "start": row.start, "end": row.end}, None)
        s.delete(row)


def copy_week(s: Session, actor: Actor, master_id: int, from_monday: date, weeks: int = 1) -> int:
    """Скопіювати графік тижня (з урахуванням змін по датах) на наступні тижні."""
    actor.require(Role.DIRECTOR, Role.ADMIN)
    if from_monday.weekday() != 0:
        raise ValidationError("Тиждень має починатися з понеділка")
    if not (1 <= weeks <= 8):
        raise ValidationError("Від 1 до 8 тижнів")
    master: User = get_or_404(s, User, master_id, "Майстра")
    n = 0
    for i in range(7):
        src = from_monday + timedelta(days=i)
        start, end, _ = day_span(s, master, src)
        for w in range(1, weeks + 1):
            set_day(s, actor, master_id, src + timedelta(days=7 * w), off=start is None, start=start, end=end)
            n += 1
    return n


def parity_pattern(s: Session, actor: Actor, master_id: int, parity: str, date_from: date, date_to: date,
                   start: str | None = None, end: str | None = None) -> int:
    """Графік «парні / непарні дні»: робочі — дати з потрібною парністю (2, 4, 6… або 1, 3, 5…), решта — вихідні.
    Години — вказані або звичайні години майстра."""
    actor.require(Role.DIRECTOR, Role.ADMIN)
    if parity not in ("even", "odd"):
        raise ValidationError("Оберіть «парні» або «непарні» дні")
    if date_to < date_from or (date_to - date_from).days > 92:
        raise ValidationError("Період — до 3 місяців")
    master: User = get_or_404(s, User, master_id, "Майстра")
    if not (start and end):
        start, end = _usual_hours(s, master)
    n, d = 0, date_from
    while d <= date_to:
        work = (d.day % 2 == 0) == (parity == "even")
        set_day(s, actor, master_id, d, off=not work, start=start, end=end)
        n += 1
        d += timedelta(days=1)
    return n


def grid(s: Session, actor: Actor, date_from: date, date_to: date) -> dict:
    if date_to < date_from or (date_to - date_from).days > MAX_RANGE_DAYS:
        raise ValidationError("Невірний період графіка")
    q = select(User).where(User.role == Role.MASTER, User.is_active.is_(True)).order_by(User.full_name)
    if actor.is_master:
        q = q.where(User.id == actor.user_id)
    elif not actor.is_staff_manager:
        raise Forbidden("Недостатньо прав")
    masters = list(s.scalars(q))
    rows = s.scalars(select(ScheduleDay).where(
        ScheduleDay.master_id.in_([m.id for m in masters] or [0]), ScheduleDay.day >= date_from, ScheduleDay.day <= date_to))
    overrides = {(r.master_id, r.day): r for r in rows}
    days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    cells = {}
    hours = {}
    for m in masters:
        cells[m.id] = {}
        for d in days:
            start, end, src = day_span(s, m, d, overrides.get((m.id, d)), lookup=False)
            t_start, t_end, _ = day_span(s, m, d, None, lookup=False)  # тижневий шаблон без змін по датах
            cells[m.id][d.isoformat()] = {"start": start, "end": end, "off": start is None, "source": src,
                                          "template": [t_start, t_end] if t_start else None}
        hours[m.id] = _usual_hours(s, m)
    return {"masters": [{"id": m.id, "full_name": m.full_name, "usual_hours": hours[m.id]} for m in masters],
            "days": [d.isoformat() for d in days], "cells": cells}


def _usual_hours(s: Session, master: User) -> list[str]:
    """Звичайні години майстра (найчастіші в тижневому шаблоні) — для дня, який позначають робочим."""
    tpl = master.schedule if master.schedule is not None else (get_setting(s, "schedule") or {})
    spans = [tuple(v) for v in tpl.values() if v]
    if not spans:
        return ["09:00", "18:00"]
    return list(max(set(spans), key=spans.count))
