"""Тексти та клавіатури бота (без звернень до Telegram — легко тестувати)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape as e

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from .. import clock
from ..models import Appointment, ApptStatus, Service, User
from ..money import fmt

BTN_SERVICES = "💅 Послуги та ціни"
BTN_BOOK = "📅 Записатися"
BTN_MY = "🗓 Мої записи"
BTN_FREE = "🕐 Вільний час"
BTN_CONTACTS = "📍 Контакти"
BTN_STAFF = "👩‍🎨 Для майстрів"
BTN_QUICK = "⚡ Швидкий запис"
BTN_CANCEL = "✖️ Скасувати"

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
STATUS_UA = {
    ApptStatus.PENDING: "очікує підтвердження",
    ApptStatus.CONFIRMED: "підтверджено",
    ApptStatus.IN_PROGRESS: "триває",
    ApptStatus.COMPLETED: "завершено",
    ApptStatus.CANCELLED: "скасовано",
    ApptStatus.NO_SHOW: "не з'явився",
}


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK), KeyboardButton(text=BTN_QUICK)],
            [KeyboardButton(text=BTN_SERVICES), KeyboardButton(text=BTN_FREE)],
            [KeyboardButton(text=BTN_CONTACTS), KeyboardButton(text=BTN_STAFF)],
        ],
        resize_keyboard=True,
    )


def contact_request() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поділитися номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def quick_contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Надіслати мій номер", request_contact=True)], [KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def price_text(x: Service) -> str:
    """Ціна для клієнта: «450 ₴» або «від 550 ₴»."""
    return ("від " if x.price_from else "") + fmt(x.price)


def services_text(services: list[Service], salon: str) -> str:
    from ..services.catalog import service_tree

    if not services:
        return "Список послуг поки порожній."
    lines = [f"✨ <b>Послуги салону {e(salon)}</b>"]
    for sec, cats in service_tree(services):
        lines.append(f"\n<b>{e(sec).upper()}</b>")
        for cat, subs in cats:
            if len(cats) > 1 or cat != "Послуги":
                lines.append(f"\n<b><i>{e(cat)}</i></b>")
            for sub, items in subs:
                if sub:
                    lines.append(f"<i>{e(sub)}</i>")
                # тривалість клієнтам не показуємо — вона потрібна лише календарю
                lines += [f"• {e(x.name)} — <b>{price_text(x)}</b>" for x in items]
    return "\n".join(lines)


SECTION_ICON = {"чоловічі": "💈", "жіночі": "💇‍♀️"}
LEVEL_PROMPT = ["Оберіть розділ:", "оберіть тип послуги:", "оберіть вид:", "оберіть послугу:"]


def _children(tree: list, path: list[int]) -> list:
    node = tree
    for i in path:
        node = node[i][1]
    return node


def _auto(children: list) -> bool:
    """Рівень, де вибору немає (один варіант), пропускається."""
    return len(children) == 1 and not isinstance(children[0], Service)


def tree_step(tree: list, path: list[int], prefix: str, leaf_cb, back_root: str | None = None):
    """Один крок вибору послуги в дереві каталогу.

    path — індекси вже обраних вузлів; prefix — початок callback_data
    (вузли кодуються як "<prefix>:1.0.2"); leaf_cb(service) -> callback_data послуги.
    Повертає (текст, клавіатуру). Рівні з одним варіантом пропускаються автоматично.
    """
    if path and any(i >= len(_children(tree, path[:k])) for k, i in enumerate(path)):
        path = []  # каталог змінився — починаємо з початку
    children = _children(tree, path)
    while children and _auto(children):
        path = path + [0]
        children = _children(tree, path)
    labels, node = [], tree
    for i in path:
        if node[i][0] and node[i][0] != "Послуги":
            labels.append(node[i][0])
        node = node[i][1]
    leaf = bool(children) and isinstance(children[0], Service)
    if leaf:
        rows = [[(f"{x.name} · {price_text(x)}", leaf_cb(x))] for x in children]
        prompt = LEVEL_PROMPT[-1]
    else:
        def label(name: str) -> str:
            return f"{SECTION_ICON.get(name.lower(), '✨')} {name}" if not path else name
        rows = [[(label(name), f"{prefix}:{'.'.join(map(str, path + [i]))}")] for i, (name, _) in enumerate(children)]
        prompt = LEVEL_PROMPT[min(len(path), 2)]
    # «Назад» — до найближчого рівня, де був вибір
    back_path = path[:-1]
    while back_path and not len(_children(tree, back_path)) > 1:
        back_path = back_path[:-1]
    if path and (back_path or len(_children(tree, [])) > 1):
        rows.append([("« Назад", f"{prefix}:{'.'.join(map(str, back_path))}")])
    elif back_root:
        rows.append([("« Назад", back_root)])
    title = " · ".join(e(x) for x in labels)
    text = f"<b>{title}</b> — {prompt}" if title else prompt[0].upper() + prompt[1:]
    return text, kb(rows)


def parse_path(v: str) -> list[int]:
    return [int(x) for x in v.split(".") if x != ""]


MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня",
              "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
WEEKDAYS_LONG = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]


def short_name(full_name: str) -> str:
    """Для клієнтів — лише ім'я майстра, без прізвища."""
    return (full_name or "").split()[0] if full_name else ""


def day_label(d: date, long: bool = False) -> str:
    """«Сьогодні», «Завтра», «Пт, 6 листопада» (long: «П'ятниця, 6 листопада»)."""
    today = clock.now().date()
    if d == today:
        return "Сьогодні"
    if d == today + timedelta(days=1):
        return "Завтра"
    wd = WEEKDAYS_LONG[d.weekday()] if long else WEEKDAYS[d.weekday()]
    return f"{wd}, {d.day} {MONTHS_GEN[d.month - 1]}"


def day_title(d: date) -> str:
    """Заголовок: «Сьогодні, 2 листопада» / «П'ятниця, 6 листопада»."""
    lbl = day_label(d, long=True)
    return f"{lbl}, {d.day} {MONTHS_GEN[d.month - 1]}" if lbl in ("Сьогодні", "Завтра") else lbl


def when_label(t: datetime) -> str:
    """Найближчий час: «сьогодні о 14:00», «завтра о 10:00», «пт, 6 листопада о 10:00»."""
    lbl = day_label(t.date())
    return f"{lbl[0].lower() + lbl[1:]} о {t:%H:%M}"


def masters_kb(service_id: int, masters: list[tuple[User, datetime]], any_first: datetime | None,
               favorite_id: int | None = None) -> InlineKeyboardMarkup:
    """masters: [(майстер, найближчий вільний час)] — показуємо лише вільних.
    Улюблений майстер клієнта — першою кнопкою з ⭐."""
    rows = []
    fav = [(m, t) for m, t in masters if m.id == favorite_id]
    rest = [(m, t) for m, t in masters if m.id != favorite_id]
    rows += [[(f"⭐ Ваш майстер {short_name(m.full_name)} · {when_label(t)}", f"mst:{service_id}:{m.id}")] for m, t in fav]
    if any_first and len(masters) > 1:
        rows.append([(f"👥 Будь-хто · {when_label(any_first)}", f"mst:{service_id}:0")])
    rows += [[(f"{short_name(m.full_name)} · {when_label(t)}", f"mst:{service_id}:{m.id}")] for m, t in rest]
    rows.append([("« Назад", "book")])
    return kb(rows)


def days_kb(prefix: str, days: list[date], back: str, counts: dict[date, int] | None = None) -> InlineKeyboardMarkup:
    """Дні по два в ряд: «Сьогодні · 6 віл.», «Пт, 6 листопада»."""
    rows, row = [], []
    for d in days:
        label = day_label(d)
        if counts and counts.get(d):
            label += f" · {counts[d]} віл."
        row.append((label, f"{prefix}:{d:%Y%m%d}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("« Назад", back)])
    return kb(rows)


DAY_PARTS = [("🌅 Ранок", 0, 12), ("☀️ День", 12, 17), ("🌙 Вечір", 17, 24)]


def slots_kb(prefix: str, slots: list[datetime], back: str) -> InlineKeyboardMarkup:
    """Час, згрупований за частинами дня (заголовки груп — неактивні кнопки)."""
    rows = []
    for title, h0, h1 in DAY_PARTS:
        part = [t for t in slots if h0 <= t.hour < h1]
        if not part:
            continue
        rows.append([(title, "nop")])
        for i in range(0, len(part), 4):
            rows.append([(t.strftime("%H:%M"), f"{prefix}:{t:%Y%m%d%H%M}") for t in part[i:i + 4]])
    rows.append([("« Інший день", back)])
    return kb(rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return kb([[("✅ Підтвердити запис", "ok")], [("✖️ Скасувати", "abort")]])


def appt_text(a: Appointment) -> str:
    return (
        f"<b>{day_title(a.starts_at.date())} о {a.starts_at:%H:%M}</b>\n"
        f"{e(a.service.name)} · {('від ' if a.service.price_from else '') + fmt(a.price)}\nМайстер: {e(short_name(a.master.full_name))}\n"
        f"Статус: {STATUS_UA.get(a.status, a.status)}"
    )


def my_appt_kb(a: Appointment) -> InlineKeyboardMarkup:
    return kb([[("🔁 Перенести", f"mv:{a.id}"), ("❌ Скасувати", f"cnl:{a.id}")]])


def cancel_confirm_kb(appt_id: int) -> InlineKeyboardMarkup:
    return kb([[("Так, скасувати", f"cnlok:{appt_id}"), ("Ні", "noop")]])


def summary_text(service: Service, master_name: str, start: datetime) -> str:
    return (
        "Перевірте запис:\n\n"
        f"<b>{e(service.name)}</b> · {price_text(service)}\n"
        f"Майстер: {e(short_name(master_name) if master_name != 'будь-який вільний' else master_name)}\n"
        f"Коли: <b>{day_title(start.date())} о {start:%H:%M}</b>"
    )


def parse_day(v: str) -> date:
    return datetime.strptime(v, "%Y%m%d").date()


def parse_dt(v: str) -> datetime:
    return datetime.strptime(v, "%Y%m%d%H%M")


def horizon(today: date, days: int) -> list[date]:
    return [today + timedelta(days=i) for i in range(days)]


def contacts_text(st: dict) -> str:
    lines = [f"<b>Салон {e(st.get('salon_name') or 'M&D')}</b>"]
    if st.get("salon_address"):
        lines.append(f"📍 {e(st['salon_address'])}")
    if st.get("salon_phone"):
        lines.append(f"📞 {e(st['salon_phone'])}")
    if st.get("salon_instagram"):
        lines.append(f"📷 Instagram: {e(st['salon_instagram'])}")
    sched = st.get("schedule") or {}
    lines.append("\n<b>Графік роботи</b>")
    for i, name in enumerate(["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]):
        span = sched.get(str(i))
        lines.append(f"{name}: {span[0]}–{span[1]}" if span else f"{name}: вихідний")
    return "\n".join(lines)


def free_text(day: date, rows: list[tuple[str, list[tuple[datetime, datetime]]]]) -> str:
    head = f"🕐 <b>Вільний час · {day_title(day)}</b>\n"
    if not any(w for _, w in rows):
        return head + "\nНа цей день вільного часу немає. Оберіть інший день 👇"
    out = [head]
    for name, wins in rows:
        spans = ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in wins) if wins else "зайнято"
        out.append(f"<b>{e(short_name(name))}</b>: {spans}")
    out.append("\n<i>Час рахується за графіком майстрів і вже зробленими записами.</i>")
    return "\n".join(out)


# ------------------------------------------------------------------ персонал

def staff_people_kb(people: list[User]) -> InlineKeyboardMarkup:
    rows = [[(("👑 " if u.role == "director" else "💇 ") + u.full_name, f"s:who:{u.id}")] for u in people]
    rows.append([("✖️ Вийти", "s:exit")])
    return kb(rows)


def master_menu_kb() -> InlineKeyboardMarkup:
    return kb([
        [("➕ Додати запис", "s:add")],
        [("📋 Мої записи", "s:list")],
        [("💰 Мій заробіток", "s:earn"), ("🔒 Закрити зміну", "s:shift")],
        [("🚪 Вийти", "s:logout")],
    ])


def director_menu_kb() -> InlineKeyboardMarkup:
    return kb([
        [("📅 Усі записи", "s:list"), ("➕ Додати запис", "s:add")],
        [("📞 Передзвонити", "s:calls"), ("📊 Фінанси", "s:fin")],
        [("💸 Виплати", "s:pay"), ("🚪 Вийти", "s:logout")],
    ])


def staff_days_kb(prefix: str, days: list[date], back: str = "s:menu", counts: dict | None = None) -> InlineKeyboardMarkup:
    return days_kb(prefix, days, back)


STATUS_ICON = {
    ApptStatus.PENDING: "⏳", ApptStatus.CONFIRMED: "✅", ApptStatus.IN_PROGRESS: "▶️",
    ApptStatus.COMPLETED: "💳", ApptStatus.CANCELLED: "❌", ApptStatus.NO_SHOW: "🚫",
}


def staff_appt_text(a: Appointment, show_master: bool) -> str:
    return (
        f"{STATUS_ICON.get(a.status, '')} <b>{a.starts_at:%H:%M}–{a.ends_at:%H:%M}</b> · {e(a.service.name)}\n"
        f"👤 {e(a.client.name)}, {e(a.client.phone)}\n"
        + (f"💇 {e(a.master.full_name)}\n" if show_master else "")
        + f"💵 {fmt(a.price)} · {STATUS_UA.get(a.status, a.status)}"
    )


def staff_appt_kb(a: Appointment, is_director: bool) -> InlineKeyboardMarkup | None:
    if a.status not in (ApptStatus.PENDING, ApptStatus.CONFIRMED, ApptStatus.IN_PROGRESS):
        return None
    rows = []
    if a.status == ApptStatus.PENDING:
        rows.append([("✅ Підтвердити", f"s:cf:{a.id}"), ("⛔ Зайнято", f"s:busy:{a.id}")])
    rows.append([("💳 Закрити замовлення", f"s:cl:{a.id}")])
    row = [("🕐 Інший час" if a.status == ApptStatus.PENDING else "🔁 Перенести", f"s:mv:{a.id}")]
    row.append(("🗑 Видалити", f"s:del:{a.id}") if is_director else ("❌ Скасувати", f"s:del:{a.id}"))
    rows.append(row)
    if a.status != ApptStatus.IN_PROGRESS:
        rows.append([("🚫 Не прийшов", f"s:ns:{a.id}")])
    return kb(rows)


def pay_method_kb(appt_id: int, amount: int) -> InlineKeyboardMarkup:
    return kb([
        [("💵 Готівка", f"s:pm:{appt_id}:{amount}:cash"), ("💳 Картка", f"s:pm:{appt_id}:{amount}:card")],
        [("« Назад", "s:list")],
    ])
