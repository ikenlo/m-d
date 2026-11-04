"""Звітність для бухгалтера: дані, на основі яких бухгалтер сам складає й подає звітність ФОП.

Логіка доходу (як у 1С у бухгалтера): каса (готівка, рах. 301) + банк (безготівка, рах. 311) = дохід ФОП за період.
Дохід — сума, яку заплатив клієнт, на дату оплати (закриття замовлення); комісія банку дохід не зменшує;
витрати на єдиному податку дохід не зменшують. Програма нічого не подає в ДПС.

Пакет Excel (.xlsx): Підсумок · Дохід по місяцях · Книга доходів (по днях) · Замовлення · Зарплата · Податки ФОП.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from ..models import EmployeeProfile, PaymentMethod, PayrollAccrual, Role
from . import tax_report, taxes
from .common import Actor, all_settings

METHOD_UA = {PaymentMethod.CASH: "Готівка (каса)", PaymentMethod.CARD: "Безготівка (банк)",
             PaymentMethod.TRANSFER: "Безготівка (переказ на рахунок)"}


def _uah(kop: int) -> Decimal:
    return Decimal(kop) / 100


def _noncash(b: dict) -> int:
    return b.get(PaymentMethod.CARD, 0) + b.get(PaymentMethod.TRANSFER, 0)


def summary(s, actor: Actor, year: int, quarter: int | None = None) -> dict:
    """Підсумок для бухгалтера: реквізити, дохід = каса + банк, по місяцях, працівники, нагадування."""
    actor.require(Role.DIRECTOR)
    st = all_settings(s)
    rep = tax_report.income_report(s, actor, year, quarter)
    t = rep["total"]
    start, end = rep["start"], rep["end"]
    employees = s.scalar(select(func.count(EmployeeProfile.id)).where(
        EmployeeProfile.hired_on.isnot(None), EmployeeProfile.hired_on <= end,
        (EmployeeProfile.fired_on.is_(None)) | (EmployeeProfile.fired_on >= start))) or 0
    has_payroll = bool(s.scalar(select(func.count(PayrollAccrual.id)).where(
        PayrollAccrual.month >= start.replace(day=1), PayrollAccrual.month <= end)))
    return {
        "year": year, "quarter": quarter, "start": start, "end": end,
        "fop": {"name": st.get("fop_name") or "", "tin": st.get("fop_tin") or "", "group": st.get("fop_group") or 1,
                "kved": st.get("fop_kved") or "", "kved_name": st.get("fop_kved_name") or "",
                "address": st.get("fop_address") or "", "sti": st.get("fop_sti_name") or ""},
        "income": {"total": t["total"], "cash": t[PaymentMethod.CASH], "noncash": _noncash(t), "count": t["count"]},
        "months": [{"month": m["month"], "cash": m[PaymentMethod.CASH], "noncash": _noncash(m), "total": m["total"],
                    "count": m["count"]} for m in rep["months"]],
        "quarters": [{"quarter": q["quarter"], "cash": q[PaymentMethod.CASH], "noncash": _noncash(q), "total": q["total"],
                      "count": q["count"]} for q in rep["quarters"]],
        "year_to_date": rep["year_to_date"], "limit": rep["limit"],
        "employees": employees, "has_payroll": has_payroll,
    }


def xlsx(s, actor: Actor, year: int, quarter: int | None = None) -> tuple[bytes, str]:
    """Пакет для бухгалтера (Excel)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    d = summary(s, actor, year, quarter)
    period = f"{quarter} квартал {year}" if quarter else f"{year} рік"
    money_fmt = '#,##0.00'
    bold, head_fill = Font(bold=True), PatternFill("solid", fgColor="E8E4D8")
    wb = Workbook()

    def sheet(title: str, header: list[str], rows: list[list], money_cols: tuple[int, ...] = (), widths=None, total=None):
        ws = wb.create_sheet(title)
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill = bold, head_fill
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for r in rows:
            ws.append(r)
        if total:
            ws.append(total)
            for c in ws[ws.max_row]:
                c.font = bold
        for col in money_cols:
            for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
                row[0].number_format = money_fmt
        for i, w in enumerate(widths or [], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        return ws

    # 1. Підсумок
    ws = wb.active
    ws.title = "Підсумок"
    f, inc = d["fop"], d["income"]
    lines = [
        ("Звітність для бухгалтера — MDS Connect", None),
        ("Період", f"{period} ({d['start']:%d.%m.%Y} — {d['end']:%d.%m.%Y})"),
        ("ФОП", f["name"]), ("РНОКПП", f["tin"]), ("Група єдиного податку", f["group"]),
        ("КВЕД", f"{f['kved']} {f['kved_name']}".strip()), ("Податкова адреса", f["address"]), ("Податкова", f["sti"]),
        (None, None),
        ("ДОХІД ЗА ПЕРІОД (каса + банк)", _uah(inc["total"])),
        ("  у т.ч. готівка (каса, рах. 301)", _uah(inc["cash"])),
        ("  у т.ч. безготівка (банк, рах. 311)", _uah(inc["noncash"])),
        ("Кількість оплат (замовлень)", inc["count"]),
        ("Дохід з початку року", _uah(d["year_to_date"])),
        ("Ліміт доходу групи на рік", _uah(d["limit"]["amount"]) if d["limit"]["amount"] else "не задано"),
        ("Наймані працівники у періоді", d["employees"]),
        (None, None),
        ("Примітки", "Дохід — сума, сплачена клієнтами, на дату оплати; комісія банку дохід не зменшує; "
                     "витрати на єдиному податку дохід не зменшують. Деталі — на інших аркушах."),
    ]
    for k, v in lines:
        ws.append([k, v])
    ws["A1"].font = Font(bold=True, size=13)
    for r in (10,):
        ws[f"A{r}"].font = ws[f"B{r}"].font = bold
    for row in ws.iter_rows(min_row=10, max_row=15, min_col=2, max_col=2):
        if isinstance(row[0].value, Decimal):
            row[0].number_format = money_fmt
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 42, 70
    ws["B18"].alignment = Alignment(wrap_text=True)

    # 2. Дохід по місяцях і кварталах
    months = [[date.fromisoformat(m["month"] + "-01").strftime("%m.%Y"), _uah(m["cash"]), _uah(m["noncash"]),
               _uah(m["total"]), m["count"]] for m in d["months"]]
    months += [[f"{q['quarter']} квартал", _uah(q["cash"]), _uah(q["noncash"]), _uah(q["total"]), q["count"]]
               for q in d["quarters"]]
    sheet("Дохід по місяцях", ["Місяць / квартал", "Готівка (каса)", "Безготівка (банк)", "Разом дохід", "Оплат"], months,
          (2, 3, 4), [20, 18, 18, 18, 10],
          ["Разом за період", _uah(inc["cash"]), _uah(inc["noncash"]), _uah(inc["total"]), inc["count"]])

    # 3. Книга доходів (по днях) і 4. Замовлення
    start, end_ex = tax_report._period(year, quarter)
    orders = tax_report._orders(s, start, end_ex)
    days: dict[date, list[int]] = {}
    for o in orders:
        b = days.setdefault(o.closed_at.date(), [0, 0])
        b[0 if o.payment_method == PaymentMethod.CASH else 1] += o.amount
    sheet("Книга доходів (по днях)", ["Дата", "Готівка (каса)", "Безготівка (банк)", "Разом дохід"],
          [[k.strftime("%d.%m.%Y"), _uah(v[0]), _uah(v[1]), _uah(v[0] + v[1])] for k, v in sorted(days.items())],
          (2, 3, 4), [14, 18, 18, 18], ["Разом", _uah(inc["cash"]), _uah(inc["noncash"]), _uah(inc["total"])])
    sheet("Замовлення", ["Дата", "Час", "№ замовлення", "Послуга", "Майстер", "Спосіб оплати", "Сума"],
          [[o.closed_at.strftime("%d.%m.%Y"), o.closed_at.strftime("%H:%M"), o.id, o.service.name if o.service else "",
            o.master.full_name if o.master else "", METHOD_UA.get(o.payment_method, o.payment_method), _uah(o.amount)]
           for o in orders], (7,), [12, 8, 13, 36, 28, 26, 14], ["Разом", "", "", "", "", "", _uah(inc["total"])])

    # 5. Зарплата найманих (по кварталах періоду)
    qs = [quarter] if quarter else [1, 2, 3, 4]
    pay_rows = []
    for q in qs:
        pq = taxes.payroll_quarter(s, actor, year, q)
        for r in pq["rows"]:
            pay_rows.append([f"{q} кв.", r["full_name"], r["tin"] or "", r["month"][:7], _uah(r["gross"]), _uah(r["pdfo"]),
                             _uah(r["military"]), _uah(r["net"]), _uah(r["esv_base"]), _uah(r["esv_employer"]), _uah(r["paid"])])
    sheet("Зарплата", ["Квартал", "Працівник", "РНОКПП", "Місяць", "Нараховано", "ПДФО", "Військовий збір", "На руки",
                       "База ЄСВ", "ЄСВ роботодавця", "Виплачено (за датою виплати)"],
          pay_rows or [["Нарахувань зарплати за період немає (ФОП без найманих працівників)"]],
          (5, 6, 7, 8, 9, 10, 11), [9, 28, 13, 10, 14, 12, 14, 14, 14, 14, 16])

    # 6. Податки ФОП (якщо ведуться в «Податки і зарплата»)
    tax_rows = [[r["title"], r["period_start"], r["period_end"] or "", _uah(r["accrued"]), _uah(r["paid"]), _uah(r["debt"]),
                 r["due_date"] or ""] for r in taxes.fop_summary(s, actor, year)]
    sheet("Податки ФОП", ["Податок", "Період з", "Період по", "Нараховано", "Сплачено", "Борг", "Строк сплати"],
          tax_rows or [["Нарахування податків ФОП у програмі не ведуться"]], (4, 5, 6), [26, 12, 12, 14, 14, 14, 14])

    buf = io.BytesIO()
    wb.save(buf)
    name = f"dlia-bukhgaltera-{year}" + (f"-Q{quarter}" if quarter else "") + ".xlsx"
    return buf.getvalue(), name
