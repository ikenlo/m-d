"""Читання журналів: фінансові операції та історія змін."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, LedgerEntry, Role
from .common import Actor


def ledger_list(s: Session, actor: Actor, *, ref_type: str | None = None, ref_id: int | None = None, limit: int = 200) -> list[dict]:
    actor.require(Role.DIRECTOR)
    q = select(LedgerEntry)
    if ref_type:
        q = q.where(LedgerEntry.ref_type == ref_type)
    if ref_id:
        q = q.where(LedgerEntry.ref_id == ref_id)
    return [
        {
            "id": e.id, "ts": e.ts.isoformat(), "kind": e.kind, "ref_type": e.ref_type, "ref_id": e.ref_id,
            "master_id": e.master_id, "amount": e.amount, "master_amount": e.master_amount,
            "salon_amount": e.salon_amount, "materials_amount": e.materials_amount,
            "user_id": e.user_id, "details": e.details,
        }
        for e in s.scalars(q.order_by(LedgerEntry.id.desc()).limit(limit))
    ]


def audit_list(s: Session, actor: Actor, *, entity: str | None = None, entity_id: int | None = None, limit: int = 200) -> list[dict]:
    actor.require(Role.DIRECTOR)
    q = select(AuditLog)
    if entity:
        q = q.where(AuditLog.entity == entity)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    return [
        {
            "id": a.id, "ts": a.ts.isoformat(), "actor": a.actor, "entity": a.entity, "entity_id": a.entity_id,
            "action": a.action, "before": a.before, "after": a.after, "reason": a.reason,
        }
        for a in s.scalars(q.order_by(AuditLog.id.desc()).limit(limit))
    ]
