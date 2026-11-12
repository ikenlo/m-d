from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from ..errors import ValidationError
from ..models import Role
from ..services.common import Actor

FONTS = Path(__file__).resolve().parent / "fonts"
FORMATS = {"csv": "text/csv; charset=utf-8", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
           "pdf": "application/pdf"}
STATUS_UA = {"completed": "оплачено", "unpaid": "не оплачено", "cancelled": "скасовано", "refunded": "повернено"}
PAY_UA = {"cash": "готівка", "card": "картка", "transfer": "переказ"}


def uah(kop: int | None) -> str:
    kop = int(kop or 0)
    sign = "-" if kop < 0 else ""
    kop = abs(kop)
    return f"{sign}{kop // 100},{kop % 100:02d}"


def _finance(s: Session, actor: Actor, a: date, b: date) -> tuple[str, list[str], list[list]]:
    from ..services.analytics import dashboard

    d = dashboard(s, actor, a, b)
    e = d["expenses"]
    rows = [
        ["Реальна виручка (послуги + товари)", uah(d["real_revenue"])],
        ["  з них готівка", uah(d["by_payment"].get("cash", 0))],
        ["  з них картка / переказ", uah(d["by_payment"].get("card", 0) + d["by_payment"].get("transfer", 0))],
        ["Знижки", uah(d["discounts"])],
        ["Частка майстрів (нараховано)", uah(d["masters_share"])],
        ["Частка салону (до матеріалів)", uah(d["salon_share"])],
        ["Матеріали", uah(d["materials"])],
        ["Валовий прибуток", uah(d["gross_profit"])],
        ["Оренда", uah(e["rent"])], ["Комунальні", uah(e["utilities"])], ["Реклама", uah(e["advertising"])],
        ["Податки", uah(e["taxes"])], ["Генератор", uah(e["generator"])], ["Інше", uah(e["other"])],
        ["Чистий прибуток", uah(d["net_profit"])],
        ["Рентабельність, %", "" if d["profitability_bp"] is None else f"{d['profitability_bp'] / 100:.1f}".replace(".", ",")],
        ["Виплачено майстрам", uah(d["payouts"])], ["Борг майстрам", uah(d["owed_to_masters"])],
        ["Бонус за товари (накопичено)", uah(d["product_bonus_accrued"])],
        ["Неоплачені замовлення", f"{d['unpaid_orders']['count']} шт. / {uah(d['unpaid_orders']['amount'])}"],
    ]
    return f"Фінанси {a:%d.%m.%Y}–{b:%d.%m.%Y}", ["Показник", "Сума, ₴"], rows


def _orders(s: Session, actor: Actor, a: date, b: date) -> tuple[str, list[str], list[list]]:
    from ..services.orders import list_orders

    rows = []
    for o in list_orders(s, actor, date_from=a, date_to=b):
        rows.append([f"{o.closed_at:%d.%m.%Y %H:%M}", o.id, o.master.full_name, o.service.name if o.service else "Товари",
                     STATUS_UA.get(o.status, o.status), PAY_UA.get(o.payment_method, o.payment_method), uah(o.price_snapshot),
                     uah(o.amount), uah(o.materials_cost), uah(o.master_earning), uah(o.salon_earning)])
    head = ["Дата", "№", "Майстер", "Послуга", "Статус", "Оплата", "Ціна", "Сплачено", "Матеріали", "Майстру", "Салону"]
    return f"Замовлення {a:%d.%m.%Y}–{b:%d.%m.%Y}", head, rows


REPORTS = {"finance": _finance, "orders": _orders}


def build(s: Session, actor: Actor, name: str, fmt: str, a: date, b: date) -> tuple[bytes, str, str]:
    actor.require(Role.DIRECTOR)
    if name not in REPORTS:
        raise ValidationError("Невідомий звіт")
    if fmt not in FORMATS:
        raise ValidationError("Формат: csv, xlsx або pdf")
    title, head, rows = REPORTS[name](s, actor, a, b)
    data = {"csv": _csv, "xlsx": _xlsx, "pdf": _pdf}[fmt](title, head, rows)
    return data, FORMATS[fmt], f"mds-{name}-{a:%Y%m%d}-{b:%Y%m%d}.{fmt}"


def _csv(title: str, head: list, rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([title])
    w.writerow(head)
    w.writerows(rows)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _xlsx(title: str, head: list, rows: list) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Звіт"
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(head)
    for c in ws[2]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(r)
    for col in ws.columns:
        width = max(len(str(c.value or "")) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 48)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf(title: str, head: list, rows: list) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", str(FONTS / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONTS / "DejaVuSans-Bold.ttf")))
    buf = io.BytesIO()
    wide = len(head) > 4
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4) if wide else A4, title=title, author="MDS Connect",
                            leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28)
    table = Table([head] + [[str(x) for x in r] for r in rows], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "DejaVu", 8 if wide else 10),
        ("FONT", (0, 0), (-1, 0), "DejaVu-Bold", 8 if wide else 10),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#efe9dc")),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    doc.build([Paragraph(title, ParagraphStyle("t", fontName="DejaVu-Bold", fontSize=14, leading=18)), Spacer(1, 10), table])
    return buf.getvalue()
