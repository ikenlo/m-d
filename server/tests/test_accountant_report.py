"""Звіт для бухгалтера (етап 1): 7 аркушів, довільний період, повернення і статус оплати, майстри, права."""

import io
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from mds import clock
from mds.errors import Forbidden, ValidationError
from mds.services import accountant_report, bookings, expenses, orders, taxes
from mds.services.common import update_settings


def _visit(db, seed, at, amount, pay, phone, master="m1"):
    clock.set_now(at.replace(hour=7))
    with db.session() as s:
        aid = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed[master], service_id=seed["haircut"],
                                          starts_at=at, client_name="К", client_phone=phone).id
    clock.set_now(at.replace(hour=at.hour + 1))
    with db.session() as s:
        oid = orders.close_order(s, seed["actors"][master], aid, amount=amount, payment_method=pay).id
    clock.set_now(None)
    return oid


@pytest.fixture
def data(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        update_settings(s, d, {"fop_name": "Тестова Маргарита", "fop_tin": "1234567890", "fop_group": 1,
                               "fop_income_limit": 144404900})
    ids = {
        "a": _visit(db, seed, datetime(2026, 1, 10, 10), 50000, "cash", "0670000001"),
        "b": _visit(db, seed, datetime(2026, 1, 10, 12), 65050, "card", "0670000002"),
        "c": _visit(db, seed, datetime(2026, 5, 3, 10), 120000, "card", "0670000003", master="m2"),
        "old": _visit(db, seed, datetime(2025, 12, 31, 10), 99900, "cash", "0670000004"),
    }
    clock.set_now(datetime(2026, 1, 12, 15))
    with db.session() as s:  # часткове повернення 150 ₴ клієнтці
        orders.correct_order(s, d, ids["b"], {"amount": 50050}, "Повернення 150 ₴ — скарга")
        expenses.create_expense(s, d, category="rent", amount=900000, spent_on=date(2026, 1, 1), description="Оренда")
    clock.set_now(None)
    return ids


def test_summary_custom_period_refunds_masters(db, seed, data):
    d = seed["actors"]["director"]
    with db.session() as s:
        r = accountant_report.summary(s, d, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
        y = accountant_report.summary(s, d, 2026)
        m = accountant_report.masters_report(s, d, date(2026, 1, 1), date(2026, 12, 31))
        with pytest.raises(ValidationError):
            accountant_report.summary(s, d, date_from=date(2026, 2, 1), date_to=date(2026, 1, 1))
    assert r["income"] == {"total": 100050, "cash": 50000, "noncash": 50050, "count": 2, "refunds": 15000}
    assert len(r["months"]) == 1 and r["management"]["expenses"] == 900000
    assert y["income"]["total"] == 220050 and len(y["months"]) == 12 and y["year_to_date"] == 220050
    by = {x["name"]: x for x in m}
    # Олена 50%: 500 + 500,50 (після повернення) → 500,25 ₴; Ірина 40% від 1 200 → 480 ₴
    assert by["Олена"]["accrued"] == 50025 and by["Ірина"]["accrued"] == 48000
    assert by["Олена"]["paid"] == 0 and by["Олена"]["balance_end"] == 50025 + 49950  # + замовлення 31.12.2025


def test_xlsx_seven_sheets(db, seed, data):
    d = seed["actors"]["director"]
    with db.session() as s:
        for key, kw in (("min_wage", {"amount": 864700}), ("pdfo", {"rate_bp": 1800, "base": "salary"}),
                        ("military_payroll", {"rate_bp": 500, "base": "salary"}),
                        ("esv_employer", {"rate_bp": 2200, "base": "salary_min_wage"})):
            taxes.add_rate(s, d, key=key, valid_from=date(2026, 1, 1), **kw)
        taxes.set_employee(s, d, seed["m2"], {"tin": "0987654321", "hired_on": date(2026, 4, 1)})
        taxes.accrue_salary(s, d, user_id=seed["m2"], month=date(2026, 5, 1), gross=1000000)
        body, name = accountant_report.xlsx(s, d, date_from=date(2026, 1, 1), date_to=date(2026, 6, 30))
    assert name == "zvit-dlia-bukhgaltera-20260101-20260630.xlsx"
    wb = load_workbook(io.BytesIO(body))
    assert wb.sheetnames == ["1. Доходи ФОП", "2. Готівкові надходження", "3. Банківські надходження",
                             "4. Фіскальні чеки та повернення", "5. Витрати салону",
                             "6. Майстри, нараховано, виплати", "7. Підсумок за період"]
    reg = list(wb["1. Доходи ФОП"].iter_rows(values_only=True))
    b = next(r for r in reg if r[2] == data["b"])
    assert (float(b[6]), float(b[7]), float(b[8]), b[9]) == (650.5, 150.0, 500.5, "частково повернено")
    tot = next(r for r in reg if r[0] == "Разом")
    assert float(tot[8]) == 2200.5 and float(tot[7]) == 150.0
    cash = list(wb["2. Готівкові надходження"].iter_rows(values_only=True))
    assert cash[1] == ("10.01.2026", 1, 500) or float(cash[1][2]) == 500.0
    bank = [r for r in wb["3. Банківські надходження"].iter_rows(values_only=True) if r[0] == "Разом"][0]
    assert float(bank[2]) == 1700.5
    ref = list(wb["4. Фіскальні чеки та повернення"].iter_rows(values_only=True))
    assert ref[1][1] == data["b"] and float(ref[1][6]) == -150.0 and "скарга" in ref[1][7]
    exp = list(wb["5. Витрати салону"].iter_rows(values_only=True))
    assert exp[1][1] == "Оренда" and float(exp[1][2]) == 9000.0
    m6 = [r for r in wb["6. Майстри, нараховано, виплати"].iter_rows(values_only=True) if r and r[0]]
    assert any(r[0] == "Офіційна зарплата найманих працівників (для бухгалтера)" for r in m6)
    assert any(r[2] == "0987654321" for r in m6)
    summ = {r[0]: r[1] for r in wb["7. Підсумок за період"].iter_rows(values_only=True) if r[0]}
    assert float(summ["ДОХІД ФОП ЗА ПЕРІОД (каса + банк)"]) == 2200.5 and summ["РНОКПП"] == "1234567890"


def test_permissions_api(db, seed, client_api):
    with db.session() as s, pytest.raises(Forbidden):
        accountant_report.summary(s, seed["actors"]["admin"], 2026)
    c, ids, hd = client_api
    r = c.get("/api/reports/accountant.xlsx", params={"date_from": "2026-01-01", "date_to": "2026-03-31"}, headers=hd)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert c.get("/api/reports/masters", params={"date_from": "2026-01-01", "date_to": "2026-03-31"}, headers=hd).status_code == 200
    tok = c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/reports/accountant", params={"year": 2026}, headers=h).status_code == 403
    assert c.get("/api/reports/masters", params={"date_from": "2026-01-01", "date_to": "2026-03-31"}, headers=h).status_code == 403
