from datetime import date, datetime

from mds import clock
from mds.services import analytics, bookings, orders


def _visit(db, seed, at, phone, amount, master="m1", close=True):
    clock.set_now(at.replace(hour=7))
    with db.session() as s:
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed[master], service_id=seed["haircut"],
                                        starts_at=at, client_name="Клієнт " + phone[-2:], client_phone=phone)
        aid = a.id
    if close:
        clock.set_now(at.replace(hour=at.hour + 1))
        with db.session() as s:
            orders.close_order(s, seed["actors"][master], aid, amount=amount, payment_method="cash")
    return aid


def test_insights_compare_clients_weekdays_and_forecast(db, seed):
    _visit(db, seed, datetime(2026, 10, 5, 10), "0670000001", 50000)
    _visit(db, seed, datetime(2026, 10, 6, 10), "0670000002", 30000, "m2")
    _visit(db, seed, datetime(2026, 11, 2, 9), "0670000001", 60000)
    _visit(db, seed, datetime(2026, 11, 2, 12), "0670000003", 40000, "m2")
    _visit(db, seed, datetime(2026, 11, 20, 10), "0670000004", 0, close=False)

    clock.set_now(datetime(2026, 11, 3, 12, 0))
    with db.session() as s:
        d = seed["actors"]["director"]
        r = analytics.insights(s, d, date(2026, 11, 1), date(2026, 11, 30))

    assert r["previous"] == {"from": "2026-10-01", "to": "2026-10-31"}
    c = r["compare"]
    assert c["revenue"] == {"current": 100000, "previous": 80000, "change_bp": 2500}
    assert c["orders_count"]["current"] == 2 and c["orders_count"]["change_bp"] == 0
    assert c["average_check"] == {"current": 50000, "previous": 40000, "change_bp": 2500}
    assert r["clients"] == {"total": 2, "new": 1, "returning": 1, "repeat_in_period": 0}
    mon = r["weekdays"][0]
    assert mon["revenue"] == 100000 and mon["orders"] == 2 and mon["average"] == 100000 // 5
    assert [m["average_check"] for m in r["masters"]] == [60000, 40000]

    f = r["forecast"]
    assert f["actual"] == 100000 and f["booked"] == 50000 and f["booked_count"] == 1
    assert f["run_rate"] == 100000 * (30 * 86400) // (2 * 86400 + 12 * 3600) == 1200000
    assert f["forecast"] == 1200000
    assert f["forecast_gross"] == 1200000 * (100000 - 46000) // 100000 == 648000
    assert f["forecast_net"] == 648000 and f["operating_expenses"] == 0


def test_forecast_uses_bookings_when_month_just_started(db, seed):
    _visit(db, seed, datetime(2026, 11, 2, 9), "0670000001", 20000)
    _visit(db, seed, datetime(2026, 11, 25, 10), "0670000002", 0, close=False)
    _visit(db, seed, datetime(2026, 11, 26, 10), "0670000003", 0, close=False)
    clock.set_now(datetime(2026, 11, 2, 11, 0))
    with db.session() as s:
        f = analytics.forecast_month(s, seed["actors"]["director"])
    run = 20000 * (30 * 86400) // (86400 + 11 * 3600)
    assert f["run_rate"] == run
    assert f["forecast"] == max(run, 20000 + 2 * 50000)


def test_insights_api_director_only(client_api):
    c, ids, d = client_api
    r = c.get("/api/finance/insights?period=month", headers=d)
    assert r.status_code == 200 and "forecast" in r.json()
    tok = c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]
    assert c.get("/api/finance/insights?period=month", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
