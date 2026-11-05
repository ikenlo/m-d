from datetime import datetime


import pytest
from fastapi.testclient import TestClient

from mds.api.app import create_app
from mds.config import Config


@pytest.fixture
def client(db, seed, tmp_path):
    cfg = Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="test-secret-key-for-unit-tests-only-0123456789")
    app = create_app(cfg, db)
    with TestClient(app) as c:
        c.ids = seed
        yield c


def login(c, username, password):
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_auth_required_and_bad_login(client):
    assert client.get("/api/appointments?date_from=2026-11-02").status_code == 401
    r = client.post("/api/auth/login", json={"username": "olena", "password": "nope"})
    assert r.status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_lockout_persists_through_api(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "olena", "password": "nope"})
    r = client.post("/api/auth/login", json={"username": "olena", "password": "Master123"})
    assert r.status_code == 401 and "заблоковано" in r.json()["detail"]


def test_full_workday_flow(client):
    ids = client.ids
    adm = login(client, "admin", "Admin1234")
    m1 = login(client, "olena", "Master123")
    d = login(client, "director", "Director123")

    r = client.post("/api/appointments", headers=adm, json={
        "master_id": ids["m1"], "service_id": ids["manicure"], "starts_at": "2026-11-02T10:00",
        "client_name": "Анна", "client_phone": "067 111 22 33"})
    assert r.status_code == 201, r.text
    appt = r.json()
    r = client.post("/api/appointments", headers=adm, json={
        "master_id": ids["m1"], "service_id": ids["haircut"], "starts_at": "2026-11-02T11:00",
        "client_name": "Б", "client_phone": "0500000000"})
    assert r.status_code == 409 and r.json()["error"] == "slot_taken"

    r = client.get("/api/appointments?date_from=2026-11-02", headers=m1)
    assert [a["id"] for a in r.json()] == [appt["id"]]
    assert r.json()[0]["client"]["phone"] == "+380671112233"

    assert client.post(f"/api/appointments/{appt['id']}/status", headers=m1, json={"status": "in_progress"}).status_code == 200
    body = {"amount": 65000, "payment_method": "card", "materials": [{"material_id": ids["gel"], "qty": 1}],
            "idempotency_key": "tap-1", "client_name": "Анна Коваленко"}
    r1 = client.post(f"/api/appointments/{appt['id']}/close", headers=m1, json=body)
    r2 = client.post(f"/api/appointments/{appt['id']}/close", headers=m1, json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["master_earning"] == 32500
    r = client.get("/api/appointments?date_from=2026-11-02", headers=m1)
    assert r.json()[0]["client"]["name"] == "Анна Коваленко"
    r3 = client.post(f"/api/appointments/{appt['id']}/close", headers=m1, json={**body, "idempotency_key": "tap-2"})
    assert r3.status_code == 409

    my = client.get("/api/finance/my", headers=m1).json()
    assert my["day"]["earning"] == 32500 and my["balance"]["balance"] == 32500

    assert client.get("/api/finance/dashboard", headers=m1).status_code == 403
    assert client.get("/api/finance/dashboard", headers=adm).status_code == 403

    r = client.post("/api/shifts/close", headers=m1)
    assert r.status_code == 200, r.text
    shift = r.json()
    assert shift["report"]["revenue"] == 65000 and shift["payout_due"] == 32500

    pending = client.get("/api/shifts?status=closed", headers=d).json()
    assert [x["id"] for x in pending] == [shift["id"]]
    p = client.post("/api/payouts", headers=d, json={"master_id": ids["m1"], "amount": 32500, "shift_id": shift["id"]})
    assert p.status_code == 201
    assert client.post("/api/payouts", headers=d, json={"master_id": ids["m1"], "amount": 32500, "shift_id": shift["id"]}).status_code == 409

    dash = client.get("/api/finance/dashboard?period=month", headers=d).json()
    assert dash["summary"]["revenue"] == 65000
    assert dash["summary"]["payouts"] == 32500
    assert dash["summary"]["salary_debt_total"] == 0
    assert dash["by_service"][0]["service_name"] == "Манікюр"

    r = client.patch(f"/api/orders/{r1.json()['id']}", headers=d, json={"amount": 70000, "reason": "Доплата за дизайн"})
    assert r.status_code == 200 and r.json()["revision"] == 2
    order = client.get(f"/api/orders/{r1.json()['id']}", headers=d).json()
    assert [h["action"] for h in order["history"]][:2] == ["correct", "close"]
    assert client.patch(f"/api/orders/{r1.json()['id']}", headers=m1, json={"amount": 1, "reason": "x"}).status_code == 403

    ledger = client.get("/api/journal/ledger", headers=d).json()
    assert {e["kind"] for e in ledger} >= {"order_closed", "order_reversal", "order_corrected", "payout", "shift_closed"}
    assert client.get("/api/journal/ledger", headers=m1).status_code == 403


def test_director_manages_staff_and_settings(client):
    d = login(client, "director", "Director123")
    r = client.post("/api/users", headers=d, json={
        "username": "new_master", "password": "Passw0rd!", "full_name": "Нова", "role": "master", "share_bp": 6000})
    assert r.status_code == 201
    uid = r.json()["id"]
    assert client.post("/api/users", headers=d, json={
        "username": "weak", "password": "123", "full_name": "x", "role": "master"}).status_code == 422
    nm = login(client, "new_master", "Passw0rd!")
    assert client.get("/api/auth/me", headers=nm).json()["role"] == "master"
    assert client.patch(f"/api/users/{uid}", headers=d, json={"is_active": False}).status_code == 200
    assert client.get("/api/auth/me", headers=nm).status_code == 401
    r = client.put("/api/settings", headers=d, json={"default_share_bp": 5500})
    assert r.status_code == 200 and r.json()["default_share_bp"] == 5500
    assert client.put("/api/settings", headers=d, json={"default_share_bp": 20000}).status_code == 422
    assert client.put("/api/settings", headers=d, json={"bogus": 1}).status_code == 422
    m1 = login(client, "olena", "Master123")
    assert client.put("/api/settings", headers=m1, json={"default_share_bp": 9000}).status_code == 403
    assert client.post("/api/users", headers=m1, json={
        "username": "hack", "password": "Passw0rd!", "full_name": "x", "role": "director"}).status_code == 403


def test_password_change_invalidates_old_token(client):
    m1 = login(client, "olena", "Master123")
    r = client.post("/api/auth/change-password", headers=m1, json={"old_password": "Master123", "new_password": "NewPass123"})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=m1).status_code == 401
    login(client, "olena", "NewPass123")


def test_backup_endpoint(client):
    d = login(client, "director", "Director123")
    r = client.post("/api/backups", headers=d)
    assert r.status_code == 201
    assert client.get("/api/backups", headers=d).json()[0]["name"] == r.json()["name"]


def test_web_panel_served(client):
    assert client.get("/").status_code == 200
    from mds import __version__
    assert client.get("/api/health").json() == {"ok": True, "version": __version__}


def test_master_sees_only_own_clients(client):
    ids = client.ids
    adm = login(client, "admin", "Admin1234")
    m1 = login(client, "olena", "Master123")
    r = client.post("/api/appointments", headers=adm, json={
        "master_id": ids["m2"], "service_id": ids["haircut"], "starts_at": "2026-11-02T10:00",
        "client_name": "Чужа", "client_phone": "0661234567"})
    other_client = r.json()["client"]["id"]
    client.post("/api/appointments", headers=adm, json={
        "master_id": ids["m1"], "service_id": ids["haircut"], "starts_at": "2026-11-02T10:00",
        "client_name": "Своя", "client_phone": "0671234567"})
    names = [c["name"] for c in client.get("/api/clients", headers=m1).json()]
    assert names == ["Своя"]
    assert client.get(f"/api/clients/{other_client}", headers=m1).status_code == 404
    assert len(client.get("/api/clients", headers=adm).json()) == 2


def test_bot_pin_and_staff_code_via_api(client):
    d = login(client, "director", "Director123")
    m1 = login(client, "olena", "Master123")
    uid = client.ids["m1"]
    assert client.patch(f"/api/users/{uid}", headers=d, json={"bot_pin": "12"}).status_code == 422
    r = client.patch(f"/api/users/{uid}", headers=d, json={"bot_pin": "4321"})
    assert r.status_code == 200 and r.json()["bot_pin_set"] is True and "bot_pin_hash" not in r.json()
    assert client.patch(f"/api/users/{uid}", headers=m1, json={"bot_pin": "1111"}).status_code == 403
    assert client.put("/api/settings", headers=d, json={"bot_staff_code": "12a4"}).status_code == 422
    r = client.put("/api/settings", headers=d, json={"bot_staff_code": "5555", "salon_address": "Київ"})
    assert r.json()["bot_staff_code"] == "5555"


def test_pending_api(client):
    from mds.services import bookings
    from mds.services.common import Actor
    ids = client.ids
    db = client.app.state.db
    with db.session() as s:
        a = bookings.create_appointment(s, Actor.telegram(555), master_id=ids["m1"], service_id=ids["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="К", client_phone="0931234567")
        appt_id = a.id
    m1 = login(client, "olena", "Master123")
    m2 = login(client, "iryna", "Master123")
    assert [x["id"] for x in client.get("/api/appointments/pending", headers=m1).json()] == [appt_id]
    assert client.get("/api/appointments/pending", headers=m2).json() == []
    assert client.post(f"/api/appointments/{appt_id}/propose", headers=m2, json={"starts_at": "2026-11-02T15:00"}).status_code == 403
    r = client.post(f"/api/appointments/{appt_id}/propose", headers=m1, json={"starts_at": "2026-11-02T15:00"})
    assert r.status_code == 200 and r.json()["notified"] and r.json()["awaiting_client"]
    r = client.post(f"/api/appointments/{appt_id}/decline-busy", headers=m1)
    assert r.status_code == 200 and r.json()["notified"]
    assert client.post(f"/api/appointments/{appt_id}/decline-busy", headers=m1).status_code == 409


def test_bug_report_api(client):
    m1 = login(client, "olena", "Master123")
    r = client.post("/api/bug-report", headers=m1, json={"text": "Кнопка не працює", "page": "today"})
    assert r.status_code == 201 and r.json()["ok"] is True
    assert client.post("/api/bug-report", json={"text": "анонімно"}).status_code == 401


def test_sync_reports_new_bookings_to_others(client):
    from datetime import datetime

    from mds import clock
    from mds.services import bookings
    from mds.services.common import Actor

    clock.set_now(datetime(2026, 11, 2, 8))
    d = login(client, "director", "Director123")
    m1 = login(client, "olena", "Master123")
    rev = client.get("/api/sync", headers=d).json()["rev"]
    m2 = login(client, "iryna", "Master123")
    rev_m = client.get("/api/sync", headers=m1).json()["rev"]
    db = client.app.state.db
    with db.session() as s:
        bookings.create_appointment(s, Actor.telegram(77), master_id=client.ids["m1"], service_id=client.ids["haircut"],
                                    starts_at=datetime(2026, 11, 2, 13), client_name="Ірина", client_phone="0671234500")
    r = client.get(f"/api/sync?since={rev}", headers=d).json()
    assert "appointment" in r["entities"] and r["new"][0]["client_name"] == "Ірина" and r["new"][0]["source"] == "telegram"
    assert client.get(f"/api/sync?since={rev_m}", headers=m1).json()["new"][0]["starts_at"] == "2026-11-02T13:00:00"
    assert client.get(f"/api/sync?since={rev_m}", headers=m2).json()["new"] == []
    rev2 = client.get("/api/sync", headers=d).json()["rev"]
    client.post("/api/settings", headers=d, json={})
    assert client.get(f"/api/sync?since={rev2}", headers=d).json()["new"] == []
    clock.set_now(None)


def test_availability_with_custom_duration(client):
    from datetime import datetime

    from mds import clock

    clock.set_now(datetime(2026, 11, 2, 8))
    d = login(client, "director", "Director123")
    url = f"/api/availability?master_id={client.ids['m1']}&service_id={client.ids['haircut']}&day=2026-11-02"
    base = client.get(url, headers=d).json()
    long = client.get(url + "&duration_min=180", headers=d).json()
    assert base and long and len(long) < len(base) and long[-1] < base[-1]
    clock.set_now(None)


def test_static_cache_gzip_and_perf_monitoring(client):
    from mds import perf

    perf.reset()
    html = client.get("/").text
    assert "__V__" not in html and "fonts.googleapis" not in html
    src = html.split('src="')[1].split('"')[0]
    r = client.get(src, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert r.headers.get("content-encoding") == "gzip"
    assert "no-cache" in client.get("/static/icon.svg").headers["cache-control"]

    d = login(client, "director", "Director123")
    m1 = login(client, "olena", "Master123")
    r = client.get("/api/settings", headers=d)
    assert "app;dur=" in r.headers["server-timing"]
    r = client.post("/api/perf/rum", headers=m1, json={"items": [
        {"name": "view:today", "ms": 420}, {"name": "nav:fcp", "ms": 900}, {"name": "view:Іван Петренко", "ms": 5}]})
    assert r.status_code == 204
    assert client.get("/api/perf", headers=m1).status_code == 403
    snap = client.get("/api/perf", headers=d).json()
    assert {x["name"] for x in snap["client"]} == {"view:today", "nav:fcp"}
    assert any(x["name"] == "GET /api/settings" for x in snap["server"])


def test_master_quick_booking_and_own_incomplete_orders_api(client):
    from mds import clock

    ids = client.ids
    m1 = login(client, "olena", "Master123")
    m2 = login(client, "iryna", "Master123")
    d = login(client, "director", "Director123")
    q = f"/api/availability/detail?master_id={ids['m1']}&service_id={ids['haircut']}&day=2026-11-02"
    r = client.get(q, headers=m1).json()
    assert r["working"] and r["shift_open"] is False and "09:00" in r["slots"] and "20:00" in r["off_hours"]
    assert client.get(q, headers=m2).status_code == 403
    assert client.post("/api/shifts/open", headers=m1).status_code == 200
    assert client.get(q, headers=m1).json()["shift_open"] is True

    body = {"master_id": ids["m1"], "service_id": ids["haircut"], "starts_at": "2026-11-02T20:00", "client_name": "Пізній"}
    r = client.post("/api/appointments", headers=m1, json=body)
    assert r.status_code == 409 and r.json()["error"] == "off_hours"
    r = client.post("/api/appointments", headers=m1, json={**body, "confirm_off_hours": True})
    assert r.status_code == 201, r.text
    appt = client.post("/api/appointments", headers=m1, json={**body, "starts_at": "2026-11-02T09:00"}).json()

    clock.set_now(datetime(2026, 11, 2, 10, 5))
    o = client.post(f"/api/appointments/{appt['id']}/close", headers=m1, json={"amount": 50000, "payment_method": "cash"}).json()
    assert o["missing"] == ["phone"] and o["shift_open"] and o["closed_by_name"] == "Олена"
    assert [x["id"] for x in client.get("/api/orders?period=month", headers=m1).json()] == [o["id"]]
    r = client.post("/api/shifts/close", headers=m1)
    assert r.status_code == 409 and r.json()["error"] == "incomplete_orders"
    assert client.put(f"/api/orders/{o['id']}/phone", headers=m2, json={"phone": "0671234567"}).status_code == 403
    r = client.put(f"/api/orders/{o['id']}/phone", headers=m1, json={"phone": "0671234567"})
    assert r.status_code == 200 and r.json()["missing"] == [] and r.json()["edited_by_name"] == "Олена"
    full = client.get(f"/api/orders/{o['id']}", headers=m1).json()
    assert [h["action"] for h in full["history"]] == ["set_phone", "close"]
    assert client.post("/api/shifts/close", headers=m1).status_code == 200
    r = client.put(f"/api/orders/{o['id']}/phone", headers=m1, json={"phone": "0671234568"})
    assert r.status_code == 403
    assert client.get(f"/api/orders/{o['id']}", headers=d).json()["shift_open"] is False
