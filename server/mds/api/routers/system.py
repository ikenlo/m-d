from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...models import Role
from ...services import backup, journal
from ...services.common import Actor, all_settings, update_settings
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
    path = backup.make_backup(cfg.database_url, cfg.backup_dir, cfg.backup_keep)
    return {"name": path.name}


@router.get("/health")
def health():
    return {"ok": True}
