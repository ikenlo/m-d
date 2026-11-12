from datetime import datetime

import pytest

from mds import clock
from mds.errors import Forbidden, ValidationError
from mds.services import bookings, orders, tax_report
from mds.services.common import update_settings


def _visit(db, seed, at, amount, pay="cash", master="m1", phone="0670000001"):
    clock.set_now(at.replace(hour=7))
    with db.session() as s:
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed[master], service_id=seed["haircut"],
                                        starts_at=at, client_name="К", client_phone=phone)
        aid = a.id
    clock.set_now(at.replace(hour=at.hour + 1))
    with db.session() as s:
        oid = orders.close_order(s, seed["actors"][master], aid, amount=amount, payment_method=pay).id
    clock.set_now(None)
    return oid


@pytest.fixture
def year_data(db, seed):
    _visit(db, seed, datetime(2025, 12, 30, 10), 99900)
    a = _visit(db, seed, datetime(2026, 1, 15, 10), 50000, "cash")
    _visit(db, seed, datetime(2026, 1, 16, 12), 60000, "card", master="m2", phone="0670000002")
    _visit(db, seed, datetime(2026, 3, 31, 18), 70000, "card", phone="0670000003")
    _visit(db, seed, datetime(2026, 4, 1, 9), 45000, "cash", phone="0670000004")
    _visit(db, seed, datetime(2027, 1, 2, 10), 88800)
    return {"first": a}


def test_year_report_exact_sums(db, seed, year_data):
    with db.session() as s:
        r = tax_report.income_report(s, seed["actors"]["director"], 2026)
    assert len(r["months"]) == 12
    jan, mar, apr = r["months"][0], r["months"][2], r["months"][3]
    assert (jan["total"], jan["cash"], jan["card"], jan["count"]) == (110000, 50000, 60000, 2)
    assert (mar["total"], mar["card"]) == (70000, 70000) and apr["total"] == 45000
    assert [q["total"] for q in r["quarters"]] == [180000, 45000, 0, 0]
    assert (r["quarters"][0]["cash"], r["quarters"][0]["card"]) == (50000, 130000)
    assert (r["total"]["total"], r["total"]["count"]) == (225000, 4)
    assert str(r["start"]) == "2026-01-01" and str(r["end"]) == "2026-12-31"
    assert r["limit"]["used_bp"] is None


def test_quarter_report_year_to_date_and_limit(db, seed, year_data):
    d = seed["actors"]["director"]
    with db.session() as s:
        update_settings(s, d, {"fop_income_limit": 144404900})
        r = tax_report.income_report(s, d, 2026, 2)
    assert [m["month"] for m in r["months"]] == ["2026-04", "2026-05", "2026-06"]
    assert r["total"]["total"] == 45000 and [q["quarter"] for q in r["quarters"]] == [2]
    assert r["year_to_date"] == 225000
    assert r["limit"] == {"amount": 144404900, "used_bp": 15, "remaining": 144179900}


def test_corrected_order_uses_new_amount(db, seed, year_data):
    d = seed["actors"]["director"]
    with db.session() as s:
        orders.correct_order(s, d, year_data["first"], {"amount": 40000}, "клієнт отримав знижку")
    with db.session() as s:
        r = tax_report.income_report(s, d, 2026, 1)
    assert r["total"]["total"] == 170000 and r["months"][0]["cash"] == 40000


def test_csv_by_day_and_by_order(db, seed, year_data):
    d = seed["actors"]["director"]
    with db.session() as s:
        body, name = tax_report.income_csv(s, d, 2026, 1, "day")
        body2, name2 = tax_report.income_csv(s, d, 2026, None, "order")
    assert body.startswith("﻿") and name == "dohody-2026-Q1-dni.csv"
    lines = body.lstrip("﻿").split("\r\n")
    assert lines[0] == "Дата;Дохід, грн;Готівка, грн;Картка, грн;Переказ, грн;Кількість замовлень"
    assert lines[1:4] == ["15.01.2026;500,00;500,00;0,00;0,00;1", "16.01.2026;600,00;0,00;600,00;0,00;1",
                          "31.03.2026;700,00;0,00;700,00;0,00;1"]
    assert "Разом;1800,00" in body
    assert name2 == "dohody-2026-zamovlennia.csv"
    assert "Стрижка;Ірина;Картка;600,00" in body2 and "Разом;2250,00" in body2


def test_validation_and_permissions(db, seed):
    with db.session() as s:
        with pytest.raises(ValidationError):
            tax_report.income_report(s, seed["actors"]["director"], 2026, 5)
        with pytest.raises(ValidationError):
            tax_report.income_csv(s, seed["actors"]["director"], 2026, None, "week")
        for who in ("m1", "m2"):
            with pytest.raises(Forbidden):
                tax_report.income_report(s, seed["actors"][who], 2026)


def test_api_report_and_csv(client_api):
    c, seed, hd = client_api
    r = c.get("/api/reports/income", params={"year": 2026}, headers=hd)
    assert r.status_code == 200 and r.json()["total"]["total"] == 0
    r = c.get("/api/reports/income.csv", params={"year": 2026, "quarter": 1}, headers=hd)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    tok = c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/reports/income", params={"year": 2026}, headers=h).status_code == 403
    assert c.get("/api/reports/income.csv", params={"year": 2026}, headers=h).status_code == 403
