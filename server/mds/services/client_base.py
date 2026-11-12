from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import NotFound, ValidationError
from ..models import Appointment, ApptStatus, Client, Order, OrderStatus, Role, Service, User
from .catalog import client_public, normalize_phone
from .common import Actor, audit, get_or_404, get_setting

SEGMENTS = {"new": "Новий", "regular": "Постійний", "vip": "VIP", "lapsed": "Давно не був", "inactive": "Неактивний"}
SOURCES = ("instagram", "рекомендація", "сайт", "telegram-бот", "проходили повз", "google", "інше")
CARD_FIELDS = ("name", "phone", "phone2", "birthday", "telegram_username", "source", "wishes", "notes", "marketing_consent")


def _cfg(s: Session) -> dict:
    return {k: int(get_setting(s, f"client_seg_{k}") or 0) for k in ("regular_visits", "vip_visits", "vip_spent", "lapsed_days", "inactive_days")}


def segment(visits: int, spent: int, last_visit: datetime | None, created_at: datetime, now: datetime, cfg: dict) -> str:
    ref = last_visit or created_at
    idle = (now - ref).days
    if idle >= cfg["inactive_days"]:
        return "inactive"
    if visits and idle >= cfg["lapsed_days"]:
        return "lapsed"
    if visits >= cfg["vip_visits"] or spent >= cfg["vip_spent"]:
        return "vip"
    if visits >= cfg["regular_visits"]:
        return "regular"
    return "new"


def _order_stats(s: Session, client_ids: list[int] | None = None, master_id: int | None = None) -> dict[int, dict]:
    q = select(Order.client_id, func.count(Order.id), func.coalesce(func.sum(Order.amount), 0),
               func.min(Order.closed_at), func.max(Order.closed_at)).where(
        Order.status == OrderStatus.COMPLETED).group_by(Order.client_id)
    if client_ids is not None:
        q = q.where(Order.client_id.in_(client_ids or [0]))
    if master_id:
        q = q.where(Order.master_id == master_id)
    return {cid: {"visits": int(n), "spent": int(total), "first_visit": first, "last_visit": last}
            for cid, n, total, first, last in s.execute(q)}


def _row(c: Client, st: dict | None, now: datetime, cfg: dict, finance: bool = True) -> dict:
    st = st or {"visits": 0, "spent": 0, "first_visit": None, "last_visit": None}
    seg = segment(st["visits"], st["spent"], st["last_visit"], c.created_at, now, cfg)
    out = {**client_public(c), "visits": st["visits"], "segment": seg, "segment_label": SEGMENTS[seg],
           "first_visit": st["first_visit"].isoformat() if st["first_visit"] else None,
           "last_visit": st["last_visit"].isoformat() if st["last_visit"] else None}
    if finance:
        out["spent"] = st["spent"]
        out["average_check"] = st["spent"] // st["visits"] if st["visits"] else 0
    return out


def list_clients(s: Session, actor: Actor, *, q: str = "", segment_key: str = "", sort: str = "name", page: int = 1,
                 per_page: int = 50, birthdays_days: int | None = None) -> dict:
    actor.require(Role.DIRECTOR)
    now, cfg = clock.now(), _cfg(s)
    stmt = select(Client)
    if q.strip():
        import re

        digits = re.sub(r"\D", "", q)
        words = " ".join(q.lower().split())
        conds = [Client.search_name.like(f"%{words}%")]
        if len(digits) >= 3:
            conds += [Client.phone.like(f"%{digits}%"), Client.phone2.like(f"%{digits}%")]
        from sqlalchemy import or_

        stmt = stmt.where(or_(*conds))
    clients = list(s.scalars(stmt))
    stats = _order_stats(s)
    rows = [_row(c, stats.get(c.id), now, cfg, finance=actor.is_director) for c in clients]
    counts = Counter(r["segment"] for r in rows)
    if segment_key:
        rows = [r for r in rows if r["segment"] == segment_key]
    if birthdays_days:
        soon = {(now.date() + timedelta(days=i)).strftime("%m-%d") for i in range(birthdays_days + 1)}
        rows = [r for r in rows if r["birthday"] and r["birthday"][5:] in soon]
    if sort == "visits":
        rows.sort(key=lambda r: (-r["visits"], r["name"].lower()))
    elif sort == "spent":
        rows.sort(key=lambda r: (-r.get("spent", 0), r["name"].lower()))
    elif sort == "last_visit":
        rows.sort(key=lambda r: r["last_visit"] or "", reverse=True)
    elif sort == "created":
        rows.sort(key=lambda r: r["created_at"], reverse=True)
    else:
        rows.sort(key=lambda r: r["name"].lower())
    per_page = max(1, min(int(per_page), 200))
    total = len(rows)
    start = (max(1, int(page)) - 1) * per_page
    return {"items": rows[start:start + per_page], "total": total, "page": max(1, int(page)), "per_page": per_page,
            "segments": {k: counts.get(k, 0) for k in SEGMENTS}, "segment_labels": SEGMENTS}


def card(s: Session, actor: Actor, client_id: int) -> dict:
    c = get_or_404(s, Client, client_id, "Клієнта")
    master_id = actor.user_id if actor.is_master else None
    if master_id and not s.scalar(select(Appointment.id).where(Appointment.client_id == c.id, Appointment.master_id == master_id).limit(1)):
        raise NotFound(f"Клієнта #{client_id} не знайдено")
    now, cfg = clock.now(), _cfg(s)
    all_stats = _order_stats(s, [c.id]).get(c.id)
    out = _row(c, all_stats, now, cfg, finance=not actor.is_master)
    oq = select(Order).where(Order.client_id == c.id)
    if master_id:
        oq = oq.where(Order.master_id == master_id)
    orders = list(s.scalars(oq.order_by(Order.closed_at)))
    dates = [o.closed_at for o in orders]
    gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
    svc = Counter(o.service_id for o in orders)
    mst = Counter(o.master_id for o in orders)
    names = {x.id: x.name for x in s.scalars(select(Service).where(Service.id.in_(list(svc) or [0])))}
    users = {x.id: x.full_name for x in s.scalars(select(User).where(User.id.in_(list(mst) or [0])))}
    aq = select(Appointment.status, func.count(Appointment.id)).where(Appointment.client_id == c.id).group_by(Appointment.status)
    if master_id:
        aq = aq.where(Appointment.master_id == master_id)
    by_status = dict(s.execute(aq).all())
    out.update({
        "avg_interval_days": round(sum(gaps) / len(gaps)) if gaps else None,
        "cancelled": int(by_status.get(ApptStatus.CANCELLED, 0)),
        "no_show": int(by_status.get(ApptStatus.NO_SHOW, 0)),
        "top_services": [{"name": names.get(k, "—"), "count": n} for k, n in svc.most_common(5)],
        "top_masters": [{"id": k, "name": users.get(k, "—"), "count": n} for k, n in mst.most_common(3)],
        "next_visit_hint": (dates[-1] + timedelta(days=round(sum(gaps) / len(gaps)))).date().isoformat() if gaps else None,
    })
    if not actor.is_master:
        out["finance"] = {"spent": sum(o.amount for o in orders), "orders": len(orders),
                          "by_month": _by_month(orders), "materials": sum(o.materials_cost for o in orders)}
    return out


def _by_month(orders: list[Order]) -> list[dict]:
    m: dict[str, int] = {}
    for o in orders:
        k = o.closed_at.strftime("%Y-%m")
        m[k] = m.get(k, 0) + o.amount
    return [{"month": k, "amount": v} for k, v in sorted(m.items())][-12:]


def update_card(s: Session, actor: Actor, client_id: int, data: dict) -> Client:
    from ..errors import Conflict

    actor.require(Role.DIRECTOR)
    c = get_or_404(s, Client, client_id, "Клієнта")
    before = client_public(c)
    for k in ("phone", "phone2"):
        if k in data:
            v = (data[k] or "").strip()
            if not v:
                setattr(c, k, None)
                continue
            n = normalize_phone(v)
            other = s.scalar(select(Client).where(((Client.phone == n) | (Client.phone2 == n)), Client.id != c.id))
            if other:
                raise Conflict(f"Номер {n} уже є в картці «{other.name}» — об'єднайте клієнтів замість дубліката")
            setattr(c, k, n)
    if "name" in data:
        if not (data["name"] or "").strip():
            raise ValidationError("Вкажіть ім'я клієнта")
        c.name = data["name"].strip()
    if "birthday" in data:
        c.birthday = data["birthday"] or None
        if c.birthday and not (date(1900, 1, 1) <= c.birthday <= clock.now().date()):
            raise ValidationError("Невірна дата народження")
    for k in ("telegram_username", "source", "wishes", "notes"):
        if k in data:
            v = (data[k] or "").strip() or None
            if k == "telegram_username" and v:
                v = v.lstrip("@")[:64]
            setattr(c, k, v)
    if "marketing_consent" in data and data["marketing_consent"] is not None:
        c.marketing_consent = bool(data["marketing_consent"])
    if "blocked" in data and data["blocked"] is not None:
        c.blocked = bool(data["blocked"])
        c.blocked_reason = ((data.get("blocked_reason") or "").strip() or None) if c.blocked else None
    s.flush()
    audit(s, actor, "client", c.id, "update", before, client_public(c), data.get("reason") or data.get("blocked_reason"))
    return c


def find_by_phone(s: Session, actor: Actor, phone: str) -> Client | None:
    actor.require(Role.DIRECTOR, Role.MASTER)
    n = normalize_phone(phone)
    return s.scalar(select(Client).where((Client.phone == n) | (Client.phone2 == n)))


def duplicates(s: Session, actor: Actor) -> list[list[dict]]:
    actor.require(Role.DIRECTOR)
    groups: dict[str, list[Client]] = {}
    for c in s.scalars(select(Client).where(Client.search_name.is_not(None))):
        groups.setdefault(c.search_name, []).append(c)
    out = [[client_public(c) for c in g] for g in groups.values() if len(g) > 1]
    by_phone = {c.phone: c for c in s.scalars(select(Client).where(Client.phone.is_not(None)))}
    for c in s.scalars(select(Client).where(Client.phone2.is_not(None))):
        o = by_phone.get(c.phone2)
        if o and o.id != c.id:
            out.append([client_public(o), client_public(c)])
    return out[:100]


def merge(s: Session, actor: Actor, keep_id: int, dup_id: int) -> Client:
    actor.require(Role.DIRECTOR)
    if keep_id == dup_id:
        raise ValidationError("Оберіть дві різні картки")
    keep, dup = get_or_404(s, Client, keep_id, "Клієнта"), get_or_404(s, Client, dup_id, "Клієнта")
    before = {"keep": client_public(keep), "dup": client_public(dup)}
    for a in s.scalars(select(Appointment).where(Appointment.client_id == dup.id)):
        a.client_id = keep.id
    for o in s.scalars(select(Order).where(Order.client_id == dup.id)):
        o.client_id = keep.id
    phone, tg = dup.phone, dup.telegram_user_id
    dup.phone, dup.telegram_user_id = None, None
    s.flush()
    if not keep.phone and phone:
        keep.phone = phone
    elif phone and not keep.phone2 and phone != keep.phone:
        keep.phone2 = phone
    if not keep.telegram_user_id and tg:
        keep.telegram_user_id = tg
    for k in ("phone2", "birthday", "telegram_username", "source", "wishes"):
        if not getattr(keep, k) and getattr(dup, k) and not (k == "phone2" and getattr(dup, k) == keep.phone):
            setattr(keep, k, getattr(dup, k))
    keep.notes = "\n".join(x for x in (keep.notes, dup.notes) if x) or None
    keep.marketing_consent = keep.marketing_consent or dup.marketing_consent
    s.flush()
    audit(s, actor, "client", keep.id, "merge", before, client_public(keep), f"Об'єднано з #{dup.id}")
    audit(s, actor, "client", dup.id, "merged_into", before["dup"], {"into": keep.id})
    s.delete(dup)
    s.flush()
    return keep


def anonymize(s: Session, actor: Actor, client_id: int, reason: str) -> Client:
    actor.require(Role.DIRECTOR)
    if not (reason or "").strip():
        raise ValidationError("Вкажіть підставу (напр. «запит клієнта від 05.11»)")
    c = get_or_404(s, Client, client_id, "Клієнта")
    before = client_public(c)
    c.name = f"Знеособлений клієнт #{c.id}"
    for k in ("phone", "phone2", "birthday", "telegram_user_id", "telegram_username", "source", "wishes", "notes", "last_contact_at"):
        setattr(c, k, None)
    c.marketing_consent = False
    s.flush()
    audit(s, actor, "client", c.id, "anonymize", before, client_public(c), reason)
    return c


def export_csv(s: Session, actor: Actor) -> str:
    actor.require(Role.DIRECTOR)
    first = list_clients(s, actor, per_page=200, page=1)
    rows = first["items"]
    for p in range(2, (first["total"] + 199) // 200 + 1):
        rows += list_clients(s, actor, per_page=200, page=p)["items"]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["ID", "Ім'я", "Телефон", "Дод. телефон", "Дата народження", "Джерело", "Згода на рекламу", "Сегмент",
                "Візитів", "Витрачено, грн", "Середній чек, грн", "Перший візит", "Останній візит", "Створено"])
    for r in rows:
        w.writerow([r["id"], r["name"], r["phone"] or "", r.get("phone2") or "", r.get("birthday") or "", r.get("source") or "",
                    "так" if r.get("marketing_consent") else "ні", r["segment_label"], r["visits"], r.get("spent", 0) / 100,
                    r.get("average_check", 0) / 100, (r["first_visit"] or "")[:10], (r["last_visit"] or "")[:10], r["created_at"][:10]])
    audit(s, actor, "client", 0, "export", None, {"rows": len(rows)})
    return "﻿" + buf.getvalue()


def analytics(s: Session, actor: Actor, date_from: date, date_to: date) -> dict:
    actor.require(Role.DIRECTOR)
    now, cfg = clock.now(), _cfg(s)
    a, b = datetime.combine(date_from, datetime.min.time()), datetime.combine(date_to + timedelta(days=1), datetime.min.time())
    stats = _order_stats(s)
    clients = list(s.scalars(select(Client)))
    period = list(s.execute(select(Order.client_id, Order.amount, Order.closed_at).where(
        Order.closed_at >= a, Order.closed_at < b, Order.status == OrderStatus.COMPLETED)))
    by_client = Counter(cid for cid, _, _ in period)
    new_ids = {cid for cid, st in stats.items() if st["first_visit"] and a <= st["first_visit"] < b}
    repeat_orders = sum(1 for cid, _, closed in period if stats.get(cid) and stats[cid]["first_visit"] < closed)
    segs = Counter(segment(stats.get(c.id, {}).get("visits", 0), stats.get(c.id, {}).get("spent", 0),
                           stats.get(c.id, {}).get("last_visit"), c.created_at, now, cfg) for c in clients)
    top = sorted(((cid, sum(x for c2, x, _ in period if c2 == cid)) for cid in by_client), key=lambda x: -x[1])[:10]
    names = {c.id: c.name for c in clients}
    revenue = sum(x for _, x, _ in period)
    return {
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "total_clients": len(clients),
        "new_clients": len(new_ids),
        "active_clients": len(by_client),
        "visits": len(period),
        "repeat_visits": repeat_orders,
        "repeat_share_bp": repeat_orders * 10000 // len(period) if period else 0,
        "average_check": revenue // len(period) if period else 0,
        "revenue": revenue,
        "segments": {k: segs.get(k, 0) for k in SEGMENTS},
        "top_clients": [{"id": cid, "name": names.get(cid, "—"), "spent": v, "visits": by_client[cid]} for cid, v in top],
        "stopped": segs.get("lapsed", 0) + segs.get("inactive", 0),
    }
