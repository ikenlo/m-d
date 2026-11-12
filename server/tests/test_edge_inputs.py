from datetime import datetime

from fastapi.testclient import TestClient

from mds.api.app import create_app
from mds.config import Config
from mds.services import bookings

BIG = 10**30


def test_no_server_errors_on_bad_numbers(db, seed, tmp_path):
    cfg = Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="test-secret-key-for-unit-tests-only-0123456789")
    with db.session() as s:
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10, 0), client_name="К", client_phone="0670000001")
        appt = a.id
    with TestClient(create_app(cfg, db), raise_server_exceptions=False) as c:
        tok = c.post("/api/auth/login", json={"username": "director", "password": "Director123"}).json()["token"]
        hd = {"Authorization": f"Bearer {tok}"}
        cases = [
            ("post", f"/api/appointments/{appt}/close", {"amount": -1, "payment_method": "cash"}),
            ("post", f"/api/appointments/{appt}/close", {"amount": BIG, "payment_method": "cash"}),
            ("post", f"/api/appointments/{appt}/close", {"amount": 100, "payment_method": "bitcoin"}),
            ("post", f"/api/appointments/{appt}/close", {"amount": 100, "payment_method": "cash",
                                                         "materials": [{"material_id": seed["gel"], "qty": 1e300}]}),
            ("post", f"/api/appointments/{appt}/close", {"amount": 100, "payment_method": "cash",
                                                         "materials": [{"material_id": 999, "qty": 1}]}),
            ("post", "/api/expenses", {"category": "rent", "amount": -5, "spent_on": "2026-11-02"}),
            ("post", "/api/expenses", {"category": "rent", "amount": BIG, "spent_on": "2026-11-02"}),
            ("post", "/api/expenses", {"category": "nope", "amount": 5, "spent_on": "2026-11-02"}),
            ("post", "/api/payouts", {"master_id": seed["m1"], "amount": 0}),
            ("post", "/api/payouts", {"master_id": seed["m1"], "amount": BIG}),
            ("post", "/api/payouts", {"master_id": 999, "amount": 100}),
            ("post", "/api/services", {"name": "X", "duration_min": -30, "price": 100}),
            ("post", "/api/services", {"name": "X", "duration_min": 30, "price": BIG}),
            ("patch", f"/api/users/{seed['m1']}", {"master_rate_bp": 20000}),
            ("put", "/api/settings", {"workplaces": 0}),
            ("put", "/api/settings", {"slot_step_min": 0}),
            ("post", "/api/appointments", {"master_id": seed["m1"], "service_id": seed["haircut"],
                                           "starts_at": "2026-11-02T03:00", "client_name": "К", "client_phone": "1"}),
            ("get", "/api/finance/dashboard?date_from=2026-13-01", None),
            ("get", "/api/orders?period=century", None),
            ("get", "/api/schedule?date_from=2026-11-02&date_to=2030-11-02", None),
        ]
        bad = []
        for method, url, body in cases:
            r = getattr(c, method)(url, headers=hd, **({"json": body} if body is not None else {}))
            if r.status_code >= 500 or r.status_code < 400:
                bad.append((method, url, body, r.status_code, r.text[:150]))
        for x in bad:
            print("BAD", x)
        assert not bad
