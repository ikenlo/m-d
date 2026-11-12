from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...errors import NotFound
from ...models import Client, Role
from ...services import bookings, catalog
from ...services.common import Actor, get_or_404
from ..deps import get_actor, get_session
from ..schemas import ClientIn, ClientPatch, MaterialIn, MaterialPatch, ServiceIn, ServicePatch

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/services")
def list_services(all: bool = False, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [catalog.service_public(x) for x in catalog.list_services(s, active_only=not all)]


@router.post("/services", status_code=201)
def create_service(body: ServiceIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.service_public(catalog.create_service(s, actor, body.model_dump()))


@router.patch("/services/{service_id}")
def update_service(service_id: int, body: ServicePatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.service_public(catalog.update_service(s, actor, service_id, body.model_dump(exclude_unset=True)))


@router.get("/materials")
def list_materials(all: bool = False, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [catalog.material_public(x) for x in catalog.list_materials(s, active_only=not all)]


@router.post("/materials", status_code=201)
def create_material(body: MaterialIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.material_public(catalog.create_material(s, actor, body.model_dump()))


@router.patch("/materials/{material_id}")
def update_material(material_id: int, body: MaterialPatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.material_public(catalog.update_material(s, actor, material_id, body.model_dump(exclude_unset=True)))


@router.get("/clients")
def search_clients(q: str = "", actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    actor.require(Role.DIRECTOR, Role.MASTER)
    master_id = actor.user_id if actor.is_master else None
    return [catalog.client_public(c) for c in catalog.search_clients(s, q, master_id=master_id)]


@router.post("/clients", status_code=201)
def create_client(body: ClientIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.client_public(catalog.create_client(s, actor, body.name, body.phone, body.notes))


@router.post("/clients/purge-no-phone")
def purge_no_phone(dry_run: bool = False, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.purge_clients_without_phone(s, actor, dry_run=dry_run)


@router.patch("/clients/{client_id}")
def update_client(client_id: int, body: ClientPatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.client_public(catalog.update_client(s, actor, client_id, body.model_dump(exclude_unset=True)))


@router.get("/clients/{client_id}")
def get_client(client_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    c = get_or_404(s, Client, client_id, "Клієнта")
    history = bookings.client_history(s, c.id)
    if actor.is_master:
        history = [a for a in history if a.master_id == actor.user_id]
        if not history:
            raise NotFound(f"Клієнта #{client_id} не знайдено")
    fav = bookings.favorite_master(s, c.id)
    return {**catalog.client_public(c), "history": [bookings.appt_public(a) for a in history],
            "favorite_master": {"id": fav[0].id, "full_name": fav[0].full_name, "visits": fav[1]} if fav else None}
