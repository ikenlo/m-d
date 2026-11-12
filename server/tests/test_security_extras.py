from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select

from mds import clock
from mds.models import Expense, LoginEvent, Role
from mds.security import create_token, decode_token
from mds.services import security_journal as sj
from mds.services import users
from mds.services.common import Actor


def tok(c, u, p):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": u, "password": p}).json()["token"]}


def test_login_journal_records_success_and_failure(client_api, db):
    c, seed, hd = client_api
    assert c.post("/api/auth/login", json={"username": "olena", "password": "wrong"}, headers={"User-Agent": "UA-1"}).status_code == 401
    tok(c, "olena", "Master123")
    rows = c.get("/api/security/logins", headers=hd).json()
    assert rows[0]["success"] and rows[0]["user_name"] == "Олена"
    assert any(not r["success"] and r["user_agent"] == "UA-1" for r in rows)
    assert c.get("/api/security/logins", headers=tok(c, "olena", "Master123")).status_code == 403


def test_ip_throttle_blocks_brute_force(client_api):
    c, seed, hd = client_api
    h = {"X-Forwarded-For": "203.0.113.7"}
    for i in range(20):
        c.post("/api/auth/login", json={"username": f"nobody{i}", "password": "x"}, headers=h)
    r = c.post("/api/auth/login", json={"username": "director", "password": "Director123"}, headers=h)
    assert r.status_code == 429
    r = c.post("/api/auth/login", json={"username": "director", "password": "Director123"},
               headers={"X-Forwarded-For": "198.51.100.1"})
    assert r.status_code == 200


def test_login_events_kept_90_days(db, seed):
    with db.session() as s:
        sj.log_login(s, user=None, username="x", ip=None, user_agent=None, method="password", success=False)
    clock.set_now(clock.now() + timedelta(days=91))
    with db.session() as s:
        sj.log_login(s, user=None, username="y", ip=None, user_agent=None, method="password", success=True)
        assert sj.cleanup(s) == 1
        assert [e.username for e in s.scalars(select(LoginEvent))] == ["y"]


def test_revoke_sessions_and_logout_all(client_api):
    c, seed, hd = client_api
    m = tok(c, "olena", "Master123")
    assert c.post(f"/api/users/{seed['m1']}/revoke-sessions", headers=m).status_code == 200
    assert c.get("/api/auth/me", headers=m).status_code == 401
    m = tok(c, "olena", "Master123")
    assert c.post(f"/api/users/{seed['director']}/revoke-sessions", headers=m).status_code == 403
    assert c.post(f"/api/users/{seed['m1']}/revoke-sessions", headers=hd).status_code == 200
    assert c.get("/api/auth/me", headers=m).status_code == 401
    d2 = tok(c, "director", "Director123")
    assert c.post("/api/auth/logout-all", headers=d2).status_code == 200
    assert c.get("/api/auth/me", headers=d2).status_code == 401


def test_idle_token_expires_and_refresh_has_absolute_cap():
    secret = "s" * 40
    t = create_token(secret, 1, 0, 12, idle_minutes=30)
    p = decode_token(secret, t)
    assert p["exp"] - p["iat"] == 30 * 60 and p["abs"] - p["st"] == 12 * 3600
    old = int((datetime.now(timezone.utc) - timedelta(hours=11, minutes=50)).timestamp())
    t2 = create_token(secret, 1, 0, 12, idle_minutes=30, started=old)
    p2 = decode_token(secret, t2)
    assert p2["exp"] == p2["abs"] and p2["exp"] - p2["iat"] < 30 * 60
    expired = jwt.encode({"sub": "1", "tv": 0, "exp": int(datetime.now(timezone.utc).timestamp()) - 5}, secret, algorithm="HS256")
    assert decode_token(secret, expired) is None


def test_refresh_endpoint(client_api):
    c, seed, hd = client_api
    r = c.post("/api/auth/refresh", headers=hd)
    assert r.status_code == 200 and r.json()["idle_minutes"] == 30
    assert c.get("/api/auth/me", headers={"Authorization": "Bearer " + r.json()["token"]}).status_code == 200


def test_finance_pin_gate(client_api, db):
    c, seed, hd = client_api
    assert c.get("/api/finance/dashboard", headers=hd).status_code == 200
    assert c.put("/api/settings", headers=hd, json={"finance_pin_enabled": True}).status_code == 200
    r = c.get("/api/finance/dashboard", headers=hd)
    assert r.status_code == 403 and r.json()["error"] == "finance_pin_setup"
    assert c.post("/api/auth/finance-pin", headers=hd, json={"pin": "12"}).status_code == 422
    assert c.post("/api/auth/finance-pin", headers=hd, json={"pin": "2468"}).status_code == 200
    assert c.post("/api/auth/finance-pin", headers=hd, json={"pin": "1357"}).status_code == 422
    assert c.get("/api/finance/dashboard", headers=hd).json()["error"] == "finance_pin_required"
    assert c.post("/api/auth/finance-verify", headers=hd, json={"pin": "0000"}).status_code == 401
    ft = c.post("/api/auth/finance-verify", headers=hd, json={"pin": "2468"}).json()["finance_token"]
    assert c.get("/api/finance/dashboard", headers={**hd, "X-Finance-Token": ft}).status_code == 200
    assert c.get("/api/auth/me", headers={"Authorization": "Bearer " + ft}).status_code == 401
    for _ in range(5):
        c.post("/api/auth/finance-verify", headers=hd, json={"pin": "0000"})
    assert c.post("/api/auth/finance-verify", headers=hd, json={"pin": "2468"}).status_code == 429


def test_finance_pin_reset_by_other_director(db, seed):
    from mds.errors import Forbidden
    import pytest
    d = seed["actors"]["director"]
    with db.session() as s:
        d2 = users.create_user(s, Actor.system(), username="director2", password="Director234", full_name="Дмитро", role=Role.DIRECTOR)
        sj.set_finance_pin(s, d, "2468")
        with pytest.raises(Forbidden):
            sj.reset_finance_pin(s, d, seed["director"])
        sj.reset_finance_pin(s, Actor.of(d2), seed["director"])
        assert s.get(__import__("mds.models", fromlist=["User"]).User, seed["director"]).finance_pin_hash is None


def test_expense_archived_not_deleted_and_csp(client_api, db):
    c, seed, hd = client_api
    e = c.post("/api/expenses", headers=hd, json={"category": "other", "amount": 500, "spent_on": "2026-11-02"}).json()
    assert c.delete(f"/api/expenses/{e['id']}", headers=hd).status_code == 200
    with db.session() as s:
        row = s.get(Expense, e["id"])
        assert row is not None and row.is_deleted and row.archived_at is not None
    r = c.get("/", headers={"X-Forwarded-Proto": "https"})
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "max-age" in r.headers["strict-transport-security"]
    assert "content-security-policy" not in c.get("/docs").headers


def test_bot_pin_login_is_journaled(db, seed):
    with db.session() as s:
        users.set_bot_pin(s, Actor.system(), seed["m1"], "1111")
        users.verify_bot_pin(s, seed["m1"], "0000", chat_id=5)
        users.verify_bot_pin(s, seed["m1"], "1111", chat_id=5)
        rows = [(e.method, e.success) for e in s.scalars(select(LoginEvent).order_by(LoginEvent.id))]
        assert rows == [("bot_pin", False), ("bot_pin", True)]
