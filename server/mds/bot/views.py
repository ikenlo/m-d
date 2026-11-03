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

from ..models import Appointment, ApptStatus, Service, User
from ..money import fmt

BTN_SERVICES = "💅 Послуги та ціни"
BTN_BOOK = "📅 Записатися"
BTN_MY = "🗓 Мої записи"

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
        keyboard=[[KeyboardButton(text=BTN_BOOK)], [KeyboardButton(text=BTN_SERVICES), KeyboardButton(text=BTN_MY)]],
        resize_keyboard=True,
    )


def contact_request() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поділитися номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def _dur(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return (f"{h} год " if h else "") + (f"{m} хв" if m else "").strip()


def services_text(services: list[Service], salon: str) -> str:
    if not services:
        return "Список послуг поки порожній."
    lines = [f"✨ <b>Послуги салону {salon}</b>\n"]
    cat = object()
    for x in services:
        if x.category != cat:
            cat = x.category
            if cat:
                lines.append(f"\n<b>{e(cat)}</b>")
        lines.append(f"• {e(x.name)} — <b>{fmt(x.price)}</b> ({_dur(x.duration_min).strip()})")
    return "\n".join(lines)


def services_kb(services: list[Service]) -> InlineKeyboardMarkup:
    return _kb([[(f"{x.name} · {fmt(x.price)}", f"svc:{x.id}")] for x in services])


def masters_kb(service_id: int, masters: list[User]) -> InlineKeyboardMarkup:
    rows = [[("👥 Будь-який вільний майстер", f"mst:{service_id}:0")]]
    rows += [[(m.full_name, f"mst:{service_id}:{m.id}")] for m in masters]
    rows.append([("« Назад", "book")])
    return _kb(rows)


def days_kb(prefix: str, days: list[date], back: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for d in days:
        row.append((f"{WEEKDAYS[d.weekday()]} {d:%d.%m}", f"{prefix}:{d:%Y%m%d}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("« Назад", back)])
    return _kb(rows)


def slots_kb(prefix: str, slots: list[datetime], back: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for t in slots:
        row.append((t.strftime("%H:%M"), f"{prefix}:{t:%Y%m%d%H%M}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("« Інший день", back)])
    return _kb(rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return _kb([[("✅ Підтвердити запис", "ok")], [("✖️ Скасувати", "abort")]])


def appt_text(a: Appointment) -> str:
    return (
        f"<b>{WEEKDAYS[a.starts_at.weekday()]} {a.starts_at:%d.%m.%Y о %H:%M}</b>\n"
        f"{e(a.service.name)} · {fmt(a.price)}\nМайстер: {e(a.master.full_name)}\n"
        f"Статус: {STATUS_UA.get(a.status, a.status)}"
    )


def my_appt_kb(a: Appointment) -> InlineKeyboardMarkup:
    return _kb([[("🔁 Перенести", f"mv:{a.id}"), ("❌ Скасувати", f"cnl:{a.id}")]])


def cancel_confirm_kb(appt_id: int) -> InlineKeyboardMarkup:
    return _kb([[("Так, скасувати", f"cnlok:{appt_id}"), ("Ні", "noop")]])


def summary_text(service: Service, master_name: str, start: datetime) -> str:
    return (
        "Перевірте запис:\n\n"
        f"<b>{e(service.name)}</b> · {fmt(service.price)}\n"
        f"Майстер: {e(master_name)}\n"
        f"Дата: <b>{WEEKDAYS[start.weekday()]} {start:%d.%m.%Y о %H:%M}</b>\n"
        f"Тривалість: {_dur(service.duration_min).strip()}"
    )


def parse_day(v: str) -> date:
    return datetime.strptime(v, "%Y%m%d").date()


def parse_dt(v: str) -> datetime:
    return datetime.strptime(v, "%Y%m%d%H%M")


def horizon(today: date, days: int) -> list[date]:
    return [today + timedelta(days=i) for i in range(days)]
