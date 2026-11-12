from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Forbidden, NotFound, ValidationError
from ..models import Material, Notification, Order, OrderStatus, Product, Role, Shift, ShiftStatus, User
from ..money import fmt
from .common import Actor, get_setting

CRITICAL = ("salon_negative", "master_negative_balance", "permission_request")
IMPORTANT = ("earnings_change", "unpaid_overdue", "shift_not_closed")
INFO = ("profitability_low", "negative_stock", "no_show", "new_order", "cancellation")
KINDS = CRITICAL + IMPORTANT + INFO
LABELS = {
    "salon_negative": "Салон у мінусі по замовленню", "master_negative_balance": "Від'ємний баланс майстра",
    "permission_request": "Запит дозволу", "earnings_change": "Зміна заробітку > 50 ₴",
    "unpaid_overdue": "Неоплачені > 3 днів", "shift_not_closed": "Зміна не закрита > 12 год",
    "profitability_low": "Рентабельність < 18%", "negative_stock": "Від'ємний залишок",
    "no_show": "Неявка", "new_order": "Нове замовлення", "cancellation": "Скасування",
}
EARNINGS_CHANGE_THRESHOLD = 5000
UNPAID_DAYS = 3
SHIFT_HOURS = 12
PROFITABILITY_MIN_BP = 1800


def level_of(kind: str) -> str:
    return "critical" if kind in CRITICAL else "important" if kind in IMPORTANT else "info"


def enabled(s: Session, kind: str) -> bool:
    if kind in CRITICAL:
        return True
    return kind not in (get_setting(s, "alerts_disabled") or [])


def _directors(s: Session) -> list[User]:
    return list(s.scalars(select(User).where(User.role == Role.DIRECTOR, User.is_active.is_(True))))


def raise_alert(s: Session, kind: str, text: str, *, entity: str | None = None, entity_id: int | None = None,
                master_id: int | None = None, to_directors: bool = True, exclude_user_id: int | None = None,
                dedupe: str | None = None, buttons: list | None = None) -> int:
    if kind not in KINDS:
        raise ValueError(kind)
    if not enabled(s, kind):
        return 0
    level = level_of(kind)
    recipients: list[User] = _directors(s) if to_directors else []
    if master_id:
        m = s.get(User, master_id)
        if m is not None and m.is_active and m not in recipients:
            recipients.append(m)
    group = f"{kind}:{entity or '-'}:{entity_id or 0}:{secrets.token_hex(4)}"
    n = 0
    for u in recipients:
        if u.id == exclude_user_id:
            continue
        dkey = f"{kind}:{dedupe}" if dedupe else None
        if dkey and s.scalar(select(Notification.id).where(Notification.dedupe_key == dkey, Notification.user_id == u.id)):
            continue
        is_dir = u.role == Role.DIRECTOR
        s.add(Notification(
            chat_id=u.telegram_chat_id if level != "info" else None, user_id=u.id, kind=kind, level=level,
            group_key=group, dedupe_key=dkey, text=text, digest_pending=level == "info" and u.telegram_chat_id is not None,
            buttons=buttons if is_dir else None, created_at=clock.now(),
        ))
        n += 1
    s.flush()
    return n


def alert_public(n: Notification) -> dict:
    return {"id": n.id, "kind": n.kind, "level": n.level, "label": LABELS.get(n.kind or "", ""), "text": n.text,
            "created_at": n.created_at.isoformat(), "read": n.read_at is not None,
            "handled": n.handled_at is not None, "handled_by": n.handled_by}


def my_alerts(s: Session, actor: Actor, unread_only: bool = False, limit: int = 50) -> dict:
    if not actor.user_id:
        raise Forbidden("Недостатньо прав")
    q = select(Notification).where(Notification.user_id == actor.user_id, Notification.kind.is_not(None))
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    rows = list(s.scalars(q.order_by(Notification.id.desc()).limit(limit)))
    unread = s.scalar(select(func.count(Notification.id)).where(Notification.user_id == actor.user_id,
                                                                 Notification.kind.is_not(None), Notification.read_at.is_(None)))
    return {"unread": int(unread or 0), "items": [alert_public(n) for n in rows]}


def mark_read(s: Session, actor: Actor, ids: list[int] | None = None) -> int:
    q = update(Notification).where(Notification.user_id == actor.user_id, Notification.read_at.is_(None))
    if ids:
        q = q.where(Notification.id.in_(ids))
    return s.execute(q.values(read_at=clock.now()).execution_options(synchronize_session=False)).rowcount


def mark_handled(s: Session, actor: Actor, notification_id: int) -> dict:
    actor.require(Role.DIRECTOR)
    n = s.get(Notification, notification_id)
    if n is None or n.user_id != actor.user_id:
        raise NotFound("Сповіщення не знайдено")
    if n.handled_at:
        return {"handled_by": n.handled_by, "already": True}
    now = clock.now()
    s.execute(update(Notification).where(Notification.group_key == n.group_key, Notification.handled_at.is_(None))
              .values(handled_at=now, handled_by=actor.user_id, read_at=func.coalesce(Notification.read_at, now))
              .execution_options(synchronize_session=False))
    return {"handled_by": actor.user_id, "already": False}


def set_disabled(s: Session, actor: Actor, kinds: list[str]) -> list[str]:
    from .common import set_setting

    actor.require(Role.DIRECTOR)
    bad = [k for k in kinds if k not in IMPORTANT]
    if bad:
        raise ValidationError("Вимкнути можна лише важливі сповіщення: " + ", ".join(IMPORTANT))
    set_setting(s, actor, "alerts_disabled", sorted(set(kinds)))
    return sorted(set(kinds))


def earnings_changed(s: Session, o: Order, before: int, reason: str) -> None:
    from . import notifications

    text = (f"✏️ Виправлено замовлення #{o.id} ({o.service.name if o.service else 'товари'}).\n"
            f"Заробіток майстра: {fmt(before)} → {fmt(o.master_earning)}\nПричина: {reason}")
    if abs(o.master_earning - before) > EARNINGS_CHANGE_THRESHOLD and enabled(s, "earnings_change"):
        raise_alert(s, "earnings_change", text, entity="order", entity_id=o.id, master_id=o.master_id)
    elif o.master and o.master.telegram_chat_id:
        notifications.enqueue(s, o.master.telegram_chat_id, text)


def order_voided(s: Session, o: Order, reason: str) -> None:
    title = "↩️ Повернення" if o.status == OrderStatus.REFUNDED else "🚫 Скасовано замовлення"
    raise_alert(s, "cancellation", f"{title} #{o.id}: {fmt(o.amount)}\nПричина: {reason}",
                entity="order", entity_id=o.id, master_id=o.master_id)


def new_order(s: Session, o: Order, actor: Actor) -> None:
    if actor.user_id == o.master_id:
        return
    raise_alert(s, "new_order", f"🧾 Нове замовлення #{o.id}: {o.service.name if o.service else 'товари'}",
                entity="order", entity_id=o.id, master_id=o.master_id, to_directors=False)


def no_show(s: Session, appt) -> None:
    raise_alert(s, "no_show", f"🚫 Клієнт не прийшов: {appt.starts_at:%d.%m %H:%M}, {appt.service.name}",
                entity="appointment", entity_id=appt.id, master_id=appt.master_id, to_directors=False)


def appointment_cancelled(s: Session, appt, actor: Actor) -> None:
    raise_alert(s, "cancellation", f"❌ Скасовано запис {appt.starts_at:%d.%m %H:%M}, {appt.service.name}",
                entity="appointment", entity_id=appt.id, master_id=appt.master_id, exclude_user_id=actor.user_id)


def check_master_balance(s: Session, master_id: int) -> None:
    from .orders import master_balance

    b = master_balance(s, master_id)
    if b["balance"] < 0:
        m = s.get(User, master_id)
        raise_alert(s, "master_negative_balance",
                    f"🔴 Від'ємний баланс майстра {m.full_name if m else master_id}: {fmt(b['balance'])} (переплата). "
                    "Буде утримано з наступної виплати.", entity="user", entity_id=master_id,
                    dedupe=f"{master_id}:{clock.now().date().isoformat()}:{b['balance']}")


def hourly_checks(s: Session, now: datetime | None = None) -> dict:
    now = now or clock.now()
    out = {"unpaid": 0, "shifts": 0, "balances": 0}
    cut = now - timedelta(days=UNPAID_DAYS)
    for o in s.scalars(select(Order).where(Order.status == OrderStatus.UNPAID, Order.closed_at < cut)):
        out["unpaid"] += raise_alert(s, "unpaid_overdue",
                                     f"⏳ Неоплачене замовлення #{o.id} ({fmt(o.amount)}) — понад {UNPAID_DAYS} дні",
                                     entity="order", entity_id=o.id, dedupe=f"order:{o.id}")
    scut = now - timedelta(hours=SHIFT_HOURS)
    for sh in s.scalars(select(Shift).where(Shift.status == ShiftStatus.OPEN, Shift.opened_at < scut)):
        out["shifts"] += raise_alert(s, "shift_not_closed", f"⏰ Зміна {sh.master.full_name} не закрита понад {SHIFT_HOURS} год",
                                     entity="shift", entity_id=sh.id, master_id=sh.master_id, dedupe=f"shift:{sh.id}")
    for m in s.scalars(select(User).where(User.role == Role.MASTER, User.is_active.is_(True))):
        before = s.scalar(select(func.count(Notification.id))) or 0
        check_master_balance(s, m.id)
        out["balances"] += (s.scalar(select(func.count(Notification.id))) or 0) - before
    return out


def daily_checks(s: Session, today: date | None = None) -> dict:
    from .analytics import dashboard, period_range

    today = today or clock.now().date()
    out = {"profitability": 0, "stock": 0}
    a, b = period_range("month", today)
    d = dashboard(s, Actor.system(), a, min(b, today))
    if d["revenue"] and d["net_profit"] * 10000 // d["revenue"] < PROFITABILITY_MIN_BP:
        out["profitability"] = raise_alert(
            s, "profitability_low", f"📉 Рентабельність місяця {d['net_profit'] * 100 // d['revenue']}% (< 18%)",
            dedupe=f"profit:{today.isoformat()}")
    for m in s.scalars(select(Material).where(Material.is_active.is_(True), Material.stock_milli < 0)):
        out["stock"] += raise_alert(s, "negative_stock", f"📦 Від'ємний залишок: {m.name} — зробіть інвентаризацію",
                                    entity="material", entity_id=m.id, dedupe=f"mat:{m.id}:{today.isoformat()}")
    for p in s.scalars(select(Product).where(Product.active.is_(True), Product.stock_qty < 0)):
        out["stock"] += raise_alert(s, "negative_stock", f"📦 Від'ємний залишок товару: {p.name} — зробіть інвентаризацію",
                                    entity="product", entity_id=p.id, dedupe=f"prod:{p.id}:{today.isoformat()}")
    out["digests"] = send_digests(s)
    return out


def send_digests(s: Session) -> int:
    from . import notifications

    rows = list(s.scalars(select(Notification).where(Notification.digest_pending.is_(True)).order_by(Notification.id)))
    by_user: dict[int, list[Notification]] = {}
    for n in rows:
        by_user.setdefault(n.user_id, []).append(n)
    sent = 0
    for uid, items in by_user.items():
        u = s.get(User, uid)
        for n in items:
            n.digest_pending = False
        if u is None or not u.telegram_chat_id:
            continue
        text = "📰 Щоденний дайджест\n" + "\n".join(f"• {n.text}" for n in items[:30])
        sent += notifications.enqueue(s, u.telegram_chat_id, text)
    return sent
