from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .. import clock
from ..errors import Conflict, ValidationError

log = logging.getLogger("mds.gdrive")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_NAME = "MDS Connect — резервні копії"
FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass
class Resp:
    status: int
    body: bytes
    headers: dict

    def json(self) -> dict:
        return json.loads(self.body or b"{}")


def _urllib_http(method: str, url: str, headers: dict | None = None, data: bytes | None = None) -> Resp:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            return Resp(r.status, r.read(), {k.lower(): v for k, v in r.headers.items()})
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.read(), {k.lower(): v for k, v in (e.headers or {}).items()})


http: Callable[..., Resp] = _urllib_http


def client_id() -> str:
    return os.getenv("MDS_GDRIVE_CLIENT_ID", "")


def client_secret() -> str:
    return os.getenv("MDS_GDRIVE_CLIENT_SECRET", "")


def configured() -> bool:
    return bool(client_id() and client_secret())


def keep() -> int:
    try:
        return max(int(os.getenv("MDS_GDRIVE_KEEP", "180")), 1)
    except ValueError:
        return 180


def _state_path(data_dir: Path) -> Path:
    return data_dir / "gdrive.json"


def load_state(data_dir: Path) -> dict:
    p = _state_path(data_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def save_state(data_dir: Path, state: dict) -> None:
    p = _state_path(data_dir)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(p)


def status(data_dir: Path) -> dict:
    st = load_state(data_dir)
    return {
        "configured": configured(),
        "connected": bool(st.get("refresh_token")),
        "account": st.get("account"),
        "last_ok_at": st.get("last_ok_at"),
        "last_file": st.get("last_file"),
        "last_error": st.get("last_error"),
        "last_error_at": st.get("last_error_at"),
        "keep": keep(),
    }


def auth_url(redirect_uri: str, state: str) -> str:
    if not configured():
        raise Conflict("Google Drive не налаштовано на сервері (MDS_GDRIVE_CLIENT_ID / MDS_GDRIVE_CLIENT_SECRET)")
    q = {
        "client_id": client_id(), "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def _token_request(fields: dict) -> dict:
    r = http("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"},
             urllib.parse.urlencode(fields).encode())
    if r.status != 200:
        raise ValidationError(f"Google відхилив авторизацію ({r.status}): {r.body[:200].decode(errors='replace')}")
    return r.json()


def finish_auth(data_dir: Path, code: str, redirect_uri: str) -> dict:
    tok = _token_request({"code": code, "client_id": client_id(), "client_secret": client_secret(),
                          "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    if not tok.get("refresh_token"):
        raise ValidationError("Google не видав постійний доступ. Спробуйте підключити ще раз.")
    st = load_state(data_dir)
    st.update({"refresh_token": tok["refresh_token"], "folder_id": None, "last_error": None})
    st.pop("access_token", None)
    save_state(data_dir, st)
    access = _access_token(data_dir)
    about = http("GET", f"{API}/about?fields=user(emailAddress)", {"Authorization": f"Bearer {access}"})
    if about.status == 200:
        st = load_state(data_dir)
        st["account"] = about.json().get("user", {}).get("emailAddress")
        save_state(data_dir, st)
    _folder_id(data_dir, access)
    return status(data_dir)


def disconnect(data_dir: Path) -> None:
    st = load_state(data_dir)
    tok = st.get("refresh_token")
    if tok:
        try:
            http("POST", "https://oauth2.googleapis.com/revoke?" + urllib.parse.urlencode({"token": tok}),
                 {"Content-Type": "application/x-www-form-urlencoded"}, b"")
        except Exception:  # noqa: BLE001
            log.warning("Не вдалося відкликати токен Google")
    save_state(data_dir, {k: v for k, v in st.items() if k in ("last_ok_at", "last_file")})


def _access_token(data_dir: Path) -> str:
    st = load_state(data_dir)
    if st.get("access_token") and st.get("access_exp", 0) > time.time() + 60:
        return st["access_token"]
    if not st.get("refresh_token"):
        raise Conflict("Google Drive не підключено")
    tok = _token_request({"refresh_token": st["refresh_token"], "client_id": client_id(),
                          "client_secret": client_secret(), "grant_type": "refresh_token"})
    st["access_token"] = tok["access_token"]
    st["access_exp"] = time.time() + int(tok.get("expires_in", 3600))
    save_state(data_dir, st)
    return st["access_token"]


def _check(r: Resp, what: str) -> Resp:
    if r.status >= 300:
        raise RuntimeError(f"Google Drive: {what} — {r.status} {r.body[:300].decode(errors='replace')}")
    return r


def _folder_id(data_dir: Path, access: str) -> str:
    st = load_state(data_dir)
    if st.get("folder_id"):
        r = http("GET", f"{API}/files/{st['folder_id']}?fields=id,trashed", {"Authorization": f"Bearer {access}"})
        if r.status == 200 and not r.json().get("trashed"):
            return st["folder_id"]
    meta = json.dumps({"name": FOLDER_NAME, "mimeType": FOLDER_MIME}).encode()
    r = _check(http("POST", f"{API}/files?fields=id", {"Authorization": f"Bearer {access}",
                                                       "Content-Type": "application/json"}, meta), "створення папки")
    st = load_state(data_dir)
    st["folder_id"] = r.json()["id"]
    save_state(data_dir, st)
    return st["folder_id"]


def upload(data_dir: Path, path: Path) -> dict:
    access = _access_token(data_dir)
    folder = _folder_id(data_dir, access)
    meta = json.dumps({"name": path.name, "parents": [folder], "mimeType": "application/x-sqlite3"}).encode()
    data = path.read_bytes()
    start = _check(http("POST", f"{UPLOAD}?uploadType=resumable&fields=id,name,size",
                        {"Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8",
                         "X-Upload-Content-Type": "application/x-sqlite3", "X-Upload-Content-Length": str(len(data))},
                        meta), "початок завантаження")
    loc = start.headers.get("location")
    if not loc:
        raise RuntimeError("Google Drive не повернув адресу для завантаження")
    done = _check(http("PUT", loc, {"Content-Type": "application/x-sqlite3", "Content-Length": str(len(data))}, data),
                  "завантаження файлу")
    info = done.json()
    _rotate(access, folder)
    st = load_state(data_dir)
    st.update({"last_ok_at": clock.now().isoformat(timespec="seconds"), "last_file": info.get("name", path.name),
               "last_error": None, "last_error_at": None})
    save_state(data_dir, st)
    return info


def _rotate(access: str, folder: str) -> None:
    q = urllib.parse.urlencode({
        "q": f"'{folder}' in parents and trashed = false and name contains 'mds-'",
        "orderBy": "createdTime desc", "fields": "files(id,name)", "pageSize": "1000",
    })
    r = http("GET", f"{API}/files?{q}", {"Authorization": f"Bearer {access}"})
    if r.status != 200:
        log.warning("Не вдалося отримати список копій на диску: %s", r.status)
        return
    for f in r.json().get("files", [])[keep():]:
        http("DELETE", f"{API}/files/{f['id']}", {"Authorization": f"Bearer {access}"})


def record_error(data_dir: Path, err: str) -> bool:
    st = load_state(data_dir)
    now = clock.now()
    last = st.get("last_alert_at")
    alert = not last or now - datetime.fromisoformat(last) >= timedelta(hours=24)
    st.update({"last_error": err[:500], "last_error_at": now.isoformat(timespec="seconds")})
    if alert:
        st["last_alert_at"] = now.isoformat(timespec="seconds")
    save_state(data_dir, st)
    return alert
