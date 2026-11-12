"""Черга вебхуків Meta: публічний прийом і pull/ack для MDs Manager (D-5, D-6a)."""
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mds import clock
from mds.api.app import create_app
from mds.config import Config
from mds.models import WebhookQueue

KEY = "manager-test-key-0123456789abcdef"
H = {"X-API-Key": KEY}
VT = "verify-token-123"
SECRET = "meta-app-secret-xyz"


def _client(db, tmp_path, secret=""):
    cfg = Config(data_dir=tmp_path / "data", database_url=db.url,
                 secret_key="test-secret-key-for-unit-tests-only-0123456789", manager_api_key=KEY,
                 meta_verify_token=VT, meta_app_secret=secret, webhook_processing_timeout_minutes=5)
    return TestClient(create_app(cfg, db))


@pytest.fixture
def c(db, tmp_path, fixed_clock):
    with _client(db, tmp_path) as cl:
        yield cl


def _rows(db):
    with db.session() as s:
        return [(w.uuid, w.source, w.status, w.retry_count, w.processed_at, w.error_message)
                for w in s.scalars(select(WebhookQueue).order_by(WebhookQueue.id))]


def _post(c, body, **kw):
    return c.post("/api/webhooks/meta", content=body if isinstance(body, (bytes, str)) else json.dumps(body), **kw)


def _queue(db, n, status="pending", started=None):
    with db.session() as s:
        for i in range(n):
            s.add(WebhookQueue(source="instagram", payload={"i": i}, status=status,
                               received_at=clock.now() - timedelta(minutes=60 - i), processing_started_at=started))


# Перевірка підписки (GET)

def test_verify_ok(c):
    r = c.get("/api/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": VT, "hub.challenge": "12345"})
    assert r.status_code == 200 and r.text == "12345"


@pytest.mark.parametrize("params", [
    {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
    {"hub.mode": "unsubscribe", "hub.verify_token": VT, "hub.challenge": "1"},
    {"hub.challenge": "1"},
])
def test_verify_rejected(c, params):
    r = c.get("/api/webhooks/meta", params=params)
    assert r.status_code == 403 and r.text == "Forbidden"


def test_verify_rejected_when_token_not_configured(db, tmp_path):
    cfg = Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="x" * 40, meta_verify_token="")
    with TestClient(create_app(cfg, db)) as cl:
        r = cl.get("/api/webhooks/meta", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"})
        assert r.status_code == 403


# Прийом подій (POST)

@pytest.mark.parametrize("obj,source", [("instagram", "instagram"), ("page", "facebook"), ("whatsapp_business_account", "unknown")])
def test_post_queues_by_source(c, db, obj, source):
    r = _post(c, {"object": obj, "entry": [{"id": "1"}]})
    assert r.status_code == 200 and r.json() == {"ok": True}
    [(uuid, src, status, retries, processed, _)] = _rows(db)
    assert (src, status, retries, processed) == (source, "pending", 0, None) and len(uuid) == 36
    with db.session() as s:
        w = s.scalar(select(WebhookQueue))
        assert w.payload == {"object": obj, "entry": [{"id": "1"}]} and w.received_at == clock.now()


@pytest.mark.parametrize("body", [b"{not json", b"[1, 2]", b""])
def test_post_bad_json(c, db, body):
    r = _post(c, body)
    assert r.status_code == 400 and r.json()["error"] == "bad_json"
    assert _rows(db) == []


def test_post_too_large(c, db):
    r = _post(c, b'{"object":"page","x":"' + b"a" * (1024 * 1024) + b'"}')
    assert r.status_code == 413 and _rows(db) == []


def test_post_public_no_api_key_or_jwt_needed(c):
    assert _post(c, {"object": "page"}).status_code == 200


def test_signature(db, tmp_path, fixed_clock):
    raw = json.dumps({"object": "instagram", "entry": []}).encode()
    good = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    with _client(db, tmp_path, secret=SECRET) as cl:
        assert _post(cl, raw).status_code == 403
        assert _post(cl, raw, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64}).status_code == 403
        assert _post(cl, raw + b" ", headers={"X-Hub-Signature-256": good}).status_code == 403
        assert _rows(db) == []
        assert _post(cl, raw, headers={"X-Hub-Signature-256": good}).status_code == 200
    assert [r[1] for r in _rows(db)] == ["instagram"]


# Pull для Manager

def test_pull_requires_key(c):
    assert c.get("/api/ext/v1/webhooks").status_code == 401
    assert c.get("/api/ext/v1/webhooks", headers={"X-API-Key": "wrong"}).status_code == 401


def test_pull_disabled_without_manager_key(db, tmp_path):
    cfg = Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="x" * 40, manager_api_key="")
    with TestClient(create_app(cfg, db)) as cl:
        assert cl.get("/api/ext/v1/webhooks", headers=H).status_code == 404


def test_pull_marks_processing(c, db):
    _queue(db, 3)
    r = c.get("/api/ext/v1/webhooks", headers=H)
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 3 and [w["payload"]["i"] for w in d["webhooks"]] == [0, 1, 2]
    assert set(d["webhooks"][0]) == {"uuid", "source", "payload", "received_at"}
    assert d["webhooks"][0]["received_at"] == (clock.now() - timedelta(minutes=60)).isoformat()
    assert {r[2] for r in _rows(db)} == {"processing"}
    assert c.get("/api/ext/v1/webhooks", headers=H).json() == {"webhooks": [], "count": 0}


def test_pull_limit(c, db):
    _queue(db, 5)
    d = c.get("/api/ext/v1/webhooks", params={"limit": 2}, headers=H).json()
    assert d["count"] == 2 and [w["payload"]["i"] for w in d["webhooks"]] == [0, 1]
    assert [r[2] for r in _rows(db)] == ["processing", "processing", "pending", "pending", "pending"]


@pytest.mark.parametrize("limit", [0, 101])
def test_pull_limit_bounds(c, limit):
    assert c.get("/api/ext/v1/webhooks", params={"limit": limit}, headers=H).status_code == 422


def test_pull_empty(c):
    assert c.get("/api/ext/v1/webhooks", headers=H).json() == {"webhooks": [], "count": 0}


def test_pull_resets_stuck(c, db):
    _queue(db, 1, status="processing", started=clock.now() - timedelta(minutes=6))
    _queue(db, 1, status="processing", started=clock.now() - timedelta(minutes=4))
    d = c.get("/api/ext/v1/webhooks", headers=H).json()
    assert d["count"] == 1
    with db.session() as s:
        rows = s.scalars(select(WebhookQueue).order_by(WebhookQueue.id)).all()
        assert [w.status for w in rows] == ["processing", "processing"]
        assert rows[0].processing_started_at == clock.now()
        assert rows[1].processing_started_at == clock.now() - timedelta(minutes=4)


# Ack від Manager

def _pulled_uuid(c, db):
    _queue(db, 1)
    return c.get("/api/ext/v1/webhooks", headers=H).json()["webhooks"][0]["uuid"]


def test_ack_requires_key(c, db):
    uuid = _pulled_uuid(c, db)
    assert c.post(f"/api/ext/v1/webhooks/{uuid}/ack", json={"status": "completed"}).status_code == 401


def test_ack_completed_idempotent(c, db):
    uuid = _pulled_uuid(c, db)
    for _ in range(2):
        r = c.post(f"/api/ext/v1/webhooks/{uuid}/ack", json={"status": "completed"}, headers=H)
        assert r.status_code == 200 and r.json() == {"uuid": uuid, "status": "completed"}
    [(_, _, status, retries, processed, _)] = _rows(db)
    assert (status, retries, processed) == ("completed", 0, clock.now())


def test_ack_failed_retries_then_fails(c, db):
    uuid = _pulled_uuid(c, db)
    for n in range(1, 5):
        r = c.post(f"/api/ext/v1/webhooks/{uuid}/ack", json={"status": "failed", "error": f"помилка {n}"}, headers=H)
        assert r.json() == {"uuid": uuid, "status": "pending"}
        [(_, _, status, retries, processed, err)] = _rows(db)
        assert (status, retries, processed, err) == ("pending", n, None, f"помилка {n}")
        assert c.get("/api/ext/v1/webhooks", headers=H).json()["count"] == 1
    r = c.post(f"/api/ext/v1/webhooks/{uuid}/ack", json={"status": "failed", "error": "остання"}, headers=H)
    assert r.json() == {"uuid": uuid, "status": "failed"}
    [(_, _, status, retries, processed, err)] = _rows(db)
    assert (status, retries, processed, err) == ("failed", 5, clock.now(), "остання")
    r = c.post(f"/api/ext/v1/webhooks/{uuid}/ack", json={"status": "failed"}, headers=H)
    assert r.json()["status"] == "failed" and _rows(db)[0][3] == 5
    assert c.get("/api/ext/v1/webhooks", headers=H).json()["count"] == 0


def test_ack_unknown_and_bad_status(c, db):
    assert c.post("/api/ext/v1/webhooks/00000000-0000-0000-0000-000000000000/ack", json={"status": "completed"}, headers=H).status_code == 404
    uuid = _pulled_uuid(c, db)
    assert c.post(f"/api/ext/v1/webhooks/{uuid}/ack", json={"status": "done"}, headers=H).status_code == 422
    assert c.post(f"/api/ext/v1/webhooks/{uuid}/ack", json={}, headers=H).status_code == 422
