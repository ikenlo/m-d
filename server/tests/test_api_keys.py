import pytest
from fastapi.testclient import TestClient

from mds.api.app import create_app
from mds.config import Config


@pytest.fixture
def c(db, seed, tmp_path):
    cfg = Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="test-secret-key-for-unit-tests-only-0123456789")
    with TestClient(create_app(cfg, db)) as client:
        client.ids = seed
        yield client


def login(c, u, p):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]}


def test_read_key_reads_but_cannot_write_or_reach_admin(c):
    d = login(c, "director", "Director123")
    r = c.post("/api/api-keys", headers=d, json={"name": "Google Таблиці", "scope": "read"})
    assert r.status_code == 201, r.text
    raw = r.json()["key"]
    assert raw.startswith("mds_") and r.json()["prefix"] == raw[:12]
    assert "key" not in c.get("/api/api-keys", headers=d).json()[0]
    k = {"X-Api-Key": raw}
    assert c.get("/api/services", headers=k).status_code == 200
    assert c.get("/api/appointments?date_from=2026-11-02", headers={"Authorization": "Bearer " + raw}).status_code == 200
    r = c.post("/api/appointments", headers=k, json={"master_id": c.ids["m1"], "service_id": c.ids["haircut"],
                                                       "starts_at": "2026-11-02T10:00", "client_name": "Через ключ"})
    assert r.status_code == 403
    for path in ("/api/settings", "/api/users", "/api/api-keys", "/api/backups", "/api/auth/me"):
        assert c.get(path, headers=k).status_code == 403, path
    assert c.get("/api/api-keys", headers=d).json()[0]["last_used_at"]


def test_write_key_creates_appointment_and_is_audited_then_revoked(c):
    d = login(c, "director", "Director123")
    raw = c.post("/api/api-keys", headers=d, json={"name": "Сайт", "scope": "write"}).json()["key"]
    k = {"X-Api-Key": raw}
    r = c.post("/api/appointments", headers=k, json={"master_id": c.ids["m1"], "service_id": c.ids["haircut"],
                                                       "starts_at": "2026-11-02T10:00", "client_name": "Через ключ"})
    assert r.status_code == 201, r.text
    assert c.post("/api/api-keys", headers=k, json={"name": "Ще", "scope": "write"}).status_code == 403
    audit = c.get("/api/journal/audit?entity=appointment", headers=d).json()
    assert any(a["actor"] == "api:Сайт" for a in audit)
    kid = c.get("/api/api-keys", headers=d).json()[0]["id"]
    assert c.post(f"/api/api-keys/{kid}/revoke", headers=d).status_code == 200
    assert c.get("/api/services", headers=k).status_code == 401


def test_only_director_manages_keys_and_bad_input(c):
    m = login(c, "olena", "Master123")
    a = login(c, "admin", "Admin1234")
    d = login(c, "director", "Director123")
    assert c.post("/api/api-keys", headers=m, json={"name": "Х", "scope": "read"}).status_code == 403
    assert c.get("/api/api-keys", headers=a).status_code == 403
    assert c.post("/api/api-keys", headers=d, json={"name": "Х", "scope": "read"}).status_code == 422
    assert c.post("/api/api-keys", headers=d, json={"name": "Норм", "scope": "admin"}).status_code == 422
    assert c.get("/api/services", headers={"X-Api-Key": "mds_wrong"}).status_code == 401


def test_key_stops_working_when_owner_deactivated(c, db, seed):
    from mds.services import users
    from mds.services.common import Actor

    d = login(c, "director", "Director123")
    raw = c.post("/api/api-keys", headers=d, json={"name": "Звіти", "scope": "read"}).json()["key"]
    with db.session() as s:
        u2 = users.create_user(s, Actor.system(), username="dir2", password="Director123", full_name="Другий", role="director")
        users.update_user(s, Actor.of(u2), seed["director"], {"is_active": False})
    assert c.get("/api/services", headers={"X-Api-Key": raw}).status_code == 401
