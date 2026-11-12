from __future__ import annotations

import logging
import time
from datetime import date

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import Field
from sqlalchemy.orm import Session

from ... import __version__, perf
from ...errors import Conflict, ValidationError
from ...models import Role, User
from ...services import backup, bugreport, gdrive, journal, sync
from ...services import journal_feed as journal_feed_svc
from ...services.common import Actor, all_settings, update_settings
from ...services.common import audit as write_audit
from ..schemas import BaseModel
from ..deps import get_actor, get_session

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/settings")
def get_settings(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return all_settings(s)


@router.put("/settings")
def put_settings(data: dict, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return update_settings(s, actor, data)


@router.get("/journal/ledger")
def ledger(ref_type: str | None = None, ref_id: int | None = None, limit: int = 200,
           actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return journal.ledger_list(s, actor, ref_type=ref_type, ref_id=ref_id, limit=min(limit, 1000))


@router.get("/journal/feed")
def journal_feed(date_from: date, date_to: date | None = None, category: str | None = None, q: str | None = None,
                 limit: int = 300, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    if date_to and (date_to - date_from).days > 366:
        raise ValidationError("Період журналу — не більше року")
    return journal_feed_svc.feed(s, actor, date_from=date_from, date_to=date_to or date_from, category=category,
                                 q=q, limit=min(max(limit, 1), 1000))


@router.get("/journal/audit")
def audit(entity: str | None = None, entity_id: int | None = None, limit: int = 200,
          actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return journal.audit_list(s, actor, entity=entity, entity_id=entity_id, limit=min(limit, 1000))


@router.get("/backups")
def list_backups(request: Request, actor: Actor = Depends(get_actor)):
    actor.require(Role.DIRECTOR)
    return backup.list_backups(request.app.state.config.backup_dir)


@router.post("/backups", status_code=201)
def create_backup(request: Request, actor: Actor = Depends(get_actor)):
    actor.require(Role.DIRECTOR)
    cfg = request.app.state.config
    path = backup.make_backup(cfg.database_url, cfg.backup_dir, cfg.backup_keep_days)
    return {"name": path.name}


@router.get("/backups/{name}")
def download_backup(name: str, request: Request, actor: Actor = Depends(get_actor)):
    actor.require(Role.DIRECTOR)
    path = backup.backup_file(request.app.state.config.backup_dir, name)
    return FileResponse(path, media_type="application/x-sqlite3", filename=name)


class BugReportIn(BaseModel):
    text: str
    page: str | None = None
    context: dict | None = None
    image: str | None = Field(default=None, max_length=7_500_000)


@router.post("/bug-report", status_code=201)
def bug_report(body: BugReportIn, request: Request, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return bugreport.create(s, actor, request.app.state.config.data_dir, text=body.text, page=body.page,
                            context=body.context, image_b64=body.image)


class RumItem(BaseModel):
    name: str = Field(max_length=40)
    ms: float = Field(ge=0, le=300000)


class RumIn(BaseModel):
    items: list[RumItem] = Field(max_length=30)


@router.post("/perf/rum", status_code=204)
def perf_rum(body: RumIn, actor: Actor = Depends(get_actor)):
    for it in body.items:
        if perf.CLIENT_NAME.match(it.name):
            perf.record("client", it.name, it.ms, perf.SLOW_CLIENT_MS)


@router.get("/perf")
def perf_stats(actor: Actor = Depends(get_actor)):
    actor.require(Role.DIRECTOR)
    return perf.snapshot()


@router.get("/health")
def health():
    return {"ok": True, "version": __version__}


def _redirect_uri(request: Request) -> str:
    return str(request.url_for("gdrive_callback"))


@router.get("/gdrive")
def gdrive_status(request: Request, actor: Actor = Depends(get_actor)):
    actor.require(Role.DIRECTOR)
    return gdrive.status(request.app.state.config.data_dir)


@router.post("/gdrive/connect")
def gdrive_connect(request: Request, actor: Actor = Depends(get_actor)):
    actor.require(Role.DIRECTOR)
    cfg = request.app.state.config
    now = int(time.time())
    state = jwt.encode({"sub": str(actor.user_id), "purpose": "gdrive", "iat": now, "exp": now + 900},
                       cfg.secret_key, algorithm="HS256")
    return {"url": gdrive.auth_url(_redirect_uri(request), state)}


@router.get("/gdrive/callback", name="gdrive_callback", include_in_schema=False)
def gdrive_callback(request: Request, state: str = "", code: str = "", error: str = "",
                    s: Session = Depends(get_session)):
    cfg = request.app.state.config
    try:
        payload = jwt.decode(state, cfg.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        payload = {}
    u = s.get(User, int(payload["sub"])) if payload.get("purpose") == "gdrive" and payload.get("sub") else None
    if u is None or u.role != Role.DIRECTOR or not u.is_active:
        return RedirectResponse("/#/settings?gdrive=denied", status_code=303)
    if error or not code:
        return RedirectResponse("/#/settings?gdrive=cancelled", status_code=303)
    try:
        st = gdrive.finish_auth(cfg.data_dir, code, _redirect_uri(request))
    except Exception:  # noqa: BLE001
        logging.getLogger("mds").exception("Google Drive: помилка підключення")
        return RedirectResponse("/#/settings?gdrive=error", status_code=303)
    write_audit(s, Actor.of(u), "gdrive", u.id, "connect", None, {"account": st.get("account")})
    return RedirectResponse("/#/settings?gdrive=ok", status_code=303)


@router.post("/gdrive/backup-now")
def gdrive_backup_now(request: Request, actor: Actor = Depends(get_actor)):
    actor.require(Role.DIRECTOR)
    cfg = request.app.state.config
    res = backup.backup_cycle(cfg, request.app.state.db)
    if res.get("drive_error"):
        raise Conflict(f"Локальну копію створено, але Google Drive повернув помилку: {res['drive_error'][:300]}")
    return {**res, "status": gdrive.status(cfg.data_dir)}


@router.post("/gdrive/disconnect")
def gdrive_disconnect(request: Request, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    actor.require(Role.DIRECTOR)
    gdrive.disconnect(request.app.state.config.data_dir)
    write_audit(s, actor, "gdrive", actor.user_id or 0, "disconnect", None, None)
    return gdrive.status(request.app.state.config.data_dir)


@router.get("/sync")
def sync_changes(since: int | None = None, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return sync.changes(s, actor, since)


@router.get("/sms/status")
def sms_status(actor: Actor = Depends(get_actor)):
    from ...services import sms as sms_svc

    actor.require(Role.DIRECTOR)
    return {"configured": sms_svc.configured()}
