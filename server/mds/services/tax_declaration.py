"""Річна декларація платника єдиного податку — ФОП 1 групи (F0103407, «у якого податковий (звітний) період рік»)
+ Додаток 1 про ЄСВ (F0134107) у форматі XML ДПС. (F0103309 — квартальна форма для 3 групи, тут НЕ використовується.)

Програма лише ГОТУЄ файли: ФОП завантажує їх в Електронний кабінет, перевіряє, підписує КЕП / Дія.Підписом і
відправляє сам. Ключі й підписи програма не зберігає і нічого в ДПС не надсилає.

Форма: наказ Мінфіну № 578 у редакції наказу № 57 від 31.01.2025 (схеми XSD — tests/fixtures/dps, з реєстру ДПС).
Ставки (мінімальна зарплата на 1 січня, єдиний податок на місяць) вносить директор/бухгалтер — у коді їх немає.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from xml.sax.saxutils import escape

from ..errors import ValidationError
from ..models import Role
from . import tax_report
from .common import Actor, all_settings

QUARTERS = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9), 4: (10, 11, 12)}

# поля профілю ФОП у налаштуваннях: ключ → (назва для повідомлення, обов'язкове)
PROFILE = {
    "fop_tin": ("РНОКПП (податковий номер)", True),
    "fop_name": ("ПІБ ФОП", True),
    "fop_address": ("Податкова адреса", True),
    "fop_sti_name": ("Назва податкової, до якої подається", True),
    "fop_c_reg": ("Код області (C_REG)", True),
    "fop_c_raj": ("Код району (C_RAJ)", True),
    "fop_c_sti": ("Код податкової (C_STI_ORIG)", True),
    "fop_kved": ("КВЕД", True),
    "fop_kved_name": ("Назва КВЕД", True),
    "fop_single_tax_month": ("Єдиний податок на місяць", True),
    "fop_min_wage": ("Мінімальна зарплата на 1 січня року", True),
    "fop_email": ("E-mail", False),
    "fop_phone": ("Телефон", False),
}


def _d2(kop: int) -> str:
    """Копійки → «1234.50» (тип DGdecimal2 схеми ДПС)."""
    sign = "-" if kop < 0 else ""
    kop = abs(kop)
    return f"{sign}{kop // 100}.{kop % 100:02d}"


def _pct(kop: int, bp: int) -> int:
    """Відсоток від суми з округленням до копійки (half-up), без float."""
    return (kop * bp + 5000) // 10000


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d%m%Y")


def validate_profile_value(key: str, value) -> object:
    """Перевірка одного поля профілю (викликається з update_settings)."""
    if key in ("fop_c_reg", "fop_c_raj"):
        if not (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 99):
            raise ValidationError(f"{PROFILE[key][0]}: число від 0 до 99")
    elif key == "fop_c_sti":
        if not (isinstance(value, int) and not isinstance(value, bool) and (value == 0 or 100 <= value <= 9999)):
            raise ValidationError(f"{PROFILE[key][0]}: 3–4 цифри")
    elif key in ("fop_single_tax_month", "fop_min_wage"):
        if not (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**9):
            raise ValidationError(f"{PROFILE[key][0]}: ціле число копійок ≥ 0")
    else:
        if not isinstance(value, str):
            raise ValidationError(f"{PROFILE[key][0]}: очікується текст")
        value = " ".join(value.split())
        if key == "fop_tin" and value and not re.fullmatch(r"[0-9]{10}", value):
            raise ValidationError("РНОКПП — 10 цифр")
        if key == "fop_kved" and value and not re.fullmatch(r"[0-9]{2}\.[0-9]{2}", value):
            raise ValidationError("КВЕД у форматі 96.02")
        if key == "fop_email" and value and not re.fullmatch(r"[^@\s]+@[^@\s]+", value):
            raise ValidationError("Невірний e-mail")
        if key == "fop_phone" and value and not re.fullmatch(r"[0-9 ()\-+.,;]{4,}", value):
            raise ValidationError("Невірний телефон")
    return value


def declaration_data(s, actor: Actor, year: int) -> dict:
    """Цифри декларації за рік (1 група, звітна, увесь рік на єдиному податку) + чого не вистачає в профілі."""
    actor.require(Role.DIRECTOR)
    st = all_settings(s)
    missing = [label for key, (label, req) in PROFILE.items() if req and not st.get(key)]
    income = tax_report.income_report(s, actor, year)["total"]["total"]
    tax_m = int(st.get("fop_single_tax_month") or 0)
    mzp = int(st.get("fop_min_wage") or 0)
    months = list(range(1, 13))
    esv_m = _pct(mzp, 2200)  # ЄСВ 22% мінімальної зарплати (ч. 1 ст. 8 Закону про ЄСВ)
    military_m = _pct(mzp, 1000)  # військовий збір 10% МЗП на 1 січня (рядок 22 декларації)
    return {
        "year": year,
        "group": 1,
        "income": income,
        "single_tax_by_quarter": {q: tax_m * sum(1 for m in ms if m in months) for q, ms in QUARTERS.items()},
        "single_tax_year": tax_m * len(months),
        "military_levy": military_m * len(months),
        "esv_month_base": mzp,
        "esv_month": esv_m,
        "esv_year": esv_m * len(months),
        "months": months,
        "missing": missing,
        "ready": not missing,
        "limit_note": "Перевірте, що дохід не перевищує ліміт 1 групи на цей рік.",
    }


def _head(st: dict, year: int, c_doc_sub: str, today: date, linked: tuple[str, str, int] | None) -> str:
    """Шапка DECLARHEAD (версія форм — 7). linked = (C_DOC_SUB пов'язаного документа, ім'я файлу, TYPE)."""
    lk = ""
    if linked:
        sub, fname, typ = linked
        lk = (f'<LINKED_DOCS><DOC NUM="1" TYPE="{typ}"><C_DOC>F01</C_DOC><C_DOC_SUB>{sub}</C_DOC_SUB>'
              f"<C_DOC_VER>7</C_DOC_VER><C_DOC_TYPE>0</C_DOC_TYPE><C_DOC_CNT>1</C_DOC_CNT>"
              f"<C_DOC_STAN>1</C_DOC_STAN><FILENAME>{fname}</FILENAME></DOC></LINKED_DOCS>")
    return (f"<DECLARHEAD><TIN>{st['fop_tin']}</TIN><C_DOC>F01</C_DOC><C_DOC_SUB>{c_doc_sub}</C_DOC_SUB>"
            f"<C_DOC_VER>7</C_DOC_VER><C_DOC_TYPE>0</C_DOC_TYPE><C_DOC_CNT>1</C_DOC_CNT>"
            f"<C_REG>{st['fop_c_reg']}</C_REG><C_RAJ>{st['fop_c_raj']}</C_RAJ>"
            f"<PERIOD_MONTH>12</PERIOD_MONTH><PERIOD_TYPE>5</PERIOD_TYPE><PERIOD_YEAR>{year}</PERIOD_YEAR>"
            f"<C_STI_ORIG>{st['fop_c_sti']}</C_STI_ORIG><C_DOC_STAN>1</C_DOC_STAN>{lk}"
            f"<D_FILL>{_ddmmyyyy(today)}</D_FILL><SOFTWARE>MDS Connect</SOFTWARE></DECLARHEAD>")


def filename(st: dict, year: int, c_doc_sub: str) -> str:
    """Ім'я файлу за стандартом ДПС (тип DGFilename): РЕГ РАЙ ТІН(10) F01 ПІДТИП ВЕР СТАН ТИП НОМЕР ПЕРІОД МІС РІК ДПІ."""
    return (f"{int(st['fop_c_reg']):02d}{int(st['fop_c_raj']):02d}{st['fop_tin']:0>10}F01{c_doc_sub}07"
            f"1000000001512{year}{int(st['fop_c_sti']):04d}.xml")


def _wrap(xsd: str, head: str, body: str) -> bytes:
    xml = ('<?xml version="1.0" encoding="windows-1251"?>'
           f'<DECLAR xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="{xsd}">'
           f"{head}<DECLARBODY>{body}</DECLARBODY></DECLAR>")
    return xml.encode("cp1251")


def declaration_files(s, actor: Actor, year: int, today: date) -> list[tuple[str, bytes]]:
    """Два XML: річна декларація F0103407 і Додаток 1 F0134107 (ЄСВ). Лише 1 група, тип «Звітна», рік."""
    d = declaration_data(s, actor, year)
    if not d["ready"]:
        raise ValidationError("Заповніть дані ФОП: " + ", ".join(d["missing"]))
    st = all_settings(s)
    e = {k: escape(str(st.get(k) or "")) for k in PROFILE}
    main_name, app_name = filename(st, year, "034"), filename(st, year, "341")

    rows = "".join(f"<R08G{m}>1</R08G{m}>" for m in d["months"])
    body = (
        "<HZ>1</HZ><HY>1</HY>"
        f"<HZY>{year}</HZY><HSTI>{e['fop_sti_name']}</HSTI><HNAME>{e['fop_name']}</HNAME><HLOC>{e['fop_address']}</HLOC>"
        + (f"<HEMAIL>{e['fop_email']}</HEMAIL>" if st.get("fop_email") else "")
        + (f"<HTEL>{e['fop_phone']}</HTEL>" if st.get("fop_phone") else "")
        + f"<HTIN>{e['fop_tin']}</HTIN>"
        "<HNACTL>0</HNACTL>"
        f'<T1RXXXXG1S ROWNUM="1">{e["fop_kved"]}</T1RXXXXG1S><T1RXXXXG2S ROWNUM="1">{e["fop_kved_name"]}</T1RXXXXG2S>'
        + "".join(f"<R02G{q}>{_d2(v)}</R02G{q}>" for q, v in d["single_tax_by_quarter"].items())
        + f"<R001G3>{_d2(d['income'])}</R001G3>"
        f"<R008G3>{_d2(d['income'])}</R008G3>"
        f"<R021G3>{_d2(d['esv_year'])}</R021G3>"
        + rows
        + f"<R022G3>{_d2(d['military_levy'])}</R022G3>"
        "<HD1>1</HD1>"
        f"<HFILL>{_ddmmyyyy(today)}</HFILL><HBOS>{e['fop_name']}</HBOS>"
    )
    main = _wrap("F0103407.xsd", _head(st, year, "034", today, ("341", app_name, 1)), body)

    m_rows = "".join(
        f"<R09{m}G2>{_d2(d['esv_month_base'])}</R09{m}G2><R09{m}G3>22.00</R09{m}G3><R09{m}G4>{_d2(d['esv_month'])}</R09{m}G4>"
        for m in d["months"]
    )
    app_body = (
        f"<HZ>1</HZ><HTIN>{e['fop_tin']}</HTIN><HNAME>{e['fop_name']}</HNAME><HY>1</HY><HZY>{year}</HZY>"
        f"<HKVED>{e['fop_kved']}</HKVED>"
        f"<R08G1D>{_ddmmyyyy(date(year, 1, 1))}</R08G1D><R08G2D>{_ddmmyyyy(date(year, 12, 31))}</R08G2D>"
        "<R081G1>6</R081G1>"  # код категорії 6 — ФОП на спрощеній системі
        + m_rows
        + f"<R09G2>{_d2(d['esv_month_base'] * len(d['months']))}</R09G2><R09G4>{_d2(d['esv_year'])}</R09G4>"
        f"<HBOS>{e['fop_name']}</HBOS>"
    )
    app = _wrap("F0134107.xsd", _head(st, year, "341", today, ("034", main_name, 2)), app_body)
    return [(main_name, main), (app_name, app)]


def declaration_zip(s, actor: Actor, year: int, today: date) -> tuple[bytes, str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in declaration_files(s, actor, year, today):
            z.writestr(name, data)
    return buf.getvalue(), f"deklaratsiia-fop-{year}.zip"
