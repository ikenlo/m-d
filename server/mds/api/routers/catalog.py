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


# ----------------------------------------------------------------- послуги
@router.get("/services")
def list_services(all: bool = False, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [catalog.service_public(x) for x in catalog.list_services(s, active_only=not all)]


@router.post("/services", status_code=201)
def create_service(body: ServiceIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.service_public(catalog.create_service(s, actor, body.model_dump()))


@router.patch("/services/{service_id}")
def update_service(service_id: int, body: ServicePatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.service_public(catalog.update_service(s, actor, service_id, body.model_dump(exclude_unset=True)))


# --------------------------------------------------------------- матеріали
@router.get("/materials")
def list_materials(all: bool = False, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return [catalog.material_public(x) for x in catalog.list_materials(s, active_only=not all)]


@router.post("/materials", status_code=201)
def create_material(body: MaterialIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.material_public(catalog.create_material(s, actor, body.model_dump()))


@router.patch("/materials/{material_id}")
def update_material(material_id: int, body: MaterialPatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.material_public(catalog.update_material(s, actor, material_id, body.model_dump(exclude_unset=True)))


# ----------------------------------------------------------------- клієнти
@router.get("/clients")
def search_clients(q: str = "", actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    actor.require(Role.DIRECTOR, Role.ADMIN, Role.MASTER)
    master_id = actor.user_id if actor.is_master else None
    return [catalog.client_public(c) for c in catalog.search_clients(s, q, master_id=master_id)]


@router.post("/clients", status_code=201)
def create_client(body: ClientIn, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    c = catalog.get_or_create_client(s, actor, body.name, body.phone)
    if body.notes:
        c.notes = body.notes
    return catalog.client_public(c)


@router.patch("/clients/{client_id}")
def update_client(client_id: int, body: ClientPatch, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    return catalog.client_public(catalog.update_client(s, actor, client_id, body.model_dump(exclude_unset=True)))


@router.get("/clients/{client_id}")
def get_client(client_id: int, actor: Actor = Depends(get_actor), s: Session = Depends(get_session)):
    """Картка клієнта з історією відвідувань. Майстер бачить лише свої візити."""
    c = get_or_404(s, Client, client_id, "Клієнта")
    history = bookings.client_history(s, c.id)
    if actor.is_master:
        history = [a for a in history if a.master_id == actor.user_id]
        if not history:
            raise NotFound(f"Клієнта #{client_id} не знайдено")
    return {**catalog.client_public(c), "history": [bookings.appt_public(a) for a in history]}
