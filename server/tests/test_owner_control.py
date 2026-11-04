"""Екран керівника (сьогодні + контроль) і календар податків/звітності."""

from datetime import date, datetime

import pytest

from mds import clock
from mds.errors import Forbidden
from mds.services import analytics, bookings, orders, taxes
from mds.services.common import update_settings


def _visit(db, seed, at, amount, pay, phone):
    clock.set_now(at.replace(hour=7))
    with db.session() as s:
        aid = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=seed["haircut"],
                                          starts_at=at, client_name="К", client_phone=phone).id
    clock.set_now(at.replace(hour=at.hour + 1))
    with db.session() as s:
        orders.close_order(s, seed["actors"]["m1"], aid, amount=amount, payment_method=pay)


def test_calendar_group1_dates_amounts_status(db, seed):
    d = seed["actors"]["director"]
    clock.set_now(datetime(2026, 3, 15, 10))
    with db.session() as s:
        taxes.add_rate(s, d, key="min_wage", valid_from=date(2026, 1, 1), amount=864700)
        taxes.add_rate(s, d, key="single_tax", valid_from=date(2026, 1, 1), amount=33280, due_day=20, due_from="start")
        taxes.add_rate(s, d, key="military_fop", valid_from=date(2026, 1, 1), rate_bp=1000, base="min_wage", due_day=20,
                       due_from="start")
        taxes.add_rate(s, d, key="esv_fop", valid_from=date(2026, 1, 1), rate_bp=2200, base="min_wage", period="quarter",
                       due_day=20, due_from="end", due_offset_months=1)
        taxes.accrue_fop(s, d, "single_tax", date(2026, 1, 1))
        taxes.pay_tax(s, d, key="single_tax", period_start=date(2026, 1, 1), amount=33280, paid_on=date(2026, 1, 19))
        taxes.accrue_fop(s, d, "military_fop", date(2026, 2, 1))  # не сплачено → прострочено (строк 20.02)
        ev = taxes.tax_calendar(s, d, 2026)
    by = {(e["title"], e["period_from"]): e for e in ev}
    jan = by[("Єдиний податок (аванс за місяць)", "2026-01-01")]
    assert jan["due"] == "2026-01-20" and jan["status"] == "paid" and jan["amount"] == 33280
    assert by[("Військовий збір ФОП (за місяць)", "2026-02-01")]["status"] == "overdue"
    assert by[("Військовий збір ФОП (за місяць)", "2026-04-01")]["amount"] == 86470
    # 20.06.2026 — субота → 22.06 (понеділок)
    assert by[("Єдиний податок (аванс за місяць)", "2026-06-01")]["due"] == "2026-06-22"
    q4 = by[("ЄСВ ФОП за себе за 4 квартал", "2026-10-01")]
    assert q4["due"] == "2027-01-20" and q4["amount"] == 3 * 190234
    decl = [e for e in ev if e["kind"] == "report"]
    assert len(decl) == 1 and decl[0]["due"] == "2027-03-01"
    assert not any("найманих" in e["title"] for e in ev)
    clock.set_now(None)


def test_calendar_group3_and_employees(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        update_settings(s, d, {"fop_group": 3})
        taxes.set_employee(s, d, seed["m1"], {"tin": "1234567890", "hired_on": date(2026, 2, 1)})
        ev = taxes.tax_calendar(s, d, 2026)
    reports = [e for e in ev if e["kind"] == "report"]
    assert sum("3 група" in e["title"] for e in reports) == 4 and sum("найманих" in e["title"] for e in reports) == 4
    q1 = next(e for e in reports if "найманих" in e["title"] and "1 квартал" in e["title"])
    assert q1["due"] == "2026-05-11"  # 31.03 + 40 днів = 10.05 (неділя) → 11.05


def test_owner_control_today_and_alerts(db, seed):
    d = seed["actors"]["director"]
    _visit(db, seed, datetime(2026, 11, 4, 10), 50000, "cash", "0670000001")
    _visit(db, seed, datetime(2026, 11, 4, 12), 65000, "card", "0670000002")
    _visit(db, seed, datetime(2026, 11, 3, 10), 99900, "card", "0670000003")
    clock.set_now(datetime(2026, 11, 4, 18))
    with db.session() as s:
        update_settings(s, d, {"fop_income_limit": 200000})
        c = analytics.owner_control(s, d)
        with pytest.raises(Forbidden):
            analytics.owner_control(s, seed["actors"]["admin"])
    clock.set_now(None)
    assert c["today"] == {"date": "2026-11-04", "revenue": 115000, "cash": 50000, "noncash": 65000, "orders": 2}
    assert c["year_to_date"] == 214900 and c["fiscal"]["connected"] is False
    assert any("ліміту групи" in a["text"] and a["level"] == "danger" for a in c["alerts"])
    # строк 20.11 — через 16 днів, поза вікном 10 днів
    assert not any(a["text"].startswith("Скоро: Єдиний податок") for a in c["alerts"])
    clock.set_now(datetime(2026, 11, 12, 9))
    with db.session() as s:
        c2 = analytics.owner_control(s, d)
    clock.set_now(None)
    assert any(a["text"].startswith("Скоро: Єдиний податок") and "2026-11-20" in a["text"] for a in c2["alerts"])
    assert c2["today"]["revenue"] == 0 and c2["next_tax"]["due"] == "2026-11-20"
