"""Звітність для бухгалтера: дохід = каса + банк, по місяцях/днях/замовленнях, зарплата, Excel, права."""

import io
from datetime import date, datetime
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from mds import clock
from mds.errors import Forbidden, ValidationError
from mds.services import accountant_report, bookings, orders, taxes
from mds.services.common import update_settings


def _visit(db, seed, at, amount, pay, phone):
    clock.set_now(at.replace(hour=7))
    with db.session() as s:
        aid = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=seed["haircut"],
                                          starts_at=at, client_name="К", client_phone=phone).id
    clock.set_now(at.replace(hour=at.hour + 1))
    with db.session() as s:
        orders.close_order(s, seed["actors"]["m1"], aid, amount=amount, payment_method=pay)
    clock.set_now(None)


@pytest.fixture
def data(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        update_settings(s, d, {"fop_name": "Тестова Маргарита", "fop_tin": "1234567890", "fop_group": 1,
                               "fop_income_limit": 144404900})
    _visit(db, seed, datetime(2026, 1, 10, 10), 50000, "cash", "0670000001")
    _visit(db, seed, datetime(2026, 1, 10, 12), 65050, "card", "0670000002")
    _visit(db, seed, datetime(2026, 5, 3, 10), 120000, "card", "0670000003")
    _visit(db, seed, datetime(2025, 12, 31, 10), 99900, "cash", "0670000004")  # інший рік


def test_summary_cash_plus_bank(db, seed, data):
    with db.session() as s:
        r = accountant_report.summary(s, seed["actors"]["director"], 2026)
        q1 = accountant_report.summary(s, seed["actors"]["director"], 2026, 1)
    assert r["income"] == {"total": 235050, "cash": 50000, "noncash": 185050, "count": 3}
    assert r["months"][0] == {"month": "2026-01", "cash": 50000, "noncash": 65050, "total": 115050, "count": 2}
    assert [q["total"] for q in r["quarters"]] == [115050, 120000, 0, 0]
    assert r["fop"]["tin"] == "1234567890" and r["fop"]["group"] == 1 and r["employees"] == 0
    assert q1["income"]["total"] == 115050 and q1["year_to_date"] == 115050


def test_xlsx_package(db, seed, data):
    d = seed["actors"]["director"]
    with db.session() as s:
        for key, kw in (("min_wage", {"amount": 864700}), ("pdfo", {"rate_bp": 1800, "base": "salary"}),
                        ("military_payroll", {"rate_bp": 500, "base": "salary"}),
                        ("esv_employer", {"rate_bp": 2200, "base": "salary_min_wage"})):
            taxes.add_rate(s, d, key=key, valid_from=date(2026, 1, 1), **kw)
        taxes.set_employee(s, d, seed["m2"], {"tin": "0987654321", "hired_on": date(2026, 4, 1)})
        taxes.accrue_salary(s, d, user_id=seed["m2"], month=date(2026, 5, 1), gross=1000000)
        body, name = accountant_report.xlsx(s, d, 2026)
    assert name == "dlia-bukhgaltera-2026.xlsx"
    wb = load_workbook(io.BytesIO(body))
    assert wb.sheetnames == ["Підсумок", "Дохід по місяцях", "Книга доходів (по днях)", "Замовлення", "Зарплата", "Податки ФОП"]
    summ = {r[0]: r[1] for r in wb["Підсумок"].iter_rows(values_only=True) if r[0]}
    assert summ["ДОХІД ЗА ПЕРІОД (каса + банк)"] == Decimal("2350.50") or float(summ["ДОХІД ЗА ПЕРІОД (каса + банк)"]) == 2350.5
    assert summ["РНОКПП"] == "1234567890" and summ["Наймані працівники у періоді"] == 1
    book = list(wb["Книга доходів (по днях)"].iter_rows(values_only=True))
    assert book[1][0] == "10.01.2026" and float(book[1][1]) == 500.0 and float(book[1][2]) == 650.5
    assert book[-1][0] == "Разом" and float(book[-1][3]) == 2350.5
    assert len(list(wb["Замовлення"].iter_rows(values_only=True))) == 1 + 3 + 1
    pay = list(wb["Зарплата"].iter_rows(values_only=True))
    assert pay[1][1] and pay[1][2] == "0987654321" and float(pay[1][4]) == 10000.0 and float(pay[1][5]) == 1800.0


def test_validation_and_permissions(db, seed, client_api):
    with db.session() as s:
        with pytest.raises(ValidationError):
            update_settings(s, seed["actors"]["director"], {"fop_group": 4})
        with pytest.raises(Forbidden):
            accountant_report.summary(s, seed["actors"]["admin"], 2026)
    c, ids, hd = client_api
    r = c.get("/api/reports/accountant.xlsx", params={"year": 2026, "quarter": 2}, headers=hd)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    tok = c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]
    assert c.get("/api/reports/accountant", params={"year": 2026}, headers={"Authorization": f"Bearer {tok}"}).status_code == 403
