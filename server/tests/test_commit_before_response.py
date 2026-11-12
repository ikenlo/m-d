"""Транзакція фіксується ДО відповіді: клієнт, що отримав 200, одразу читає вже збережені дані."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from mds.api.app import create_app
from mds.config import Config


def test_commit_happens_before_response_is_sent(db, seed, tmp_path, monkeypatch):
    events: list[str] = []
    orig_commit = Session.commit

    def commit(self):
        events.append("commit")
        return orig_commit(self)

    monkeypatch.setattr(Session, "commit", commit)
    app = create_app(Config(data_dir=tmp_path / "data", database_url=db.url,
                            secret_key="test-secret-key-for-unit-tests-only-0123456789"), db)

    async def spy(scope, receive, send):
        async def send2(msg):
            if msg["type"] == "http.response.start":
                events.append(f"response:{scope['path']}")
            await send(msg)
        await app(scope, receive, send2)

    with TestClient(spy) as c:
        token = c.post("/api/auth/login", json={"username": "director", "password": "Director123"}).json()["token"]
        events.clear()
        r = c.put("/api/settings", json={"salon_name": "Тест"}, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
    assert "commit" in events and events.index("commit") < events.index("response:/api/settings"), events
