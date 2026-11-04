"""Податки ФОП і офіційна зарплата. УВАГА: усі ставки тут — ТЕСТОВІ значення, не чинні законодавчі."""

from datetime import date, datetime

import pytest
from sqlalchemy import select

from mds import clock
from mds.errors import Conflict, Forbidden, ValidationError
from mds.models import Notification, Order, PayrollAccrual, TaxAccrual, User
from mds.services import bookings, orders, taxes

# тестові значення (умовні)
MIN_WAGE = 800000  # 8 000 ₴


def _rates(s, d, *, frm=date(2026, 1, 1)):
    taxes.add_rate(s, d, key="min_wage", valid_from=frm, amount=MIN_WAGE)
    taxes.add_rate(s, d, key="single_tax", valid_from=frm, rate_bp=2000, base="min_wage", period="month",
                   due_day=20, due_from="start", due_offset_months=0)
    taxes.add_rate(s, d, key="military_fop", valid_from=frm, rate_bp=1000, base="min_wage", period="month",
                   due_day=20, due_from="start")
    taxes.add_rate(s, d, key="esv_fop", valid_from=frm, rate_bp=2200, base="min_wage", period="quarter",
                   due_day=20, due_from="end", due_offset_months=1)
    taxes.add_rate(s, d, key="pdfo", valid_from=frm, rate_bp=1800, base="salary")
    taxes.add_rate(s, d, key="military_payroll", valid_from=frm, rate_bp=500, base="salary")
    taxes.add_rate(s, d, key="esv_employer", valid_from=frm, rate_bp=2200, base="salary_min_wage")


def _hire(s, d, uid, **kw):
    data = {"tin": "1234567890", "position": "Перукар", "hired_on": date(2026, 1, 1), "main_job": True} | kw
    return taxes.set_employee(s, d, uid, data)


def test_rates_with_dates_and_history(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        _rates(s, d)
        with pytest.raises(Forbidden):
            taxes.add_rate(s, seed["actors"]["admin"], key="pdfo", valid_from=date(2027, 1, 1), rate_bp=1900)
        new = taxes.add_rate(s, d, key="min_wage", valid_from=date(2027, 1, 1), amount=900000)
        assert taxes.rate_for(s, "min_wage", date(2026, 12, 31)).amount == MIN_WAGE
        assert taxes.rate_for(s, "min_wage", date(2027, 1, 1)).id == new.id
        old = [r for r in taxes.list_rates(s, d) if r["key"] == "min_wage" and r["amount"] == MIN_WAGE][0]
        assert old["valid_to"] == "2026-12-31"  # стара ставка завершилась, значення не змінилось
        with pytest.raises(Conflict):
            taxes.add_rate(s, d, key="min_wage", valid_from=date(2026, 6, 1), amount=1)  # раніше за чинну
        with pytest.raises(ValidationError):
            taxes.add_rate(s, d, key="unknown", valid_from=date(2026, 1, 1), rate_bp=1)


def test_fop_accruals_due_dates_payments_and_corrections(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        _rates(s, d)
        st = taxes.accrue_fop(s, d, "single_tax", date(2026, 11, 15))
        assert (st.amount, st.due_date, st.period_start) == (160000, date(2026, 11, 20), date(2026, 11, 1))  # 20% × 8000
        mf = taxes.accrue_fop(s, d, "military_fop", date(2026, 11, 1))
        assert mf.amount == 80000
        esv = taxes.accrue_fop(s, d, "esv_fop", date(2026, 11, 5))
        # квартал IV: 22% × 8000 × 3 = 5 280 ₴, строк — 20 січня
        assert (esv.amount, esv.period_start, esv.period_end, esv.due_date) == (528000, date(2026, 10, 1), date(2026, 12, 31), date(2027, 1, 20))
        with pytest.raises(Conflict):
            taxes.accrue_fop(s, d, "single_tax", date(2026, 11, 2))  # двічі не нараховується
        taxes.pay_tax(s, d, key="single_tax", period_start=date(2026, 11, 1), amount=100000, paid_on=date(2026, 11, 10))
        clock.set_now(datetime(2026, 11, 25, 9))
        rows = {r["key"]: r for r in taxes.fop_summary(s, d, 2026)}
        assert rows["single_tax"]["debt"] == 60000 and rows["single_tax"]["overdue"]
        assert rows["esv_fop"]["debt"] == 528000 and not rows["esv_fop"]["overdue"]
        # виправлення: сторно + нове (вказана сума)
        new = taxes.correct_fop(s, d, st.id, "Уточнення бухгалтера", amount=150000)
        rows = {r["key"]: r for r in taxes.fop_summary(s, d, 2026)}
        assert rows["single_tax"]["accrued"] == 150000 and rows["single_tax"]["debt"] == 50000
        assert s.get(TaxAccrual, st.id).reversed and new.dedupe_key == "fop:single_tax:2026-11-01"
        assert len(list(s.scalars(select(TaxAccrual).where(TaxAccrual.key == "single_tax")))) == 3
        with pytest.raises(Conflict):
            taxes.correct_fop(s, d, st.id, "ще раз")


def test_income_based_tax_and_reminders(db, seed):
    d = seed["actors"]["director"]
    s_ = db.session
    with s_() as s:
        taxes.add_rate(s, d, key="single_tax", valid_from=date(2026, 1, 1), rate_bp=500, base="income", period="quarter",
                       due_day=10, due_from="end", due_offset_months=2)
        s.get(User, seed["director"]).telegram_chat_id = 9001
    clock.set_now(datetime(2026, 11, 2, 9))
    with s_() as s:
        a = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"], starts_at=datetime(2026, 11, 2, 10),
                                        client_name="К", client_phone="0501112233")
        aid = a.id
    clock.set_now(datetime(2026, 11, 2, 11))
    with s_() as s:
        orders.close_order(s, seed["actors"]["m1"], aid, amount=1000000, payment_method="cash")
    with s_() as s:
        t = taxes.accrue_fop(s, d, "single_tax", date(2026, 11, 2))
        assert (t.base_amount, t.amount, t.due_date) == (1000000, 50000, date(2027, 2, 10))  # 5% від 10 000 ₴
    clock.set_now(datetime(2027, 2, 6, 9))
    with s_() as s:
        assert taxes.tax_reminders(s, days_before=5) == 1
        assert taxes.tax_reminders(s, days_before=5) == 0  # один раз
        assert any("до сплати 500.00" in n.text for n in s.scalars(select(Notification).where(Notification.chat_id == 9001)))


def test_payroll_calc_payments_debt_corrections(db, seed):
    d, m1 = seed["actors"]["director"], seed["m1"]
    with db.session() as s:
        with pytest.raises(ValidationError):
            taxes.calc_payroll(s, 1000000, date(2026, 11, 1))  # ставок немає
        _rates(s, d)
        c = taxes.calc_payroll(s, 1000000, date(2026, 11, 1))
        # 10 000 ₴: ПДФО 18% = 1 800; ВЗ 5% = 500; на руки 7 700; ЄСВ роботодавця 22% = 2 200
        assert (c["pdfo"], c["military"], c["net"], c["esv_employer"]) == (180000, 50000, 770000, 220000)
        part = taxes.calc_payroll(s, 400000, date(2026, 11, 1))
        assert part["esv_base"] == MIN_WAGE and part["esv_employer"] == 176000  # ЄСВ не менше ніж з мінімальної
        with pytest.raises(ValidationError):  # без кадрових даних не можна
            taxes.accrue_salary(s, d, user_id=m1, month=date(2026, 11, 10), gross=1000000)
        _hire(s, d, m1)
        p = taxes.accrue_salary(s, d, user_id=m1, month=date(2026, 11, 10), gross=1000000)
        with pytest.raises(Conflict):
            taxes.accrue_salary(s, d, user_id=m1, month=date(2026, 11, 1), gross=1)
        with pytest.raises(ValidationError):
            taxes.accrue_salary(s, d, user_id=seed["director"], month=date(2026, 11, 1), gross=1)
        taxes.pay_salary(s, d, user_id=m1, month=date(2026, 11, 1), amount=300000, paid_on=date(2026, 11, 20))
        assert taxes.salary_debt(s, m1, date(2026, 11, 1)) == 470000
        with pytest.raises(Conflict):
            taxes.pay_salary(s, d, user_id=m1, month=date(2026, 11, 1), amount=470001, paid_on=date(2026, 11, 30))
        taxes.pay_salary(s, d, user_id=m1, month=date(2026, 11, 1), amount=470000, paid_on=date(2026, 11, 30))
        assert taxes.salary_debt(s, m1, date(2026, 11, 1)) == 0
        # виправлення: сторно + нове; переплата стає видимою як від'ємний борг
        taxes.correct_salary(s, d, p.id, gross=900000, reason="Помилка в табелі")
        summ = taxes.payroll_summary(s, d, date(2026, 11, 1))
        row = summ["rows"][0]
        assert row["gross"] == 900000 and row["net"] == 900000 - 162000 - 45000 and row["debt"] == row["net"] - 770000
        assert summ["totals"]["to_budget"] == 162000 + 45000 + 198000
        assert len(taxes.payroll_history(s, d, m1)) == 3
        # управлінський поділ 50/50 не зачеплено
        assert orders.master_balance(s, m1) == {"accrued": 0, "paid": 0, "charged": 0, "balance": 0}
        assert s.scalar(select(PayrollAccrual).where(PayrollAccrual.reverses_id == p.id)).gross == -1000000
        assert not s.scalars(select(Order)).first()


def test_payroll_rate_change_by_date(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        _rates(s, d)
        taxes.add_rate(s, d, key="military_payroll", valid_from=date(2027, 1, 1), rate_bp=700)
        assert taxes.calc_payroll(s, 1000000, date(2026, 12, 1))["military"] == 50000
        assert taxes.calc_payroll(s, 1000000, date(2027, 1, 1))["military"] == 70000
        _hire(s, d, seed["m2"])
        dec = taxes.accrue_salary(s, d, user_id=seed["m2"], month=date(2026, 12, 1), gross=1000000)
        assert dec.rates["military_payroll"] == 500  # знімок ставок у нарахуванні


def test_employee_profile_and_esv_min_base_rules(db, seed):
    d, m1, m2 = seed["actors"]["director"], seed["m1"], seed["m2"]
    with db.session() as s:
        _rates(s, d)
        with pytest.raises(ValidationError):
            taxes.set_employee(s, d, m1, {"tin": "123", "hired_on": date(2026, 1, 1)})
        with pytest.raises(ValidationError):
            taxes.set_employee(s, d, m1, {"tin": "1234567890", "hired_on": date(2026, 5, 1), "fired_on": date(2026, 4, 1)})
        _hire(s, d, m1)  # основне місце, повний місяць → база не менше мінімальної
        assert taxes.calc_payroll(s, 400000, date(2026, 11, 1), m1)["esv_base"] == MIN_WAGE
        _hire(s, d, m2, main_job=False)  # сумісництво → база = фактичне нарахування
        assert taxes.calc_payroll(s, 400000, date(2026, 11, 1), m2)["esv_base"] == 400000
        _hire(s, d, m2, hired_on=date(2026, 11, 15))  # прийнята посеред місяця → не повний місяць
        c = taxes.calc_payroll(s, 400000, date(2026, 11, 1), m2)
        assert c["esv_base"] == 400000 and c["min_base_applied"] is False
        with pytest.raises(ValidationError):  # до прийому нарахувати не можна
            taxes.accrue_salary(s, d, user_id=m2, month=date(2026, 10, 1), gross=100000)
        emps = {e["user_id"]: e for e in taxes.list_employees(s, d)}
        assert emps[m1]["tin"] == "1234567890" and emps[m2]["hired_on"] == "2026-11-15" and emps[m2]["is_employee"]
        with pytest.raises(Forbidden):
            taxes.set_employee(s, seed["actors"]["admin"], m1, {"tin": "1234567890"})


def test_quarter_report_and_csv(db, seed):
    d, m1, m2 = seed["actors"]["director"], seed["m1"], seed["m2"]
    with db.session() as s:
        _rates(s, d)
        _hire(s, d, m1)
        _hire(s, d, m2, tin="0987654321", main_job=False)
        for mon in (10, 11, 12):
            taxes.accrue_salary(s, d, user_id=m1, month=date(2026, mon, 1), gross=1000000)
        taxes.accrue_salary(s, d, user_id=m2, month=date(2026, 11, 1), gross=400000)
        taxes.pay_salary(s, d, user_id=m1, month=date(2026, 10, 1), amount=770000, paid_on=date(2026, 11, 5))
        taxes.pay_salary(s, d, user_id=m1, month=date(2026, 12, 1), amount=770000, paid_on=date(2027, 1, 5))  # інший квартал
        q = taxes.payroll_quarter(s, d, 2026, 4)
        body, name = taxes.payroll_quarter_csv(s, d, 2026, 4)
    t = q["totals"]
    assert q["employees"] == 2 and len(q["rows"]) == 4  # m1: 3 місяці; m2: листопад
    assert (t["gross"], t["pdfo"], t["military"], t["esv_base"], t["paid"]) == (3400000, 612000, 170000, 3400000, 770000)
    assert t["esv_employer"] == 3 * 220000 + 88000 and t["to_budget"] == 612000 + 170000 + 748000
    assert name == "zarplata-2026-Q4.csv" and body.startswith("\ufeff")
    assert "0987654321;2026-11;4000,00;720,00;200,00;3080,00;4000,00;880,00;0,00" in body


def test_payroll_api_and_permissions(client_api):
    c, ids, d = client_api
    tok = lambda u, p: {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]}  # noqa: E731
    m, a = tok("olena", "Master123"), tok("admin", "Admin1234")
    director_only = [
        ("get", "/api/taxes/rates", None), ("post", "/api/taxes/rates", {"key": "pdfo", "valid_from": "2026-01-01", "rate_bp": 1800}),
        ("get", "/api/taxes/fop", None), ("post", "/api/taxes/fop/accrue", {"key": "single_tax", "on": "2026-11-01"}),
        ("post", "/api/taxes/payments", {"key": "single_tax", "period_start": "2026-11-01", "amount": 1, "paid_on": "2026-11-01"}),
        ("get", "/api/payroll", None), ("post", "/api/payroll", {"user_id": ids["m1"], "month": "2026-11-01", "gross": 1}),
        ("post", "/api/payroll/payments", {"user_id": ids["m1"], "month": "2026-11-01", "amount": 1, "paid_on": "2026-11-01"}),
        ("get", f"/api/payroll/history/{ids['m1']}", None), ("get", "/api/payroll/calc?gross=1&month=2026-11-01", None),
        ("get", "/api/employees", None), ("put", f"/api/employees/{ids['m1']}", {"tin": "1234567890"}),
        ("get", "/api/payroll/quarter?year=2026&quarter=4", None), ("get", "/api/payroll/quarter.csv?year=2026&quarter=4", None),
    ]
    bad = []
    for who, hdr in (("master", m), ("admin", a)):
        for method, url, body in director_only:
            r = getattr(c, method)(url, headers=hdr, **({"json": body} if body is not None else {}))
            if r.status_code != 403:
                bad.append((who, method, url, r.status_code))
    assert not bad, bad
    for key, kw in (("min_wage", {"amount": 800000}), ("pdfo", {"rate_bp": 1800, "base": "salary"}),
                    ("military_payroll", {"rate_bp": 500, "base": "salary"}), ("esv_employer", {"rate_bp": 2200, "base": "salary_min_wage"})):
        assert c.post("/api/taxes/rates", headers=d, json={"key": key, "valid_from": "2026-01-01", **kw}).status_code == 201
    assert c.put(f"/api/employees/{ids['m1']}", headers=d, json={"tin": "1234567890", "hired_on": "2026-01-01"}).status_code == 200
    assert c.post("/api/payroll", headers=d, json={"user_id": ids["m1"], "month": "2026-11-01", "gross": 1000000}).status_code == 201
    r = c.get("/api/payroll/quarter.csv?year=2026&quarter=4", headers=d)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
