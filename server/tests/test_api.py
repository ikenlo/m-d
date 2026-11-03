
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

    # адміністратор створює запис
    r = client.post("/api/appointments", headers=adm, json={
        "master_id": ids["m1"], "service_id": ids["manicure"], "starts_at": "2026-11-02T10:00",
        "client_name": "Анна", "client_phone": "067 111 22 33"})
    assert r.status_code == 201, r.text
    appt = r.json()
    # подвійний запис — 409
    r = client.post("/api/appointments", headers=adm, json={
        "master_id": ids["m1"], "service_id": ids["haircut"], "starts_at": "2026-11-02T11:00",
        "client_name": "Б", "client_phone": "0500000000"})
    assert r.status_code == 409 and r.json()["error"] == "slot_taken"

    # майстер бачить свій запис на сьогодні
    r = client.get("/api/appointments?date_from=2026-11-02", headers=m1)
    assert [a["id"] for a in r.json()] == [appt["id"]]
    assert r.json()[0]["client"]["phone"] == "+380671112233"

    # приймає клієнта і закриває замовлення
    assert client.post(f"/api/appointments/{appt['id']}/status", headers=m1, json={"status": "in_progress"}).status_code == 200
    body = {"amount": 65000, "payment_method": "card", "materials": [{"material_id": ids["gel"], "qty": 1}],
            "idempotency_key": "tap-1"}
    r1 = client.post(f"/api/appointments/{appt['id']}/close", headers=m1, json=body)
    r2 = client.post(f"/api/appointments/{appt['id']}/close", headers=m1, json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["master_earning"] == 32500
    r3 = client.post(f"/api/appointments/{appt['id']}/close", headers=m1, json={**body, "idempotency_key": "tap-2"})
    assert r3.status_code == 409

    # заробіток майстра
    my = client.get("/api/finance/my", headers=m1).json()
    assert my["day"]["earning"] == 32500 and my["balance"]["balance"] == 32500

    # майстер не має доступу до панелі директора
    assert client.get("/api/finance/dashboard", headers=m1).status_code == 403
    assert client.get("/api/finance/dashboard", headers=adm).status_code == 403

    # закриття зміни
    r = client.post("/api/shifts/close", headers=m1)
    assert r.status_code == 200, r.text
    shift = r.json()
    assert shift["report"]["revenue"] == 65000 and shift["payout_due"] == 32500

    # директор бачить звіт і підтверджує виплату
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

    # виправлення директором з історією
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
    # деактивація скасовує токени
    assert client.patch(f"/api/users/{uid}", headers=d, json={"is_active": False}).status_code == 200
    assert client.get("/api/auth/me", headers=nm).status_code == 401
    # налаштування
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
    assert client.get("/api/health").json() == {"ok": True}


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
