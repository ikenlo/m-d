"""Стрічка подій для директора: журнал змін (audit_log) простими словами.

Кожен запис журналу перетворюється на подію: хто, що зробив, з ким/чим, сума,
а для змін — список «було → стало» з людськими назвами полів. Самі журнали
(audit_log, ledger) не змінюються — це лише читання й форматування.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Appointment, AuditLog, Client, Material, Order, Role, Service, User
from .common import Actor

CATEGORIES = {
    "money": "Гроші",
    "records": "Записи",
    "clients": "Клієнти",
    "stock": "Склад",
    "staff": "Персонал і налаштування",
}
_ENTITY_CAT = {
    "order": "money", "payout": "money", "shift": "money", "expense": "money",
    "appointment": "records", "callback": "records", "schedule": "records",
    "client": "clients", "material": "stock",
    "user": "staff", "service": "staff", "setting": "staff", "gdrive": "staff", "bug_report": "staff",
    "meter": "money", "generator": "money", "master_charge": "money",
}
STATUS_UA = {"pending": "очікує", "confirmed": "підтверджено", "in_progress": "клієнт прийшов",
             "completed": "завершено", "cancelled": "скасовано", "no_show": "не прийшов"}
PAY_UA = {"cash": "готівка", "card": "картка", "transfer": "переказ"}
EXPENSE_UA = {"rent": "Оренда", "utilities": "Комунальні", "advertising": "Реклама",
              "materials": "Закупівля матеріалів", "fuel": "Пальне (закупівля)", "other": "Інше"}
ROLE_UA = {"director": "директор", "admin": "адміністратор", "master": "майстер"}
MODE_UA = {"salon": "матеріали за рахунок салону", "master": "матеріали платить майстер", "split": "пропорційно"}

FIELD_UA = {
    "starts_at": "Час", "ends_at": "Кінець", "master_id": "Майстер", "service_id": "Послуга", "price": "Ціна",
    "status": "Статус", "comment": "Коментар", "amount": "Сума", "payment_method": "Оплата",
    "master_earning": "Майстру", "salon_earning": "Салону", "materials_cost": "Матеріали",
    "share_bp": "Відсоток майстра", "materials_mode": "Облік матеріалів", "name": "Назва", "full_name": "Ім'я",
    "phone": "Телефон", "notes": "Нотатки", "role": "Роль", "is_active": "Активний", "duration_min": "Тривалість, хв",
    "category": "Категорія", "description": "Опис", "spent_on": "Дата", "unit_cost": "Ціна упаковки",
    "pack_size": "В упаковці", "pack_name": "Упаковка", "tone": "Тон", "min_stock": "Мінімальний залишок",
    "stock": "Залишок", "price_from": "Ціна «від»", "section": "Розділ", "subcategory": "Вид",
    "username": "Логін", "service_ids": "Послуги", "schedule": "Графік", "reminders": "Нагадування",
    "default_share_bp": "Стандартний відсоток", "default_materials_mode": "Облік матеріалів",
    "slot_step_min": "Крок слотів, хв", "booking_horizon_days": "Запис наперед, днів",
    "booking_min_lead_min": "Мін. хвилин до запису", "telegram_auto_confirm": "Автопідтвердження з бота",
    "bot_staff_code": "Код для майстрів", "master_takes_cash": "Майстер бере з каси",
    "favorite_master_visits": "Візитів до улюбленого майстра", "salon_name": "Назва салону",
    "salon_address": "Адреса", "salon_phone": "Телефон салону", "salon_instagram": "Instagram",
    "reminders_enabled": "Нагадування клієнтам",
    "prev": "Попередній показник", "curr": "Поточний показник", "used": "Використано, кВт·год", "tariff": "Тариф",
    "paid_amount": "Сплачено", "hours": "Мотогодини", "fuel_l": "Бензин, л", "fuel_cost": "Бензин, ₴",
    "amort_cost": "Амортизація", "total_cost": "Разом", "fuel_price": "Ціна бензину", "fuel_lph": "Витрата, л/год",
    "amort_per_hour": "Амортизація за годину", "power_outage": "Без світла",
    "generator_fuel_lph_milli": "Генератор: витрата (мл/год)", "generator_fuel_price": "Генератор: ціна бензину",
    "generator_amort_per_hour": "Генератор: амортизація", "generator_hours_initial_milli": "Генератор: початкові мотогодини",
    "utilities_charge_mode": "Списання комунальних з майстрів", "utilities_split": "Принцип розподілу",
    "utilities_charge_share_bp": "Частка для розподілу", "utilities_fixed_amount": "Фіксована ставка",
    "utilities_fixed_per": "Фіксована ставка за",
}
_MONEY = {"price", "amount", "master_earning", "salon_earning", "materials_cost", "unit_cost", "cost",
          "tariff", "paid_amount", "fuel_cost", "amort_cost", "total_cost", "fuel_price", "amort_per_hour", "hour_cost",
          "utilities_fixed_amount", "generator_fuel_price", "generator_amort_per_hour"}
_HIDDEN = {"id", "created_at", "running", "started_at", "ended_at", "corrected", "note", "difference",
           "confirmed", "expense_id", "read_on", "paid_on", "month", "fuel_lph", "hour_cost", "materials", "telegram_linked", "display_name", "stock_packs", "low",
           "master_name", "shift_id", "from_till", "paid_at", "updated_at", "telegram_chat_id"}


def _fmt_money(k: int | None) -> str:
    if k is None:
        return "—"
    g, kop = divmod(abs(int(k)), 100)
    s = f"{g:,}".replace(",", " ") + (f",{kop:02d}" if kop else "")
    return ("−" if k < 0 else "") + s + " ₴"


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


def _when(v: Any) -> str:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, str) and len(v) == 10:  # лише дата (графік, витрати)
        try:
            return date.fromisoformat(v).strftime("%d.%m.%Y")
        except ValueError:
            return v
    d = _dt(v)
    if d is None:
        try:
            dd = date.fromisoformat(str(v))
            return dd.strftime("%d.%m.%Y")
        except ValueError:
            return str(v)
    return d.strftime("%d.%m %H:%M")


class _Names:
    """Кеш імен для підстановки в тексти."""

    def __init__(self, s: Session):
        self.s = s
        self._c: dict = {}

    def _get(self, model, id_):
        if id_ is None:
            return None
        key = (model, id_)
        if key not in self._c:
            self._c[key] = self.s.get(model, id_)
        return self._c[key]

    def prefetch(self, rows: list) -> None:
        """Пакетне завантаження всього, на що посилаються записи журналу, — кілька запитів замість сотень."""
        def put(model, objs):
            for o in objs:
                self._c[(model, o.id)] = o

        for model in (User, Service, Material):  # невеликі довідники — цілком
            put(model, self.s.scalars(select(model)))
        ids = {"appointment": set(), "order": set(), "client": set()}
        for a in rows:
            if a.entity in ids:
                ids[a.entity].add(a.entity_id)
        appts = list(self.s.scalars(select(Appointment).where(Appointment.id.in_(ids["appointment"])))) if ids["appointment"] else []
        orders = list(self.s.scalars(select(Order).where(Order.id.in_(ids["order"])))) if ids["order"] else []
        put(Appointment, appts)
        put(Order, orders)
        client_ids = ids["client"] | {x.client_id for x in appts + orders if x.client_id}
        if client_ids:
            put(Client, self.s.scalars(select(Client).where(Client.id.in_(client_ids))))
        for model, wanted in ((Appointment, ids["appointment"]), (Order, ids["order"]), (Client, client_ids)):
            for i in wanted:  # видалені — щоб не шукати їх повторно
                self._c.setdefault((model, i), None)

    def user(self, id_) -> str:
        u = self._get(User, id_)
        return u.full_name if u else f"#{id_}"

    def service(self, id_) -> str:
        x = self._get(Service, id_)
        return x.name if x else f"послуга #{id_}"

    def client(self, id_) -> str:
        c = self._get(Client, id_)
        return c.name if c else f"клієнт #{id_}"

    def material(self, id_) -> str:
        m = self._get(Material, id_)
        return (f"{m.name} {m.tone}" if m.tone else m.name) if m else f"матеріал #{id_}"

    def appt(self, id_) -> Appointment | None:
        return self._get(Appointment, id_)

    def order(self, id_) -> Order | None:
        return self._get(Order, id_)


def _value(n: _Names, key: str, v: Any) -> str:
    if v is None or v == "":
        return "—"
    if key in _MONEY:
        return _fmt_money(v)
    if key in ("share_bp", "default_share_bp", "utilities_charge_share_bp"):
        return f"{int(v) / 100:g}%"
    if key == "master_id":
        return n.user(v)
    if key == "service_id":
        return n.service(v)
    if key == "status":
        return STATUS_UA.get(v, v)
    if key == "payment_method":
        return PAY_UA.get(v, v)
    if key in ("materials_mode", "default_materials_mode"):
        return MODE_UA.get(v, v)
    if key == "category":
        return EXPENSE_UA.get(v, v)
    if key == "role":
        return ROLE_UA.get(v, v)
    if isinstance(v, bool):
        return "так" if v else "ні"
    if key in ("starts_at", "ends_at"):
        return _when(v)
    if isinstance(v, (list, dict)):
        return "змінено"
    return str(v)


def _changes(n: _Names, before: dict | None, after: dict | None) -> list[dict]:
    before, after = before or {}, after or {}
    out = []
    for k in list(dict.fromkeys([*before.keys(), *after.keys()])):
        if k in _HIDDEN or before.get(k) == after.get(k):
            continue
        out.append({"field": FIELD_UA.get(k, k), "before": _value(n, k, before.get(k)), "after": _value(n, k, after.get(k))})
    return out


def _who(n: _Names, a: AuditLog) -> tuple[str, str]:
    if a.user_id:
        return n.user(a.user_id), "staff"
    if (a.actor or "").startswith("telegram:"):
        return "Клієнт у Telegram", "client"
    return "Система", "system"


def _appt_line(n: _Names, appt_id: int, snap: dict | None) -> str:
    a = n.appt(appt_id)
    snap = snap or {}
    client = n.client(a.client_id) if a else "клієнт"
    service = n.service(snap.get("service_id") or (a.service_id if a else None))
    master = n.user(snap.get("master_id") or (a.master_id if a else None))
    when = _when(snap.get("starts_at") or (a.starts_at if a else ""))
    return f"{client} · {service} · {master} · {when}"


def describe(n: _Names, a: AuditLog) -> dict:
    """Одна подія журналу → {title, text, amount, icon, category, changes}."""
    e, act, b, af = a.entity, a.action, a.before or {}, a.after or {}
    title, text, amount, icon = None, "", None, "history"
    changes: list[dict] = []

    if e == "appointment":
        icon = "calendar"
        line = _appt_line(n, a.entity_id, af or b)
        if act == "create":
            title = "Новий запис" + (" через Telegram" if (a.actor or "").startswith("telegram:") else "")
            text = line
        elif act == "update":
            title, text, changes = "Запис змінено", line, _changes(n, b, af)
        elif act == "propose_time":
            title = "Клієнту запропоновано інший час"
            text = f"{_appt_line(n, a.entity_id, None)} (було {_when(b.get('starts_at'))})"
        elif act == "client_accept":
            title, text = "Клієнт погодився на новий час", line
        else:
            title = {"confirmed": "Запис підтверджено", "cancelled": "Запис скасовано", "no_show": "Клієнт не прийшов",
                     "in_progress": "Клієнт прийшов", "completed": "Візит завершено", "pending": "Запис очікує"}.get(act, "Запис")
            text = line
            icon = {"cancelled": "cancel", "no_show": "noshow", "in_progress": "play", "confirmed": "ok"}.get(act, "calendar")
    elif e == "order":
        o = n.order(a.entity_id)
        icon = "receipt"
        who = f"{n.service(o.service_id)} · {n.user(o.master_id)} · {n.client(o.client_id)}" if o else ""
        if act == "close":
            title, amount = "Замовлення закрито", af.get("amount")
            text = (f"{who} · {PAY_UA.get(af.get('payment_method'), '')}. "
                    f"Майстру {_fmt_money(af.get('master_earning'))}, салону {_fmt_money(af.get('salon_earning'))}"
                    + (f", матеріали {_fmt_money(af.get('materials_cost'))}" if af.get("materials_cost") else ""))
        else:
            title, amount, text = "Замовлення виправлено", af.get("amount"), who
            changes = _changes(n, b, af)
    elif e == "payout":
        icon, title, amount = "banknote", "Виплата майстру", af.get("amount")
        text = f"{af.get('master_name') or n.user(af.get('master_id'))} · {PAY_UA.get(af.get('method'), af.get('method') or '')}" + (
            " · взято з каси" if af.get("from_till") else "") + (f" · {af['note']}" if af.get("note") else "")
    elif e == "shift":
        icon = "clock"
        if act == "open":
            title, text = "Зміну відкрито", ""
        else:
            title, amount = "Зміну закрито", af.get("revenue")
            text = (f"Клієнтів: {af.get('clients_count', 0)}, заробіток майстра {_fmt_money(af.get('master_earning'))}"
                    + (f", на картку {_fmt_money(af.get('payout_due'))}" if af.get("payout_due") else ""))
    elif e == "expense":
        icon = "wallet"
        x = af or b
        cat = EXPENSE_UA.get(x.get("category"), "Витрата")
        title = {"create": f"Витрата: {cat}", "update": f"Витрату змінено: {cat}", "delete": f"Витрату видалено: {cat}"}.get(act, cat)
        amount = x.get("amount")
        text = x.get("description") or ""
        if act == "update":
            changes = _changes(n, b, af)
    elif e == "material":
        icon, name = "box", n.material(a.entity_id)
        if act == "receipt":
            title, text, amount = f"Прихід на склад: {name}", f"+{af.get('qty', 0):g}", af.get("cost") or None
        elif act == "writeoff":
            title, text = f"Списано зі складу: {name}", f"−{af.get('qty', 0):g}"
        elif act == "adjust":
            title, text = f"Інвентаризація: {name}", f"залишок {b.get('stock', 0):g} → {af.get('stock', 0):g}"
        elif act == "create":
            title = f"Новий матеріал: {name}"
        else:
            title, changes = f"Матеріал змінено: {name}", _changes(n, b, af)
    elif e == "client":
        icon, name = "user", n.client(a.entity_id)
        if act == "create":
            title, text = "Новий клієнт", f"{name} · {af.get('phone', '')}"
        elif act == "rename":
            title, text = "Уточнено ім'я клієнта", f"{b.get('name')} → {af.get('name')}"
        elif act == "reminders":
            title = "Клієнт увімкнув нагадування" if af.get("reminders") else "Клієнт вимкнув нагадування"
            text = name
        else:
            title, text, changes = "Дані клієнта змінено", name, _changes(n, b, af)
    elif e == "user":
        icon, name = "users", n.user(a.entity_id)
        title = {"create": "Новий акаунт", "update": "Акаунт змінено", "change_password": "Змінено пароль",
                 "reset_password": "Пароль скинуто директором", "set_bot_pin": "Встановлено PIN для бота",
                 "bot_login": "Вхід у бот («Для майстрів»)", "link_telegram": "Підключено Telegram",
                 "bot_pin_failed": "Невдала спроба PIN у боті", "locked": "Вхід тимчасово заблоковано"}.get(act, "Акаунт")
        text = name
        if act == "update":
            changes = _changes(n, b, af)
        if act in ("bot_pin_failed", "locked"):
            icon = "lock"
    elif e == "service":
        icon = "scissors"
        x = af or b
        title, text = ("Нова послуга" if act == "create" else "Послугу змінено"), x.get("name", "")
        if act == "update":
            changes = _changes(n, b, af)
    elif e == "setting":
        icon, title, changes = "settings", "Налаштування змінено", _changes(n, b, af)
    elif e == "schedule":
        icon = "clock"
        title = "Графік змінено" if act == "set_day" else "Графік: день як у шаблоні"
        x = af if act == "set_day" else b
        day = _when(x.get("day"))
        hours = "вихідний" if x.get("off") else f"{x.get('start')}–{x.get('end')}"
        text = f"{n.user(a.entity_id)} · {day} · {hours}" if act == "set_day" else f"{n.user(a.entity_id)} · {day}"
    elif e == "callback":
        icon = "phone"
        title = "Прохання передзвонити" if act == "create" else "Клієнту передзвонили"
        text = f"{af.get('name', '')} {af.get('phone', '')}".strip()
    elif e == "gdrive":
        icon = "lock"
        title = "Google Drive підключено" if act == "connect" else "Google Drive відключено"
        text = af.get("account") or ""
    elif e == "meter":
        icon = "zap"
        title = {"create": "Внесено показники лічильника", "update": "Показники лічильника виправлено",
                 "confirm": "Електроенергію за місяць підтверджено"}.get(act, "Електроенергія")
        x = af or {}
        if act in ("create", "update"):
            text = f"{_when(x.get('month'))}: {x.get('used', 0):g} кВт·год × {_fmt_money(x.get('tariff'))}"
            amount = x.get("cost")
        else:
            amount = x.get("cost")
        if act == "update":
            changes = _changes(n, b, af)
    elif e == "generator":
        icon = "zap"
        title = {"start": "Генератор увімкнено", "stop": "Генератор вимкнено", "add": "Запуск генератора додано вручну",
                 "correct": "Запуск генератора виправлено"}.get(act, "Генератор")
        if act in ("stop", "add", "correct"):
            text = f"{af.get('hours', 0):g} мотогод., бензин {af.get('fuel_l', 0):g} л"
            amount = af.get("total_cost")
        elif act == "start":
            text = "без світла" if af.get("power_outage") else ""
        if act == "correct":
            changes = _changes(n, b, af)
    elif e == "master_charge":
        icon = "zap"
        if act == "create":
            title, amount = "Списано з майстра: комунальні / генератор", af.get("amount")
            text = n.user(af.get("master_id"))
        else:
            title, amount, text = "Списання з майстра скасовано", b.get("amount"), ""
    elif e == "bug_report":
        icon, title, text = "bug", "Повідомлення про помилку", (af.get("text") or "")[:160]

    if title is None:
        title, changes = f"{e}: {act}", _changes(n, b, af)
    return {"title": title, "text": text, "amount": amount, "icon": icon,
            "category": _ENTITY_CAT.get(e, "staff"), "changes": changes}


def feed(s: Session, actor: Actor, *, date_from: date, date_to: date, category: str | None = None,
         q: str | None = None, limit: int = 300) -> list[dict]:
    actor.require(Role.DIRECTOR)
    start = datetime.combine(date_from, datetime.min.time())
    end = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)
    stmt = select(AuditLog).where(AuditLog.ts >= start, AuditLog.ts < end)
    if category in CATEGORIES:
        stmt = stmt.where(AuditLog.entity.in_([k for k, v in _ENTITY_CAT.items() if v == category]))
    n = _Names(s)
    needle = (q or "").strip().lower()
    out = []
    rows = list(s.scalars(stmt.order_by(AuditLog.id.desc()).limit(2000)))
    n.prefetch(rows)
    for a in rows:
        d = describe(n, a)
        who, who_kind = _who(n, a)
        item = {"id": a.id, "ts": a.ts.isoformat(), "who": who, "who_kind": who_kind, "reason": a.reason, **d}
        if needle:
            hay = " ".join([d["title"], d["text"] or "", who, a.reason or ""] +
                           [f"{c['field']} {c['before']} {c['after']}" for c in d["changes"]]).lower()
            if needle not in hay:
                continue
        out.append(item)
        if len(out) >= limit:
            break
    return out
