from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services import callbacks
from ...services.common import Actor
from ..deps import get_actor, get_session

router = APIRouter(prefix="/api/callbacks", tags=["callbacks"])


@router.get("")
def list_callbacks(status: str | None = "new", actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [callbacks.callback_public(r) for r in callbacks.list_requests(s, actor, status or None)]


@router.post("/{request_id}/done")
def mark_done(request_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return callbacks.callback_public(callbacks.mark_done(s, actor, request_id))
