from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, Forbidden, NotFound, ValidationError
from ..models import PermissionRequest, Role
from .common import Actor, audit, get_setting, set_setting

MAX_DAYS = 14
ACTIONS = {
    "cancel_order": "Скасувати замовлення",
    "refund": "Повернення",
    "correction": "Сторно / виправлення",
    "force_close_shift": "Примусово закрити зміну",
    "materials_after_close": "Додати матеріали після закриття зміни",
    "inventory": "Інвентаризація",
    "material_receipt": "Прихід матеріалів",
}


def status(s: Session) -> dict:
    active = bool(get_setting(s, "vacation_mode_active"))
    exp = get_setting(s, "vacation_mode_expires_at")
    if active and exp and datetime.fromisoformat(exp) <= clock.now():
        active = False
    return {"active": active, "expires_at": exp if active else None, "actions": ACTIONS}


def set_mode(s: Session, actor: Actor, active: bool, days: int = MAX_DAYS) -> dict:
    actor.require(Role.DIRECTOR)
    if active:
        if not (1 <= int(days) <= MAX_DAYS):
            raise ValidationError(f"Режим відпустки — від 1 до {MAX_DAYS} днів")
        exp = (clock.now() + timedelta(days=int(days))).isoformat()
        set_setting(s, actor, "vacation_mode_active", True)
        set_setting(s, actor, "vacation_mode_expires_at", exp)
    else:
        _turn_off(s, actor)
    audit(s, actor, "settings", 0, "vacation_mode", None, {"active": active, "days": days if active else None})
    return status(s)


def _turn_off(s: Session, actor: Actor) -> int:
    set_setting(s, actor, "vacation_mode_active", False)
    set_setting(s, actor, "vacation_mode_expires_at", None)
    res = s.execute(update(PermissionRequest).where(PermissionRequest.status == "pending")
                    .values(status="expired", resolved_at=clock.now(), note="Режим відпустки вимкнено")
                    .execution_options(synchronize_session=False))
    return res.rowcount


def auto_off(s: Session) -> bool:
    if bool(get_setting(s, "vacation_mode_active")) and not status(s)["active"]:
        _turn_off(s, Actor.system())
        audit(s, Actor.system(), "settings", 0, "vacation_mode_auto_off", None, {"active": False})
        return True
    return False


def request_public(r: PermissionRequest) -> dict:
    return {"id": r.id, "user_id": r.user_id, "user_name": r.user.full_name if r.user else None, "action": r.action,
            "action_label": ACTIONS.get(r.action, r.action), "payload": r.payload_json, "status": r.status,
            "created_at": r.created_at.isoformat(), "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            "resolved_by": r.resolved_by, "note": r.note, "result": r.result_json}


def _validate_payload(action: str, p: dict) -> None:
    need = {"cancel_order": ("order_id", "reason"), "refund": ("order_id", "reason"), "correction": ("order_id", "data", "reason"),
            "force_close_shift": (), "materials_after_close": ("order_id", "materials"), "inventory": ("items",),
            "material_receipt": ("qty",)}[action]
    miss = [k for k in need if not p.get(k)]
    if miss:
        raise ValidationError("Бракує даних запиту: " + ", ".join(miss))
    if action == "material_receipt" and not (p.get("material_id") or p.get("product_id")):
        raise ValidationError("Вкажіть матеріал або товар")


def create_request(s: Session, actor: Actor, action: str, payload: dict | None) -> PermissionRequest:
    from . import alerts

    actor.require(Role.MASTER)
    if not status(s)["active"]:
        raise Forbidden("Запит дозволу доступний лише в режимі «Ми у відпустці»")
    if action not in ACTIONS:
        raise ValidationError("Цю дію не можна запитати")
    payload = dict(payload or {})
    _validate_payload(action, payload)
    if action == "force_close_shift":
        payload["master_id"] = actor.user_id
    r = PermissionRequest(user_id=actor.user_id, action=action, payload_json=payload, status="pending", created_at=clock.now())
    s.add(r)
    s.flush()
    s.refresh(r)
    audit(s, actor, "permission_request", r.id, "create", None, request_public(r))
    alerts.raise_alert(s, "permission_request",
                       f"🙋 {r.user.full_name} просить дозвіл: {ACTIONS[action]} (запит #{r.id})\n{_describe(payload)}",
                       entity="permission_request", entity_id=r.id,
                       buttons=[[("✅ Дозволити", f"s:pr:ok:{r.id}"), ("⛔ Відхилити", f"s:pr:no:{r.id}")]])
    return r


def _describe(p: dict) -> str:
    parts = [f"{k}: {v}" for k, v in p.items() if k in ("order_id", "reason", "qty", "material_id", "product_id")]
    return "; ".join(parts)


def _get(s: Session, rid: int) -> PermissionRequest:
    r = s.get(PermissionRequest, rid)
    if r is None:
        raise NotFound("Запит не знайдено")
    return r


def withdraw(s: Session, actor: Actor, rid: int) -> PermissionRequest:
    r = _get(s, rid)
    if r.user_id != actor.user_id:
        raise Forbidden("Відкликати можна лише власний запит")
    _claim(s, rid, "withdrawn", actor, "Відкликано майстром")
    s.refresh(r)
    audit(s, actor, "permission_request", r.id, "withdraw", None, {"status": r.status})
    return r


def _claim(s: Session, rid: int, new_status: str, actor: Actor, note: str | None) -> None:
    from ..models import Notification

    now = clock.now()
    res = s.execute(update(PermissionRequest).where(PermissionRequest.id == rid, PermissionRequest.status == "pending")
                    .values(status=new_status, resolved_at=now, resolved_by=actor.user_id, note=note)
                    .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise Conflict("Запит уже вирішено або відкликано", code="already_resolved")
    s.execute(update(Notification).where(Notification.kind == "permission_request",
                                         Notification.group_key.like(f"permission_request:permission_request:{rid}:%"),
                                         Notification.handled_at.is_(None))
              .values(handled_at=now, handled_by=actor.user_id).execution_options(synchronize_session=False))


def reject(s: Session, actor: Actor, rid: int, note: str | None = None) -> PermissionRequest:
    actor.require(Role.DIRECTOR)
    r = _get(s, rid)
    _claim(s, rid, "rejected", actor, note)
    s.refresh(r)
    audit(s, actor, "permission_request", r.id, "reject", None, {"status": "rejected"}, note)
    _tell_master(s, r, f"⛔ Директор відхилив запит #{r.id}: {ACTIONS.get(r.action, r.action)}" + (f"\n{note}" if note else ""))
    return r


def approve(s: Session, actor: Actor, rid: int, note: str | None = None) -> PermissionRequest:
    actor.require(Role.DIRECTOR)
    r = _get(s, rid)
    _claim(s, rid, "approved", actor, note)
    s.refresh(r)
    result = _execute(s, actor, r)
    r.result_json = result
    audit(s, actor, "permission_request", r.id, "approve", None, {"status": "approved", "result": result}, note)
    _tell_master(s, r, f"✅ Дозволено й виконано: {ACTIONS.get(r.action, r.action)} (запит #{r.id})")
    return r


def _tell_master(s: Session, r: PermissionRequest, text: str) -> None:
    from . import notifications

    if r.user and r.user.telegram_chat_id:
        notifications.enqueue(s, r.user.telegram_chat_id, text)


def _execute(s: Session, director: Actor, r: PermissionRequest) -> dict:
    from . import orders, products, stock

    p = r.payload_json or {}
    a = r.action
    if a == "cancel_order":
        return {"order_id": orders.cancel_order(s, director, int(p["order_id"]), p["reason"]).id}
    if a == "refund":
        return {"order_id": orders.refund_order(s, director, int(p["order_id"]), p["reason"]).id}
    if a == "correction":
        return {"order_id": orders.correct_order(s, director, int(p["order_id"]), dict(p["data"]), p["reason"]).id}
    if a == "materials_after_close":
        o = orders.correct_order(s, director, int(p["order_id"]), {"materials": p["materials"]},
                                 p.get("reason") or "Матеріали після закриття зміни (дозвіл директора)")
        return {"order_id": o.id, "materials_cost": o.materials_cost}
    if a == "force_close_shift":
        return {"shift_id": orders.close_shift(s, director, int(p["master_id"]), force=True).id}
    if a == "inventory":
        done = []
        for it in p["items"]:
            if it.get("product_id"):
                products.inventory(s, director, int(it["product_id"]), int(it["actual"]), p.get("note"))
            else:
                stock.adjust(s, director, int(it["material_id"]), it["actual"], p.get("note"))
            done.append(it)
        return {"items": len(done)}
    if a == "material_receipt":
        if p.get("product_id"):
            products.receive(s, director, int(p["product_id"]), p["qty"], cost=p.get("cost"), note=p.get("note"))
        else:
            stock.receive(s, director, int(p["material_id"]), p["qty"], cost=p.get("cost"), note=p.get("note"))
        return {"ok": True}
    raise ValidationError("Невідома дія")


def list_requests(s: Session, actor: Actor, status_filter: str | None = None) -> list[PermissionRequest]:
    q = select(PermissionRequest)
    if actor.is_master:
        q = q.where(PermissionRequest.user_id == actor.user_id)
    else:
        actor.require(Role.DIRECTOR)
    if status_filter:
        q = q.where(PermissionRequest.status == status_filter)
    return list(s.scalars(q.order_by(PermissionRequest.id.desc()).limit(100)))
