from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Role, User
from ...services import users
from ...services.common import Actor
from ..deps import get_actor, get_session
from ..schemas import PasswordIn, UserIn, UserPatch

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
def list_users(actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    if actor.is_director:
        return [users.user_public(u) for u in s.scalars(select(User).order_by(User.role, User.full_name))]
    return [
        {"id": u.id, "full_name": u.full_name, "role": u.role, "service_ids": sorted(x.id for x in u.services),
         "schedule": u.schedule}
        for u in users.list_masters(s)
    ]


@router.post("", status_code=201)
def create_user(body: UserIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    actor.require(Role.DIRECTOR)
    return users.user_public(users.create_user(s, actor, **body.model_dump()))


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserPatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return users.user_public(users.update_user(s, actor, user_id, body.model_dump(exclude_unset=True)))


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, body: PasswordIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    users.reset_password(s, actor, user_id, body.password)
    return {"ok": True}


@router.post("/{user_id}/telegram-code")
def telegram_code(user_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session, scope="function")):
    return {"code": users.new_telegram_link_code(s, actor, user_id)}
