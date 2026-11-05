import re
from datetime import datetime, timedelta

from mds import clock
from mds.models import LoginChallenge, Notification, User

from .test_api import client  # noqa: F401, F811


def _code(db):
    with db.session() as s:
        n = s.query(Notification).order_by(Notification.id.desc()).first()
        return re.search(r"<b>(\d{6})</b>", n.text).group(1)


def _dir_id(db):
    with db.session() as s:
        return s.query(User).filter_by(username="director").one().id


def test_director_without_telegram_logs_in_directly(client, db):  # noqa: F811
    r = client.post("/api/auth/login", json={"username": "director", "password": "Director123"})
    assert r.status_code == 200 and "token" in r.json()


def test_code_flow_attempts_and_trusted_device(client, db):  # noqa: F811
    did = _dir_id(db)
    with db.session() as s:
        s.get(User, did).telegram_chat_id = 9001
    r = client.post("/api/auth/login", json={"username": "director", "password": "Director123"})
    assert r.status_code == 200 and r.json()["twofa"] is True and "token" not in r.json()
    ch = r.json()["challenge_id"]
    code = _code(db)
    with db.session() as s:
        assert s.get(LoginChallenge, ch).code_hash != code
    wrong = "000000" if code != "000000" else "111111"
    r = client.post("/api/auth/login/verify", json={"challenge_id": ch, "code": wrong})
    assert r.status_code == 401 and "Залишилось спроб: 4" in r.json()["detail"]
    r = client.post("/api/auth/login/verify", json={"challenge_id": ch, "code": code, "remember": True, "device_label": "iPhone"})
    assert r.status_code == 200 and r.json()["token"] and r.json()["device_token"]
    dev = r.json()["device_token"]
    auth = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.post("/api/auth/login/verify", json={"challenge_id": ch, "code": code}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "director", "password": "Director123", "device_token": dev})
    assert "token" in r.json()
    assert [d["label"] for d in client.get("/api/auth/devices", headers=auth).json()] == ["iPhone"]
    assert client.delete("/api/auth/devices", headers=auth).json()["forgotten"] == 1
    r = client.post("/api/auth/login", json={"username": "director", "password": "Director123", "device_token": dev})
    assert r.json().get("twofa") is True


def test_too_many_attempts_and_expiry(client, db):  # noqa: F811
    did = _dir_id(db)
    with db.session() as s:
        s.get(User, did).telegram_chat_id = 9001
    ch = client.post("/api/auth/login", json={"username": "director", "password": "Director123"}).json()["challenge_id"]
    code = _code(db)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post("/api/auth/login/verify", json={"challenge_id": ch, "code": wrong})
    r = client.post("/api/auth/login/verify", json={"challenge_id": ch, "code": code})
    assert r.status_code == 401 and "Забагато спроб" in r.json()["detail"]
    clock.set_now(datetime(2026, 11, 4, 12, 0))
    ch = client.post("/api/auth/login", json={"username": "director", "password": "Director123"}).json()["challenge_id"]
    code = _code(db)
    clock.set_now(datetime(2026, 11, 4, 12, 0) + timedelta(minutes=6))
    r = client.post("/api/auth/login/verify", json={"challenge_id": ch, "code": code})
    clock.set_now(None)
    assert r.status_code == 401 and "застарів" in r.json()["detail"]
    assert client.post("/api/auth/login/verify", json={"challenge_id": ch, "code": "12ab"}).status_code == 422


def test_admin_only_when_enabled_and_password_change_forgets_devices(client, db):  # noqa: F811
    with db.session() as s:
        s.query(User).filter_by(username="admin").one().telegram_chat_id = 9003
    assert "token" in client.post("/api/auth/login", json={"username": "admin", "password": "Admin1234"}).json()
    d = client.post("/api/auth/login", json={"username": "director", "password": "Director123"}).json()
    hd = {"Authorization": "Bearer " + d["token"]}
    assert client.put("/api/settings", headers=hd, json={"twofa_roles": ["director", "admin"]}).status_code == 200
    assert client.put("/api/settings", headers=hd, json={"twofa_roles": ["master"]}).status_code == 422
    r = client.post("/api/auth/login", json={"username": "admin", "password": "Admin1234"}).json()
    assert r["twofa"] is True
    r = client.post("/api/auth/login/verify", json={"challenge_id": r["challenge_id"], "code": _code(db), "remember": True}).json()
    dev, ha = r["device_token"], {"Authorization": "Bearer " + r["token"]}
    assert client.post("/api/auth/change-password", headers=ha, json={"old_password": "Admin1234", "new_password": "NewAdmin567"}).status_code == 200
    r = client.post("/api/auth/login", json={"username": "admin", "password": "NewAdmin567", "device_token": dev}).json()
    assert r.get("twofa") is True


def test_emergency_disable(client, db, monkeypatch):  # noqa: F811
    with db.session() as s:
        s.query(User).filter_by(username="director").one().telegram_chat_id = 9001
    monkeypatch.setenv("MDS_TWOFA_DISABLED", "1")
    assert "token" in client.post("/api/auth/login", json={"username": "director", "password": "Director123"}).json()
