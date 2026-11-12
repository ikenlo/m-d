from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...services import api_keys
from ...services.common import Actor
from ..deps import get_actor, get_session

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


class ApiKeyIn(BaseModel):
    name: str = Field(max_length=80)
    scope: str = "read"


@router.get("")
def list_keys(actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [api_keys.key_public(k) for k in api_keys.list_keys(s, actor)]


@router.post("", status_code=201)
def create_key(body: ApiKeyIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    k, raw = api_keys.create_key(s, actor, body.name, body.scope)
    return api_keys.key_public(k) | {"key": raw}


@router.post("/{key_id}/revoke")
def revoke_key(key_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return api_keys.key_public(api_keys.revoke_key(s, actor, key_id))
