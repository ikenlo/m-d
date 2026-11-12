from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services import security_journal as sj
from ...services.common import Actor
from ..deps import get_actor, get_session

router = APIRouter(prefix="/api", tags=["security"])


@router.get("/security/logins")
def logins(user_id: int | None = None, success: bool | None = None, limit: int = 200,
           actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return sj.list_logins(s, actor, user_id=user_id, success=success, limit=limit)


@router.post("/users/{user_id}/revoke-sessions")
def revoke(user_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    sj.revoke_sessions(s, actor, user_id)
    return {"ok": True}


@router.post("/users/{user_id}/reset-finance-pin")
def reset_pin(user_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    sj.reset_finance_pin(s, actor, user_id)
    return {"ok": True}
