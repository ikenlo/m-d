"""SMS-розсилки (директор): баланс і витрати, історія зі статусами, одноразові розсилки, аналіз бази й автоматизація.

- Сервісні повідомлення (перенесення запису тощо) — kind="service", без згоди на рекламу.
- Рекламні (розсилки, привітання, «час повернутися») — kind="marketing", лише клієнтам із `marketing_consent`.
- Надсилання — через чергу `sms_messages` (фоновий цикл і одразу після створення), тож недоступність TurboSMS
  не блокує роботу: повідомлення лишаються в історії зі статусом.
- Витрати на розсилку (кількість × ціна SMS з налаштувань) записуються у «Витрати» → «Реклама».
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import ValidationError
from ..models import Appointment, ApptStatus, Client, Order, Role, SmsCampaign, SmsMessage
from . import client_base, sms
from .common import Actor, audit, get_setting

AUDIENCES = {"all": "Усі зі згодою", "new": "Нові", "regular": "Постійні", "vip": "VIP", "lapsed": "Давно не були",
             "inactive": "Неактивні", "birthdays": "Дні народження (7 днів)", "return_due": "Час повторного візиту"}
FINAL = ("delivered", "failed", "not_configured")
WD = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]


def ready(s: Session) -> bool:
    return bool(get_setting(s, "sms_enabled")) and sms.configured()


def enqueue(s: Session, *, phone: str, text: str, kind: str, reason: str, client_id: int | None = None,
            campaign_id: int | None = None) -> SmsMessage:
    m = SmsMessage(client_id=client_id, campaign_id=campaign_id, phone=phone, text=text[:1000], kind=kind, reason=reason,
                   status="queued", created_at=clock.now())
    s.add(m)
    return m


def process_queue(s: Session, limit: int = 40) -> int:
    """Надіслати чергу. TurboSMS не підключено/вимкнено → «not_configured» (видно в історії, черга не росте)."""
    now = clock.now()
    rows = list(s.scalars(select(SmsMessage).where(SmsMessage.status == "queued").order_by(SmsMessage.id).limit(limit)))
    ok_ready = ready(s)
    n = 0
    for m in rows:
        if not ok_ready:
            m.status, m.error, m.updated_at = "not_configured", "TurboSMS не підключено", now
            continue
        r = sms.send_one(m.phone, m.text)
        m.status = "sent" if r["ok"] else "failed"
        m.provider_id, m.error, m.sent_at, m.updated_at = r["message_id"], r["error"], now, now
        if r["ok"]:
            n += 1
            if m.client_id:
                c = s.get(Client, m.client_id)
                if c:
                    c.last_contact_at = now
    return n


def refresh_statuses(s: Session) -> int:
    rows = list(s.scalars(select(SmsMessage).where(SmsMessage.status == "sent", SmsMessage.provider_id.is_not(None),
                                                   SmsMessage.sent_at >= clock.now() - timedelta(days=3)).limit(200)))
    st = sms.statuses([m.provider_id for m in rows])
    for m in rows:
        if st.get(m.provider_id) in ("delivered", "failed"):
            m.status, m.updated_at = st[m.provider_id], clock.now()
    return len(st)


def summary(s: Session, actor: Actor) -> dict:
    actor.require(Role.DIRECTOR)
    now = clock.now()
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    by_status = dict(s.execute(select(SmsMessage.status, func.count(SmsMessage.id)).group_by(SmsMessage.status)).all())
    sent_month = s.scalar(select(func.count(SmsMessage.id)).where(SmsMessage.sent_at >= month)) or 0
    return {
        "configured": sms.configured(), "enabled": bool(get_setting(s, "sms_enabled")), "balance": sms.balance(),
        "price": int(get_setting(s, "sms_price") or 0),
        "spent_total": int(s.scalar(select(func.coalesce(func.sum(SmsCampaign.cost), 0))) or 0),
        "spent_month": int(s.scalar(select(func.coalesce(func.sum(SmsCampaign.cost), 0)).where(SmsCampaign.created_at >= month)) or 0),
        "sent_total": int(sum(by_status.get(k, 0) for k in ("sent", "delivered"))), "sent_month": int(sent_month),
        "by_status": {k: int(v) for k, v in by_status.items()},
        "consent_clients": int(s.scalar(select(func.count(Client.id)).where(Client.marketing_consent.is_(True), Client.phone.is_not(None))) or 0),
    }


def _first_name(name: str) -> str:
    return (name or "").split()[0] if name else ""


def audience(s: Session, key: str) -> list[Client]:
    """Отримувачі реклами: лише зі згодою, з номером і не заблоковані."""
    if key not in AUDIENCES:
        raise ValidationError("Невідома аудиторія")
    base = [c for c in s.scalars(select(Client).where(Client.marketing_consent.is_(True), Client.phone.is_not(None),
                                                      Client.blocked.is_(False)))]
    if key == "all":
        return base
    now, cfg = clock.now(), client_base._cfg(s)
    stats = client_base._order_stats(s, [c.id for c in base])
    if key == "birthdays":
        soon = {(now.date() + timedelta(days=i)).strftime("%m-%d") for i in range(8)}
        return [c for c in base if c.birthday and c.birthday.strftime("%m-%d") in soon]
    if key == "return_due":
        future = set(s.scalars(select(Appointment.client_id).where(Appointment.starts_at >= now,
                                                                   Appointment.status.in_((ApptStatus.PENDING, ApptStatus.CONFIRMED)))))
        out = []
        for c in base:
            st = stats.get(c.id)
            if not st or c.id in future or st["visits"] < 2:
                continue
            interval = (st["last_visit"] - st["first_visit"]).days / (st["visits"] - 1)
            if interval >= 7 and st["last_visit"] + timedelta(days=interval) <= now + timedelta(days=3):
                out.append(c)
        return out
    def seg(c):
        st = stats.get(c.id) or {}
        return client_base.segment(st.get("visits", 0), st.get("spent", 0), st.get("last_visit"), c.created_at, now, cfg)
    return [c for c in base if seg(c) == key]


def create_campaign(s: Session, actor: Actor, *, title: str, text: str, audience_key: str, dry_run: bool = False) -> dict:
    """Одноразова розсилка. {name} у тексті — ім'я клієнта. dry_run — лише порахувати отримувачів і вартість."""
    actor.require(Role.DIRECTOR)
    if not (title or "").strip() or not (text or "").strip():
        raise ValidationError("Вкажіть назву й текст розсилки")
    if len(text) > 670:
        raise ValidationError("Текст задовгий (до 670 символів — 10 SMS)")
    people = audience(s, audience_key)
    price = int(get_setting(s, "sms_price") or 0)
    parts = 1 if len(text) <= 70 else (len(text) + 66) // 67  # кирилиця: 70 символів, далі по 67
    cost = len(people) * price * parts
    preview = text.replace("{name}", _first_name(people[0].name) if people else "Олено")
    if dry_run:
        return {"recipients": len(people), "cost": cost, "parts": parts, "preview": preview, "ready": ready(s)}
    if not ready(s):
        raise ValidationError("TurboSMS не підключено — розсилку надіслати неможливо (Налаштування → SMS)")
    if not people:
        raise ValidationError("Немає отримувачів зі згодою на рекламу в цій аудиторії")
    camp = SmsCampaign(title=title.strip()[:120], text=text, audience=audience_key, status="queued", recipients=len(people),
                       cost=cost, created_by=actor.user_id, created_at=clock.now())
    s.add(camp)
    s.flush()
    for c in people:
        enqueue(s, phone=c.phone, text=text.replace("{name}", _first_name(c.name)), kind="marketing", reason="campaign",
                client_id=c.id, campaign_id=camp.id)
    if cost > 0:  # витрати на розсилку — у «Витрати» → «Реклама»
        from .expenses import create_expense

        e = create_expense(s, actor, category="advertising", amount=cost, spent_on=clock.now().date(),
                           description=f"SMS-розсилка: {camp.title} ({len(people)} SMS)")
        camp.expense_id = e.id
    audit(s, actor, "sms_campaign", camp.id, "create", None, {"title": camp.title, "audience": audience_key,
                                                             "recipients": len(people), "cost": cost})
    return {"id": camp.id, "recipients": len(people), "cost": cost}


def campaigns(s: Session, actor: Actor) -> list[dict]:
    actor.require(Role.DIRECTOR)
    rows = list(s.scalars(select(SmsCampaign).order_by(SmsCampaign.id.desc()).limit(50)))
    counts = {}
    for cid, st, n in s.execute(select(SmsMessage.campaign_id, SmsMessage.status, func.count(SmsMessage.id))
                                .where(SmsMessage.campaign_id.in_([c.id for c in rows] or [0])).group_by(SmsMessage.campaign_id, SmsMessage.status)):
        counts.setdefault(cid, {})[st] = n
    return [{"id": c.id, "title": c.title, "text": c.text, "audience": c.audience, "audience_label": AUDIENCES.get(c.audience, c.audience),
             "recipients": c.recipients, "cost": c.cost, "created_at": c.created_at.isoformat(), "statuses": counts.get(c.id, {})} for c in rows]


def messages(s: Session, actor: Actor, *, client_id: int | None = None, limit: int = 100) -> list[dict]:
    actor.require(Role.DIRECTOR, Role.ADMIN)
    q = select(SmsMessage).order_by(SmsMessage.id.desc()).limit(min(limit, 300))
    if client_id:
        q = q.where(SmsMessage.client_id == client_id)
    names = {}
    rows = list(s.scalars(q))
    if rows:
        names = dict(s.execute(select(Client.id, Client.name).where(Client.id.in_({m.client_id for m in rows if m.client_id} or {0}))).all())
    return [{"id": m.id, "client_id": m.client_id, "client_name": names.get(m.client_id), "phone": m.phone, "text": m.text,
             "kind": m.kind, "reason": m.reason, "status": m.status, "error": m.error, "created_at": m.created_at.isoformat(),
             "sent_at": m.sent_at.isoformat() if m.sent_at else None} for m in rows]


def insights(s: Session, actor: Actor) -> dict:
    """Аналіз бази: коли клієнти найчастіше записуються (день і година) і кому що варто надіслати."""
    actor.require(Role.DIRECTOR)
    since = clock.now() - timedelta(days=180)
    created = [a for (a,) in s.execute(select(Appointment.created_at).where(Appointment.created_at >= since,
                                                                            Appointment.source.in_(("telegram", "site"))))]
    if len(created) < 10:  # мало онлайн-записів — орієнтуємось на час візитів
        created = [a for (a,) in s.execute(select(Order.closed_at).where(Order.closed_at >= since))]
    hours, days = Counter(x.hour for x in created), Counter(x.weekday() for x in created)
    best_hour = hours.most_common(1)[0][0] if hours else 11
    best_hour = min(max(best_hour - 1, 10), 19)  # трохи раніше за звичний час запису, у межах 10:00–19:00
    best_day = days.most_common(1)[0][0] if days else 1
    tips = {
        "return_due": ("Нагадати про повторний візит", "{name}, час оновити образ 💇 Запишіться в M&D — оберемо зручний час."),
        "lapsed": ("Повернути тих, хто давно не був", "{name}, ми сумуємо! Повертайтеся в M&D — чекаємо на вас 🌿"),
        "birthdays": ("Привітати з днем народження", "{name}, вітаємо з днем народження! 🎉 Даруємо приємний бонус на наступний візит у M&D."),
        "vip": ("Подякувати VIP-клієнтам", "{name}, дякуємо, що обираєте M&D! Для вас — пріоритетний запис на зручний час."),
        "new": ("Закріпити нових клієнтів", "{name}, дякуємо за перший візит у M&D! Будемо раді бачити вас знову."),
        "inactive": ("Оживити неактивних", "{name}, давно не бачились! У M&D нові послуги — запишіться, будемо раді."),
    }
    recs = []
    for key, (title, text) in tips.items():
        n = len(audience(s, key))
        if n:
            recs.append({"audience": key, "audience_label": AUDIENCES[key], "count": n, "title": title, "text": text})
    recs.sort(key=lambda r: ["return_due", "birthdays", "lapsed", "vip", "new", "inactive"].index(r["audience"]))
    return {"best_day": WD[best_day], "best_hour": f"{best_hour:02d}:00", "sample": len(created),
            "hours": {f"{h:02d}": n for h, n in sorted(hours.items())}, "recommendations": recs}


def run_automations(s: Session) -> int:
    """Раз на день у налаштовану годину: привітання з днем народження й нагадування про повторний візит
    (лише зі згодою; не частіше раз на рік / раз на 30 днів одному клієнту)."""
    now = clock.now()
    if now.hour != int(get_setting(s, "sms_auto_hour") or 11) or not ready(s):
        return 0
    n = 0
    for reason, enabled_key, aud, gap_days, setting_text in (
            ("birthday", "sms_auto_birthday", "birthdays", 300, "sms_birthday_text"),
            ("return", "sms_auto_return", "return_due", 30, "sms_return_text")):
        if not get_setting(s, enabled_key):
            continue
        tpl = get_setting(s, setting_text) or ""
        for c in audience(s, aud):
            if reason == "birthday" and c.birthday.strftime("%m-%d") != now.strftime("%m-%d"):
                continue
            recent = s.scalar(select(SmsMessage.id).where(SmsMessage.client_id == c.id, SmsMessage.reason == reason,
                                                          SmsMessage.created_at >= now - timedelta(days=gap_days)).limit(1))
            if recent:
                continue
            enqueue(s, phone=c.phone, text=tpl.replace("{name}", _first_name(c.name)), kind="marketing", reason=reason, client_id=c.id)
            n += 1
    return n


def flush(db) -> None:
    """Фоново після створення розсилки / зсуву записів: надіслати чергу окремою транзакцією."""
    try:
        with db.session() as s:
            process_queue(s, limit=200)
    except Exception:  # noqa: BLE001 — недоступність TurboSMS не має ламати відповідь
        import logging

        logging.getLogger("mds.sms").exception("Помилка надсилання черги SMS")


def last_message_at(s: Session, client_id: int) -> datetime | None:
    return s.scalar(select(func.max(SmsMessage.sent_at)).where(SmsMessage.client_id == client_id))
