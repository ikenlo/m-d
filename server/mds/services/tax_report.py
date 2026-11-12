from __future__ import annotations

import csv
import io
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..errors import ValidationError
from ..models import Order, OrderStatus, PaymentMethod, Role
from .common import Actor, get_setting

METHOD_NAMES = {PaymentMethod.CASH: "Готівка", PaymentMethod.CARD: "Картка", PaymentMethod.TRANSFER: "Переказ на рахунок"}


def _period(year: int, quarter: int | None) -> tuple[date, date]:
    if not (2000 <= year <= 2100):
        raise ValidationError("Некоректний рік")
    if quarter is None:
        return date(year, 1, 1), date(year + 1, 1, 1)
    if quarter not in (1, 2, 3, 4):
        raise ValidationError("Квартал — від 1 до 4")
    start = date(year, 3 * (quarter - 1) + 1, 1)
    end = date(year + 1, 1, 1) if quarter == 4 else date(year, 3 * quarter + 1, 1)
    return start, end


def _orders(s: Session, start: date, end: date) -> list[Order]:
    lo, hi = datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())
    return list(s.scalars(select(Order).where(Order.closed_at >= lo, Order.closed_at < hi, Order.status == OrderStatus.COMPLETED)
                          .options(joinedload(Order.service), joinedload(Order.master)).order_by(Order.closed_at)))


def _empty() -> dict:
    return {"total": 0, "count": 0, **{m: 0 for m in PaymentMethod.ALL}}


def _add(bucket: dict, o: Order) -> None:
    bucket["total"] += o.amount
    bucket["count"] += 1
    bucket[o.payment_method] = bucket.get(o.payment_method, 0) + o.amount


def income_report(s: Session, actor: Actor, year: int, quarter: int | None = None) -> dict:
    actor.require(Role.DIRECTOR)
    start, end = _period(year, quarter)
    orders = _orders(s, start, end)

    months: dict[str, dict] = {}
    m = start
    while m < end:
        months[m.strftime("%Y-%m")] = {"month": m.strftime("%Y-%m"), **_empty()}
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    quarters = {q: {"quarter": q, **_empty()} for q in ([quarter] if quarter else [1, 2, 3, 4])}
    total = _empty()
    for o in orders:
        _add(months[o.closed_at.strftime("%Y-%m")], o)
        _add(quarters[(o.closed_at.month - 1) // 3 + 1], o)
        _add(total, o)

    ytd = sum(o.amount for o in _orders(s, date(year, 1, 1), end)) if quarter else total["total"]
    limit = int(get_setting(s, "fop_income_limit") or 0)
    return {
        "year": year, "quarter": quarter, "start": start, "end": date.fromordinal(end.toordinal() - 1),
        "months": list(months.values()), "quarters": list(quarters.values()), "total": total,
        "year_to_date": ytd,
        "limit": {"amount": limit, "used_bp": (ytd * 10_000 // limit) if limit else None,
                  "remaining": (limit - ytd) if limit else None},
    }


def _uah(kop: int) -> str:
    sign = "-" if kop < 0 else ""
    kop = abs(kop)
    return f"{sign}{kop // 100},{kop % 100:02d}"


def income_csv(s: Session, actor: Actor, year: int, quarter: int | None = None, detail: str = "day") -> tuple[str, str]:
    actor.require(Role.DIRECTOR)
    if detail not in ("day", "order"):
        raise ValidationError("Деталізація: day або order")
    start, end = _period(year, quarter)
    orders = _orders(s, start, end)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    if detail == "day":
        w.writerow(["Дата", "Дохід, грн", "Готівка, грн", "Картка, грн", "Переказ, грн", "Кількість замовлень"])
        days: dict[date, dict] = {}
        for o in orders:
            _add(days.setdefault(o.closed_at.date(), _empty()), o)
        for d, b in sorted(days.items()):
            w.writerow([d.strftime("%d.%m.%Y"), _uah(b["total"]), _uah(b[PaymentMethod.CASH]), _uah(b[PaymentMethod.CARD]),
                        _uah(b[PaymentMethod.TRANSFER]), b["count"]])
    else:
        w.writerow(["Дата", "Час", "Замовлення №", "Послуга", "Майстер", "Спосіб оплати", "Сума, грн"])
        for o in orders:
            w.writerow([o.closed_at.strftime("%d.%m.%Y"), o.closed_at.strftime("%H:%M"), o.id,
                        o.service.name if o.service else "", o.master.full_name if o.master else "",
                        METHOD_NAMES.get(o.payment_method, o.payment_method), _uah(o.amount)])
    total = sum(o.amount for o in orders)
    w.writerow([])
    w.writerow(["Разом", _uah(total)])
    period = f"{year}" + (f"-Q{quarter}" if quarter else "")
    return "﻿" + buf.getvalue(), f"dohody-{period}-{'dni' if detail == 'day' else 'zamovlennia'}.csv"
