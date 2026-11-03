"""Матриця прав доступу: майстер не бачить і не змінює чужого та не має доступу до функцій директора."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from mds.api.app import create_app
from mds.config import Config
from mds.services import bookings, orders


@pytest.fixture
def env(db, seed, tmp_path):
    cfg = Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="test-secret-key-for-unit-tests-only-0123456789")
    d = seed["actors"]["director"]
    with db.session() as s:
        # запис і закрите замовлення майстра Ірини (m2)
        a = bookings.create_appointment(s, d, master_id=seed["m2"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10, 0), client_name="Чужий Клієнт",
                                        client_phone="0679998877")
        o = orders.close_order(s, d, a.id, amount=50000, payment_method="cash")
        a2 = bookings.create_appointment(s, d, master_id=seed["m2"], service_id=seed["haircut"],
                                         starts_at=datetime(2026, 11, 2, 12, 0), client_name="Інший",
                                         client_phone="0671234567")
        ids = {"appt": a.id, "appt_open": a2.id, "order": o.id, "shift": o.shift_id, "client": a.client_id}
    with TestClient(create_app(cfg, db)) as c:
        tok = c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]
        yield c, seed, ids, {"Authorization": f"Bearer {tok}"}


def test_master_cannot_touch_other_master_data(env):
    c, seed, ids, m1 = env
    m2 = seed["m2"]
    denied = [
        ("get", f"/api/appointments/{ids['appt']}", None),
        ("patch", f"/api/appointments/{ids['appt_open']}", {"comment": "x"}),
        ("post", f"/api/appointments/{ids['appt_open']}/status", {"status": "cancelled", "reason": "x"}),
        ("post", f"/api/appointments/{ids['appt_open']}/close", {"amount": 100, "payment_method": "cash"}),
        ("post", f"/api/appointments/{ids['appt_open']}/decline-busy", None),
        ("post", f"/api/appointments/{ids['appt_open']}/propose", {"starts_at": "2026-11-02T15:00"}),
        ("get", f"/api/orders/{ids['order']}", None),
        ("patch", f"/api/orders/{ids['order']}", {"amount": 1, "reason": "x"}),
        ("get", f"/api/shifts/{ids['shift']}", None),
        ("post", f"/api/shifts/open?master_id={m2}", None),
        ("post", f"/api/shifts/close?master_id={m2}", None),
        ("get", f"/api/clients/{ids['client']}", None),
        ("patch", f"/api/clients/{ids['client']}", {"name": "Зламано"}),
        ("post", "/api/clients", {"name": "Х", "phone": "0679998877", "notes": "перезапис"}),
        ("put", f"/api/schedule/{m2}/2026-11-03", {"off": True}),
    ]
    bad = []
    for method, url, body in denied:
        r = getattr(c, method)(url, headers=m1, **({"json": body} if body is not None else {}))
        if r.status_code not in (403, 404):
            bad.append((method, url, r.status_code, r.text[:120]))
    assert not bad, bad
    # списки з чужим master_id не повертають чужих даних
    assert c.get(f"/api/appointments?date_from=2026-11-02&master_id={m2}", headers=m1).json() == []
    assert c.get(f"/api/orders?date_from=2026-11-02&master_id={m2}", headers=m1).json() == []
    for url in (f"/api/finance/my?master_id={m2}", f"/api/payouts?master_id={m2}", f"/api/shifts?master_id={m2}"):
        r = c.get(url, headers=m1)
        assert r.status_code in (403, 404) or "Чужий" not in r.text and str(ids["shift"]) not in [str(x.get("id")) for x in (r.json() if isinstance(r.json(), list) else [])], url
    assert all(x["master_id"] != m2 for x in c.get("/api/shifts", headers=m1).json())
    assert "Чужий" not in c.get("/api/clients?q=", headers=m1).text


def test_master_has_no_director_functions(env):
    c, seed, ids, m1 = env
    denied = [
        ("post", "/api/users", {"username": "x1", "password": "Abcdef123", "full_name": "X", "role": "director"}),
        ("patch", f"/api/users/{seed['m1']}", {"share_bp": 10000}),
        ("post", f"/api/users/{seed['m2']}/reset-password", {"password": "Abcdef123"}),
        ("post", "/api/services", {"name": "X", "duration_min": 30, "price": 100}),
        ("patch", f"/api/services/{seed['haircut']}", {"price": 1}),
        ("post", "/api/materials", {"name": "X", "unit_cost": 1}),
        ("patch", f"/api/materials/{seed['gel']}", {"unit_cost": 1}),
        ("get", "/api/finance/dashboard", None),
        ("get", "/api/expenses", None),
        ("post", "/api/expenses", {"category": "rent", "amount": 100, "spent_on": "2026-11-02"}),
        ("post", "/api/payouts", {"master_id": seed["m1"], "amount": 100, "method": "cash"}),
        ("post", f"/api/stock/{seed['gel']}/receive", {"qty": 1}),
        ("post", f"/api/stock/{seed['gel']}/adjust", {"actual": 100}),
        ("post", f"/api/stock/{seed['gel']}/writeoff", {"qty": 1, "note": "x"}),
        ("get", "/api/stock/moves", None),
        ("put", "/api/settings", {"default_share_bp": 10000}),
        ("get", "/api/journal/ledger", None),
        ("get", "/api/journal/audit", None),
        ("get", "/api/backups", None),
        ("post", "/api/backups", None),
        ("get", "/api/callbacks", None),
    ]
    bad = []
    for method, url, body in denied:
        r = getattr(c, method)(url, headers=m1, **({"json": body} if body is not None else {}))
        if r.status_code != 403:
            bad.append((method, url, r.status_code, r.text[:120]))
    assert not bad, bad
    # майстер бачить лише короткі дані колег (для календаря), без логінів і умов оплати
    for u in c.get("/api/users", headers=m1).json():
        assert set(u) <= {"id", "full_name", "role", "service_ids", "schedule"}
