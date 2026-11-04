"""Звітність для бухгалтера.

Дохід ФОП = каса (готівка) + банк (безготівка): усе, що заплатили клієнти, на дату оплати (закриття замовлення),
у поточній редакції замовлення (після виправлень/повернень). Частка майстра, комісія банку і витрати дохід ФОП
НЕ зменшують. Управлінська виручка, частки майстрів, витрати й прибуток — окремо (аркуш «Підсумок»).

Excel «Звіт для бухгалтера» за довільний період — 7 аркушів:
1. Доходи ФОП · 2. Готівкові надходження · 3. Банківські надходження · 4. Фіскальні чеки та повернення ·
5. Витрати салону · 6. Нарахування та виплати майстрам · 7. Підсумок за період.
"""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from ..errors import ValidationError
from ..models import (
    EmployeeProfile, Expense, LedgerEntry, Order, PaymentMethod, Payout, PayrollAccrual, PayrollPayment, Role, User,
)
from . import analytics, tax_report
from .common import Actor, all_settings

METHOD_UA = {PaymentMethod.CASH: "Готівка", PaymentMethod.CARD: "Картка (банк)",
             PaymentMethod.TRANSFER: "Переказ на рахунок (банк)"}
EXPENSE_UA = {"rent": "Оренда", "utilities": "Комунальні послуги", "advertising": "Реклама",
              "materials": "Матеріали", "fuel": "Пальне", "equipment": "Обладнання",
              "household": "Господарські витрати", "other": "Інше"}
MAX_DAYS = 366 * 3


def period(year: int | None = None, quarter: int | None = None, date_from: date | None = None,
           date_to: date | None = None) -> tuple[date, date]:
    """Межі періоду включно: або «з — по», або рік / квартал."""
    if date_from or date_to:
        if not (date_from and date_to):
            raise ValidationError("Вкажіть обидві дати періоду")
        if date_to < date_from:
            raise ValidationError("Кінець періоду раніше початку")
        if (date_to - date_from).days > MAX_DAYS:
            raise ValidationError("Період — не більше 3 років")
        return date_from, date_to
    if year is None:
        raise ValidationError("Вкажіть рік або період")
    a, b = tax_report._period(year, quarter)
    return a, b - timedelta(days=1)


def _uah(kop: int) -> Decimal:
    return Decimal(kop) / 100


def _is_cash(o: Order) -> bool:
    return o.payment_method == PaymentMethod.CASH


def _orders(s, a: date, b: date) -> list[Order]:
    return tax_report._orders(s, a, b + timedelta(days=1))


def _history(s, order_ids: list[int]) -> dict[int, dict]:
    """Перша сума (order_closed) і виправлення кожного замовлення з фінансового журналу."""
    out: dict[int, dict] = {}
    if not order_ids:
        return out
    rows = s.scalars(select(LedgerEntry).where(LedgerEntry.ref_type == "order", LedgerEntry.ref_id.in_(order_ids),
                                               LedgerEntry.kind.in_(("order_closed", "order_reversal", "order_corrected")))
                     .order_by(LedgerEntry.id))
    for e in rows:
        h = out.setdefault(e.ref_id, {"original": None, "corrections": [], "_was": None})
        if e.kind == "order_closed":
            h["original"] = e.amount
        elif e.kind == "order_reversal":
            h["_was"] = -e.amount
        else:
            h["corrections"].append({"ts": e.ts, "was": h["_was"], "became": e.amount,
                                     "reason": (e.details or {}).get("reason") or ""})
    return out


def _status(o: Order, h: dict | None) -> tuple[str, int]:
    """(статус оплати, сума повернення) для реєстру доходів."""
    orig = (h or {}).get("original") or o.amount
    if not (h and h["corrections"]):
        return "оплачено", 0
    if o.amount == 0 and orig > 0:
        return "повернено повністю", orig
    if o.amount < orig:
        return "частково повернено", orig - o.amount
    return "виправлено", 0


def masters_report(s, actor: Actor, a: date, b: date) -> list[dict]:
    """По кожному майстру за період: нараховано (частка з замовлень), виплачено (з датами), списання, залишок
    на кінець періоду. Управлінський розрахунок — НЕ офіційна зарплата."""
    actor.require(Role.DIRECTOR)
    from .utilities import charges_total

    lo, hi = datetime.combine(a, datetime.min.time()), datetime.combine(b + timedelta(days=1), datetime.min.time())
    out = []
    for u in s.scalars(select(User).where(User.role == Role.MASTER).order_by(User.full_name)):
        accrued = int(s.scalar(select(func.coalesce(func.sum(Order.master_earning), 0)).where(
            Order.master_id == u.id, Order.closed_at >= lo, Order.closed_at < hi)) or 0)
        pays = list(s.scalars(select(Payout).where(Payout.master_id == u.id, Payout.paid_at >= lo, Payout.paid_at < hi)
                              .order_by(Payout.paid_at)))
        charged = charges_total(s, master_id=u.id, d_from=a, d_to=b)
        acc_end = int(s.scalar(select(func.coalesce(func.sum(Order.master_earning), 0)).where(
            Order.master_id == u.id, Order.closed_at < hi)) or 0)
        paid_end = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(
            Payout.master_id == u.id, Payout.paid_at < hi)) or 0)
        balance = acc_end - paid_end - charges_total(s, master_id=u.id, d_to=b)
        if not (accrued or pays or charged or balance):
            continue
        out.append({"master_id": u.id, "name": u.full_name, "share_bp": u.share_bp, "accrued": accrued,
                    "paid": sum(p.amount for p in pays), "charged": charged, "balance_end": balance,
                    "payouts": [{"date": p.paid_at.date().isoformat(), "amount": p.amount, "method": p.method,
                                 "note": p.note or ""} for p in pays]})
    return out


def summary(s, actor: Actor, year: int | None = None, quarter: int | None = None,
            date_from: date | None = None, date_to: date | None = None) -> dict:
    actor.require(Role.DIRECTOR)
    a, b = period(year, quarter, date_from, date_to)
    st = all_settings(s)
    orders = _orders(s, a, b)
    hist = _history(s, [o.id for o in orders])
    cash = sum(o.amount for o in orders if _is_cash(o))
    noncash = sum(o.amount for o in orders if not _is_cash(o))
    refunds = sum(_status(o, hist.get(o.id))[1] for o in orders)
    months: dict[str, dict] = {}
    m = a.replace(day=1)
    while m <= b:
        months[m.strftime("%Y-%m")] = {"month": m.strftime("%Y-%m"), "cash": 0, "noncash": 0, "total": 0, "count": 0}
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    for o in orders:
        r = months[o.closed_at.strftime("%Y-%m")]
        r["cash" if _is_cash(o) else "noncash"] += o.amount
        r["total"] += o.amount
        r["count"] += 1
    quarters: dict[str, dict] = {}
    for mr in months.values():
        y, mm = int(mr["month"][:4]), int(mr["month"][5:])
        q = quarters.setdefault(f"{y}-{(mm - 1) // 3 + 1}", {"label": f"{(mm - 1) // 3 + 1} квартал {y}",
                                                            "cash": 0, "noncash": 0, "total": 0, "count": 0})
        for k in ("cash", "noncash", "total", "count"):
            q[k] += mr[k]
    ytd = int(s.scalar(select(func.coalesce(func.sum(Order.amount), 0)).where(
        Order.closed_at >= datetime(b.year, 1, 1), Order.closed_at < datetime.combine(b + timedelta(days=1), datetime.min.time()))) or 0)
    limit = int(st.get("fop_income_limit") or 0)
    dash = analytics.dashboard(s, actor, a, b)
    employees = s.scalar(select(func.count(EmployeeProfile.id)).where(
        EmployeeProfile.hired_on.isnot(None), EmployeeProfile.hired_on <= b,
        (EmployeeProfile.fired_on.is_(None)) | (EmployeeProfile.fired_on >= a))) or 0
    mrep = masters_report(s, actor, a, b)
    return {
        "from": a, "to": b,
        "fop": {"name": st.get("fop_name") or "", "tin": st.get("fop_tin") or "", "group": st.get("fop_group") or 1,
                "kved": st.get("fop_kved") or "", "kved_name": st.get("fop_kved_name") or "",
                "address": st.get("fop_address") or "", "sti": st.get("fop_sti_name") or ""},
        "income": {"total": cash + noncash, "cash": cash, "noncash": noncash, "count": len(orders), "refunds": refunds},
        "months": list(months.values()),
        "quarters": list(quarters.values()),
        "year_to_date": ytd,
        "limit": {"amount": limit, "used_bp": (ytd * 10_000 // limit) if limit else None,
                  "remaining": (limit - ytd) if limit else None},
        "management": {"revenue": dash["revenue"], "masters_share": dash["masters_share"], "materials": dash["materials"],
                       "expenses": dash["operating_expenses"], "net_profit": dash["net_profit"],
                       "payouts": dash["payouts"]},
        "masters": {"accrued": sum(r["accrued"] for r in mrep), "paid": sum(r["paid"] for r in mrep),
                    "balance_end": sum(r["balance_end"] for r in mrep)},
        "employees": employees,
        "fiscal": {"enabled": False, "note": "Фіскалізація (Checkbox) не підключена — етап 2 плану"},
        "bank": {"connected": False, "note": "Банківська виписка не підключена — етап 3 плану (monobank)"},
    }


def xlsx(s, actor: Actor, year: int | None = None, quarter: int | None = None,
         date_from: date | None = None, date_to: date | None = None) -> tuple[bytes, str]:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    d = summary(s, actor, year, quarter, date_from, date_to)
    a, b = d["from"], d["to"]
    orders = _orders(s, a, b)
    hist = _history(s, [o.id for o in orders])
    money_fmt, bold, head_fill = '#,##0.00', Font(bold=True), PatternFill("solid", fgColor="E8E4D8")
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(title, header, rows, money_cols=(), widths=(), total=None, note=None):
        ws = wb.create_sheet(title)
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill, c.alignment = bold, head_fill, Alignment(wrap_text=True, vertical="center")
        for r in rows:
            ws.append(r)
        if total:
            ws.append(total)
            for c in ws[ws.max_row]:
                c.font = bold
        if note:
            ws.append([])
            ws.append([note])
            ws.cell(ws.max_row, 1).font = Font(italic=True, color="666666")
        for col in money_cols:
            for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
                if isinstance(row[0].value, Decimal):
                    row[0].number_format = money_fmt
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        return ws

    inc = d["income"]
    # 1. Доходи ФОП — реєстр усіх оплат
    reg = []
    for o in orders:
        stt, ref = _status(o, hist.get(o.id))
        orig = (hist.get(o.id) or {}).get("original") or o.amount
        reg.append([o.closed_at.strftime("%d.%m.%Y"), o.closed_at.strftime("%H:%M"), o.id,
                    o.service.name if o.service else "", o.master.full_name if o.master else "",
                    METHOD_UA.get(o.payment_method, o.payment_method), _uah(orig), _uah(ref), _uah(o.amount), stt])
    sheet("1. Доходи ФОП", ["Дата", "Час", "№ замовлення", "Послуга", "Майстер", "Спосіб оплати", "Сума замовлення",
                            "Повернено", "Отримано (дохід)", "Статус оплати"], reg, (7, 8, 9),
          (12, 7, 13, 34, 26, 24, 15, 13, 16, 20),
          ["Разом", "", "", "", "", "", "", _uah(inc["refunds"]), _uah(inc["total"]), f"{inc['count']} оплат"],
          "Дохід ФОП = сума, отримана від клієнта на дату оплати; частка майстра й комісія банку його не зменшують.")

    def by_day(cash: bool):
        days: dict[date, list[int]] = {}
        for o in orders:
            if _is_cash(o) == cash:
                r = days.setdefault(o.closed_at.date(), [0, 0])
                r[0] += 1
                r[1] += o.amount
        return [[k.strftime("%d.%m.%Y"), v[0], _uah(v[1])] for k, v in sorted(days.items())]
    # 2–3. Готівка (каса) і банк
    sheet("2. Готівкові надходження", ["Дата", "Кількість оплат", "Сума, грн"], by_day(True), (3,), (14, 16, 16),
          ["Разом", sum(1 for o in orders if _is_cash(o)), _uah(inc["cash"])], "Каса (рахунок 301 у бухгалтера).")
    sheet("3. Банківські надходження", ["Дата", "Кількість оплат", "Сума, грн"], by_day(False), (3,), (14, 16, 16),
          ["Разом", sum(1 for o in orders if not _is_cash(o)), _uah(inc["noncash"])],
          "Банк (рахунок 311): оплати карткою/переказом за даними замовлень. " + d["bank"]["note"] + ".")
    # 4. Фіскальні чеки та повернення
    refunds = []
    for o in orders:
        for c in (hist.get(o.id) or {}).get("corrections", []):
            refunds.append([c["ts"].strftime("%d.%m.%Y"), o.id, o.closed_at.strftime("%d.%m.%Y"),
                            METHOD_UA.get(o.payment_method, o.payment_method), _uah(c["was"] or 0), _uah(c["became"]),
                            _uah((c["became"] or 0) - (c["was"] or 0)), c["reason"], "—"])
    sheet("4. Фіскальні чеки та повернення", ["Дата виправлення", "№ замовлення", "Дата оплати", "Спосіб оплати",
                                              "Було, грн", "Стало, грн", "Різниця, грн", "Причина", "Фіскальний чек"],
          refunds or [["Повернень і виправлень за період немає"]], (5, 6, 7), (16, 13, 12, 22, 12, 12, 12, 34, 16),
          note=d["fiscal"]["note"] + ".")
    # 5. Витрати
    exps = list(s.scalars(select(Expense).where(Expense.spent_on >= a, Expense.spent_on <= b, Expense.is_deleted.is_(False))
                          .order_by(Expense.spent_on, Expense.id)))
    sheet("5. Витрати салону", ["Дата", "Категорія", "Сума, грн", "Спосіб оплати", "Коментар"],
          [[e.spent_on.strftime("%d.%m.%Y"), EXPENSE_UA.get(e.category, e.category), _uah(e.amount),
            METHOD_UA.get(getattr(e, "payment_method", None) or "", getattr(e, "payment_method", None) or ""),
            e.description or ""] for e in exps] or [["Витрат за період немає"]], (3,), (12, 24, 14, 20, 50),
          ["Разом", "", _uah(sum(e.amount for e in exps))] if exps else None,
          "Витрати — для управлінського обліку; дохід ФОП на єдиному податку вони НЕ зменшують.")
    # 6. Нарахування та виплати майстрам (+ офіційна зарплата найманих)
    rows6 = []
    for r in masters_report(s, actor, a, b):
        rows6.append([r["name"], "Підсумок", "", _uah(r["accrued"]), _uah(r["paid"]), _uah(r["charged"]), _uah(r["balance_end"]), ""])
        for p in r["payouts"]:
            rows6.append([r["name"], "Виплата", date.fromisoformat(p["date"]).strftime("%d.%m.%Y"), "", _uah(p["amount"]), "",
                          "", METHOD_UA.get(p["method"], p["method"]) + (f" · {p['note']}" if p["note"] else "")])
    ws6 = sheet("6. Майстри, нараховано, виплати", ["Майстер", "Рядок", "Дата виплати", "Нараховано (частка), грн",
                                                       "Виплачено, грн", "Списано (комунальні тощо), грн",
                                                       "Залишок до виплати на кінець періоду, грн", "Спосіб / примітка"],
                rows6 or [["Нарахувань і виплат майстрам за період немає"]], (4, 5, 6, 7), (28, 11, 14, 18, 15, 18, 22, 34),
                ["Разом", "", "", _uah(d["masters"]["accrued"]), _uah(d["masters"]["paid"]), "", _uah(d["masters"]["balance_end"]), ""]
                if rows6 else None,
                "Управлінський розрахунок частки майстра (напр. 50/50) — НЕ офіційна зарплата.")
    pa = list(s.scalars(select(PayrollAccrual).where(PayrollAccrual.month >= a.replace(day=1), PayrollAccrual.month <= b)))
    pp = list(s.scalars(select(PayrollPayment).where(PayrollPayment.paid_on >= a, PayrollPayment.paid_on <= b)))
    if pa or pp:
        ws6.append([])
        ws6.append(["Офіційна зарплата найманих працівників (для бухгалтера)"])
        ws6.cell(ws6.max_row, 1).font = bold
        ws6.append(["Працівник", "Місяць", "РНОКПП", "Нараховано", "ПДФО", "Військовий збір", "ЄСВ роботодавця", "На руки"])
        profs = {p.user_id: p for p in s.scalars(select(EmployeeProfile))}
        for x in pa:
            ws6.append([x.user.full_name, x.month.strftime("%m.%Y"), (profs.get(x.user_id).tin if profs.get(x.user_id) else ""),
                        _uah(x.gross), _uah(x.pdfo), _uah(x.military), _uah(x.esv_employer), _uah(x.net)])
        for x in pp:
            ws6.append([s.get(User, x.user_id).full_name, "виплата", x.paid_on.strftime("%d.%m.%Y"), "", "", "", "", _uah(x.amount)])
    # 7. Підсумок
    ws = wb.create_sheet("7. Підсумок за період")
    f, mg = d["fop"], d["management"]
    lines = [
        ("Звіт для бухгалтера — MDS Connect", None),
        ("Період", f"{a:%d.%m.%Y} — {b:%d.%m.%Y}"),
        ("ФОП", f["name"]), ("РНОКПП", f["tin"]), ("Група єдиного податку", f["group"]),
        ("КВЕД", f"{f['kved']} {f['kved_name']}".strip()), ("Податкова адреса", f["address"]), ("Податкова", f["sti"]),
        (None, None),
        ("ДОХІД ФОП ЗА ПЕРІОД (каса + банк)", _uah(inc["total"])),
        ("  готівка (каса, рах. 301)", _uah(inc["cash"])),
        ("  безготівка (банк, рах. 311)", _uah(inc["noncash"])),
        ("  повернення/зменшення (вже враховано)", _uah(inc["refunds"])),
        ("Кількість оплат", inc["count"]),
        ("Дохід з початку року (на кінець періоду)", _uah(d["year_to_date"])),
        ("Ліміт доходу групи на рік", _uah(d["limit"]["amount"]) if d["limit"]["amount"] else "не задано"),
        (None, None),
        ("Управлінський облік (НЕ зменшує дохід ФОП):", None),
        ("  Виручка салону", _uah(mg["revenue"])),
        ("  Частки майстрів (нараховано)", _uah(mg["masters_share"])),
        ("  Матеріали в замовленнях", _uah(mg["materials"])),
        ("  Операційні витрати", _uah(mg["expenses"])),
        ("  Управлінський прибуток", _uah(mg["net_profit"])),
        ("  Фактично виплачено майстрам", _uah(d["masters"]["paid"])),
        ("  Залишок до виплати майстрам", _uah(d["masters"]["balance_end"])),
        ("Наймані працівники у періоді", d["employees"]),
        (None, None),
        ("Фіскалізація", d["fiscal"]["note"]), ("Банк", d["bank"]["note"]),
    ]
    for k, v in lines:
        ws.append([k, v])
        if isinstance(v, Decimal):
            ws.cell(ws.max_row, 2).number_format = money_fmt
    ws["A1"].font = Font(bold=True, size=13)
    ws["A10"].font = ws["B10"].font = bold
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 44, 60
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"zvit-dlia-bukhgaltera-{a:%Y%m%d}-{b:%Y%m%d}.xlsx"

