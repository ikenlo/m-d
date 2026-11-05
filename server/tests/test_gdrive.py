import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from mds.api.app import create_app
from mds.config import Config
from mds.models import Notification
from mds.services import backup, gdrive
from mds.services.gdrive import Resp


class FakeGoogle:
    def __init__(self, keep_files=0):
        self.calls = []
        self.files = [{"id": f"old{i}", "name": f"mds-old-{i}.db"} for i in range(keep_files)]
        self.deleted = []
        self.uploaded = []
        self.fail_upload = False

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append((method, url))
        if url.startswith(gdrive.TOKEN_URL):
            f = parse_qs(data.decode())
            if f["grant_type"] == ["authorization_code"]:
                assert f["code"] == ["good-code"]
                return Resp(200, json.dumps({"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600}).encode(), {})
            assert f["refresh_token"] == ["rt1"]
            return Resp(200, json.dumps({"access_token": "at2", "expires_in": 3600}).encode(), {})
        assert headers.get("Authorization", "").startswith("Bearer ") or "upload-session" in url or "revoke" in url
        if "/about" in url:
            return Resp(200, b'{"user":{"emailAddress":"salon@example.com"}}', {})
        if method == "POST" and url.startswith(gdrive.API + "/files?"):
            return Resp(200, b'{"id":"folder1"}', {})
        if method == "GET" and "/files/folder1" in url:
            return Resp(200, b'{"id":"folder1","trashed":false}', {})
        if method == "POST" and url.startswith(gdrive.UPLOAD):
            if self.fail_upload:
                return Resp(403, b'{"error":"quota"}', {})
            meta = json.loads(data)
            assert meta["parents"] == ["folder1"]
            return Resp(200, b"", {"location": "https://upload-session/1"})
        if method == "PUT" and url == "https://upload-session/1":
            assert data[:15] == b"SQLite format 3"
            name = f"mds-new-{len(self.uploaded)}.db"
            self.uploaded.append(name)
            self.files.insert(0, {"id": f"new{len(self.uploaded)}", "name": name})
            return Resp(200, json.dumps({"id": "x", "name": name}).encode(), {})
        if method == "GET" and url.startswith(gdrive.API + "/files?"):
            return Resp(200, json.dumps({"files": self.files}).encode(), {})
        if method == "DELETE":
            self.deleted.append(url.rsplit("/", 1)[1])
            return Resp(204, b"", {})
        if "revoke" in url:
            return Resp(200, b"", {})
        raise AssertionError(f"неочікуваний запит {method} {url}")


@pytest.fixture
def env(db, seed, tmp_path, monkeypatch):
    monkeypatch.setenv("MDS_GDRIVE_CLIENT_ID", "cid")
    monkeypatch.setenv("MDS_GDRIVE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MDS_GDRIVE_KEEP", "3")
    fake = FakeGoogle(keep_files=4)
    monkeypatch.setattr(gdrive, "http", fake)
    cfg = Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="test-secret-key-for-unit-tests-only-0123456789")
    with TestClient(create_app(cfg, db), follow_redirects=False) as c:
        tok = c.post("/api/auth/login", json={"username": "director", "password": "Director123"}).json()["token"]
        yield c, cfg, fake, {"Authorization": f"Bearer {tok}"}


def test_connect_upload_rotate(env, db):
    c, cfg, fake, hd = env
    assert c.get("/api/gdrive", headers=hd).json()["connected"] is False
    url = c.post("/api/gdrive/connect", headers=hd).json()["url"]
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == [gdrive.SCOPE] and q["access_type"] == ["offline"]
    assert q["redirect_uri"][0].endswith("/api/gdrive/callback")
    r = c.get("/api/gdrive/callback", params={"state": "fake", "code": "good-code"})
    assert r.status_code == 303 and r.headers["location"].endswith("gdrive=denied")
    r = c.get("/api/gdrive/callback", params={"state": q["state"][0], "code": "good-code"})
    assert r.status_code == 303 and r.headers["location"].endswith("gdrive=ok")
    st = c.get("/api/gdrive", headers=hd).json()
    assert st["connected"] and st["account"] == "salon@example.com"
    assert "refresh_token" not in json.dumps(st)

    r = c.post("/api/gdrive/backup-now", headers=hd)
    assert r.status_code == 200, r.text
    assert fake.uploaded and r.json()["status"]["last_file"] == fake.uploaded[0]
    assert fake.deleted == ["old2", "old3"]
    assert (cfg.data_dir / "gdrive.json").stat().st_mode & 0o077 == 0


def test_master_cannot_manage_drive(env):
    c, cfg, fake, hd = env
    tok = c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]
    m = {"Authorization": f"Bearer {tok}"}
    for method, url in (("get", "/api/gdrive"), ("post", "/api/gdrive/connect"), ("post", "/api/gdrive/backup-now"),
                        ("post", "/api/gdrive/disconnect")):
        assert getattr(c, method)(url, headers=m).status_code == 403


def test_upload_error_alerts_director_once(env, db, seed):
    c, cfg, fake, hd = env
    with db.session() as s:
        from mds.models import User
        s.get(User, seed["director"]).telegram_chat_id = 555
    gdrive.save_state(cfg.data_dir, {"refresh_token": "rt1", "folder_id": "folder1"})
    fake.fail_upload = True
    r1 = backup.backup_cycle(cfg, db)
    r2 = backup.backup_cycle(cfg, db)
    assert r1["local"] and "403" in r1["drive_error"] and r2["drive_error"]
    with db.session() as s:
        alerts = s.query(Notification).filter_by(chat_id=555).all()
        assert len(alerts) == 1 and "Google Drive" in alerts[0].text
    assert c.post("/api/gdrive/backup-now", headers=hd).status_code == 409
    assert c.get("/api/gdrive", headers=hd).json()["last_error"]


def test_not_connected_makes_local_only(env, db):
    c, cfg, fake, hd = env
    res = backup.backup_cycle(cfg, db)
    assert res["local"] and res["drive"] is None and not fake.uploaded
