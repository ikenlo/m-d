"""Розділ «Для майстрів» у Telegram-боті.

Вхід у два кроки: спільний код персоналу (налаштування `bot_staff_code`, за замовч. 0000)
→ вибір себе зі списку → особистий PIN. Після 5 невдалих спроб чат блокується на 15 хв.
Повідомлення з кодом і PIN бот видаляє з чату.

Уся логіка й перевірка прав — у сервісах (mds.services); тут лише діалог.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import clock
from ..db import Database
from ..errors import DomainError
from ..models import ApptStatus, Role, ShiftStatus, User
from ..money import fmt
from ..services import analytics, bookings, catalog, orders, users
from ..services.catalog import find_client_by_phone
from ..services.common import Actor
from . import views as v

SESSION_TTL = timedelta(hours=12)
MAX_FAILS = 5
LOCK = timedelta(minutes=15)
DAYS_AHEAD = 14


@dataclass
class StaffSessions:
    """Сесії персоналу в боті (у пам'яті процесу бота; після перезапуску — вхід заново)."""

    sessions: dict[int, tuple[int, datetime]] = field(default_factory=dict)
    code_ok: dict[int, datetime] = field(default_factory=dict)
    fails: dict[int, tuple[int, datetime | None]] = field(default_factory=dict)

    def user_id(self, chat_id: int) -> int | None:
        item = self.sessions.get(chat_id)
        if not item:
            return None
        uid, since = item
        if clock.now() - since > SESSION_TTL:
            self.sessions.pop(chat_id, None)
            return None
        return uid

    def login(self, chat_id: int, user_id: int) -> None:
        self.sessions[chat_id] = (user_id, clock.now())
        self.fails.pop(chat_id, None)
        self.code_ok.pop(chat_id, None)

    def logout(self, chat_id: int) -> None:
        self.sessions.pop(chat_id, None)
        self.code_ok.pop(chat_id, None)

    def locked(self, chat_id: int) -> bool:
        _, until = self.fails.get(chat_id, (0, None))
        return bool(until and until > clock.now())

    def fail(self, chat_id: int) -> None:
        n, _ = self.fails.get(chat_id, (0, None))
        n += 1
        self.fails[chat_id] = (0, clock.now() + LOCK) if n >= MAX_FAILS else (n, None)

    def pass_code(self, chat_id: int) -> None:
        self.code_ok[chat_id] = clock.now()

    def code_passed(self, chat_id: int) -> bool:
        t = self.code_ok.get(chat_id)
        return bool(t and clock.now() - t < timedelta(minutes=10))


class StaffAuth(StatesGroup):
    code = State()
    pin = State()


class AddAppt(StatesGroup):
    phone = State()
    name = State()


class CloseOrder(StatesGroup):
    amount = State()


NOT_FOR_YOU = "⛔ Цей розділ не для вас."
LOCKED = "⛔ Забагато невдалих спроб. Спробуйте через 15 хвилин."
EXPIRED = "Сесія завершилась. Натисніть «👩‍🎨 Для майстрів», щоб увійти знову."


async def _delete(message: Message) -> None:
    """Прибрати з чату повідомлення з кодом/PIN."""
    try:
        await message.delete()
    except Exception:  # noqa: BLE001 — немає прав або повідомлення застаріле
        pass


def _parse_amount(text: str) -> int | None:
    t = text.replace(" ", "").replace(",", ".").replace("₴", "").replace("грн", "")
    try:
        val = float(t)
    except ValueError:
        return None
    if val < 0 or val > 1_000_000:
        return None
    return round(val * 100)


def build_staff_router() -> Router:
    router = Router(name="mds-staff")

    # ------------------------------------------------------------- вхід

    async def show_menu(target: Message, user: User, edit: bool = False) -> None:
        is_dir = user.role == Role.DIRECTOR
        text = f"{'👑' if is_dir else '💇'} <b>{v.e(user.full_name)}</b>\nОберіть дію:"
        markup = v.director_menu_kb() if is_dir else v.master_menu_kb()
        if edit:
            await target.edit_text(text, reply_markup=markup)
        else:
            await target.answer(text, reply_markup=markup)

    @router.message(Command("staff"))
    @router.message(F.text == v.BTN_STAFF)
    async def staff_entry(message: Message, db: Database, staff: StaffSessions, state: FSMContext):
        await state.clear()
        chat = message.chat.id
        uid = staff.user_id(chat)
        if uid:
            with db.session() as s:
                u = s.get(User, uid)
                if u and u.is_active:
                    await show_menu(message, u)
                    return
            staff.logout(chat)
        if staff.locked(chat):
            await message.answer(LOCKED)
            return
        await state.set_state(StaffAuth.code)
        await message.answer("🔐 Введіть код доступу для персоналу:")

    @router.message(StaffAuth.code)
    async def got_code(message: Message, db: Database, staff: StaffSessions, state: FSMContext):
        chat = message.chat.id
        await _delete(message)
        await state.clear()
        if staff.locked(chat):
            await message.answer(LOCKED)
            return
        with db.session() as s:
            ok = users.check_staff_code(s, message.text or "")
            people = users.staff_for_bot(s) if ok else []
            markup = v.staff_people_kb(people)
        if not ok:
            staff.fail(chat)
            await message.answer(NOT_FOR_YOU, reply_markup=v.main_menu())
            return
        staff.pass_code(chat)
        if not people:
            await message.answer("Немає співробітників з PIN для бота. Директор задає PIN у розділі «Персонал».")
            return
        await message.answer("Оберіть себе:", reply_markup=markup)

    @router.callback_query(F.data.startswith("s:who:"))
    async def pick_person(cb: CallbackQuery, db: Database, staff: StaffSessions, state: FSMContext):
        chat = cb.message.chat.id
        if not staff.code_passed(chat):
            await cb.answer(NOT_FOR_YOU, show_alert=True)
            return
        uid = int(cb.data.split(":")[2])
        with db.session() as s:
            u = s.get(User, uid)
            name = u.full_name if u else "?"
        await state.set_state(StaffAuth.pin)
        await state.set_data({"who": uid})
        await cb.message.edit_text(f"{v.e(name)}, введіть ваш особистий PIN:")
        await cb.answer()

    @router.message(StaffAuth.pin)
    async def got_pin(message: Message, db: Database, staff: StaffSessions, state: FSMContext):
        chat = message.chat.id
        await _delete(message)
        data = await state.get_data()
        await state.clear()
        if staff.locked(chat) or not staff.code_passed(chat):
            await message.answer(LOCKED if staff.locked(chat) else NOT_FOR_YOU)
            return
        with db.session() as s:
            u = users.verify_bot_pin(s, data.get("who", 0), message.text or "", chat_id=chat)
            if u is None:
                staff.fail(chat)
                await message.answer("❌ Невірний PIN." + ("\n" + LOCKED if staff.locked(chat) else ""))
                return
            staff.login(chat, u.id)
            await show_menu(message, u)

    @router.callback_query(F.data == "s:exit")
    async def exit_(cb: CallbackQuery, staff: StaffSessions):
        staff.logout(cb.message.chat.id)
        await cb.message.edit_text("Ви вийшли з розділу персоналу.")
        await cb.answer()

    @router.callback_query(F.data == "s:logout")
    async def logout(cb: CallbackQuery, staff: StaffSessions, state: FSMContext):
        staff.logout(cb.message.chat.id)
        await state.clear()
        await cb.message.edit_text("🚪 Ви вийшли. Щоб увійти знову — «👩‍🎨 Для майстрів».")
        await cb.answer()

    # Кожна дія персоналу перевіряє сесію і бере актуального користувача з БД.
    def current(s, staff: StaffSessions, chat_id: int) -> User | None:
        uid = staff.user_id(chat_id)
        if not uid:
            return None
        u = s.get(User, uid)
        if u is None or not u.is_active:
            staff.logout(chat_id)
            return None
        return u

    @router.callback_query(F.data == "s:menu")
    async def menu(cb: CallbackQuery, db: Database, staff: StaffSessions, state: FSMContext):
        await state.clear()
        with db.session() as s:
            u = current(s, staff, cb.message.chat.id)
            if not u:
                await cb.answer(EXPIRED, show_alert=True)
                return
            await show_menu(cb.message, u, edit=True)
        await cb.answer()

    # ---------------------------------------------------------- записи

    def _days(n: int = 7) -> list:
        today = clock.now().date()
        return [today + timedelta(days=i) for i in range(n)]

    @router.callback_query(F.data == "s:list")
    async def list_days(cb: CallbackQuery, db: Database, staff: StaffSessions, state: FSMContext):
        await state.clear()
        with db.session() as s:
            if not current(s, staff, cb.message.chat.id):
                await cb.answer(EXPIRED, show_alert=True)
                return
        await cb.message.edit_text("📋 Записи — оберіть день:", reply_markup=v.staff_days_kb("s:ld", _days()))
        await cb.answer()

    @router.callback_query(F.data.startswith("s:ld:"))
    async def list_day(cb: CallbackQuery, db: Database, staff: StaffSessions):
        day = v.parse_day(cb.data.split(":")[2])
        with db.session() as s:
            u = current(s, staff, cb.message.chat.id)
            if not u:
                await cb.answer(EXPIRED, show_alert=True)
                return
            is_dir = u.role == Role.DIRECTOR
            items = bookings.list_appointments(s, Actor.of(u), date_from=day, date_to=day)
            active = [a for a in items if a.status not in (ApptStatus.CANCELLED, ApptStatus.NO_SHOW)]
            cancelled = len(items) - len(active)
            cards = [(v.staff_appt_text(a, show_master=is_dir), v.staff_appt_kb(a, is_dir)) for a in active]
        head = f"📋 <b>{v.WEEKDAYS[day.weekday()]} {day:%d.%m}</b> — записів: {len(active)}"
        if cancelled:
            head += f" (скасовано/не прийшли: {cancelled})"
        await cb.message.edit_text(head if cards else head + "\nЗаписів немає.",
                                   reply_markup=v.kb([[("« Інший день", "s:list"), ("☰ Меню", "s:menu")]]))
        for text, markup in cards:
            await cb.message.answer(text, reply_markup=markup)
        await cb.answer()

    async def _status(cb: CallbackQuery, db: Database, staff: StaffSessions, status: str, reason: str | None = None):
        appt_id = int(cb.data.split(":")[2])
        try:
            with db.session() as s:
                u = current(s, staff, cb.message.chat.id)
                if not u:
                    await cb.answer(EXPIRED, show_alert=True)
                    return
                a = bookings.set_status(s, Actor.of(u), appt_id, status, reason)
                text = v.staff_appt_text(a, show_master=u.role == Role.DIRECTOR)
                markup = v.staff_appt_kb(a, u.role == Role.DIRECTOR)
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        await cb.message.edit_text(text, reply_markup=markup)
        await cb.answer("Готово")

    @router.callback_query(F.data.startswith("s:cf:"))
    async def confirm_appt(cb: CallbackQuery, db: Database, staff: StaffSessions):
        await _status(cb, db, staff, ApptStatus.CONFIRMED)

    @router.callback_query(F.data.startswith("s:ns:"))
    async def no_show(cb: CallbackQuery, db: Database, staff: StaffSessions):
        await _status(cb, db, staff, ApptStatus.NO_SHOW)

    @router.callback_query(F.data.startswith("s:del:"))
    async def delete_ask(cb: CallbackQuery):
        appt_id = cb.data.split(":")[2]
        await cb.message.edit_reply_markup(reply_markup=v.kb([[("Так, видалити запис", f"s:delok:{appt_id}"), ("Ні", "s:list")]]))
        await cb.answer()

    @router.callback_query(F.data.startswith("s:delok:"))
    async def delete_do(cb: CallbackQuery, db: Database, staff: StaffSessions):
        with db.session() as s:
            u = current(s, staff, cb.message.chat.id)
            who = "директором" if u and u.role == Role.DIRECTOR else "майстром"
        # «Видалення» = скасування: запис лишається в історії й журналі змін
        await _status(cb, db, staff, ApptStatus.CANCELLED, f"Видалено {who} через Telegram")

    # ------------------------------------------------------ перенесення

    @router.callback_query(F.data.startswith("s:mv:"))
    async def move_start(cb: CallbackQuery, db: Database, staff: StaffSessions):
        appt_id = int(cb.data.split(":")[2])
        try:
            with db.session() as s:
                u = current(s, staff, cb.message.chat.id)
                if not u:
                    await cb.answer(EXPIRED, show_alert=True)
                    return
                a = bookings.get_appointment(s, Actor.of(u), appt_id)
                days = [d for d in _days(DAYS_AHEAD) if bookings.free_slots(s, a.master_id, a.service_id, d)]
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        if not days:
            await cb.answer("Немає вільного часу найближчими днями", show_alert=True)
            return
        await cb.message.edit_text("🔁 Новий день:", reply_markup=v.staff_days_kb(f"s:mvd:{appt_id}", days, "s:list"))
        await cb.answer()

    @router.callback_query(F.data.startswith("s:mvd:"))
    async def move_day(cb: CallbackQuery, db: Database, staff: StaffSessions):
        _, _, appt_id, day_s = cb.data.split(":")
        day = v.parse_day(day_s)
        try:
            with db.session() as s:
                u = current(s, staff, cb.message.chat.id)
                if not u:
                    await cb.answer(EXPIRED, show_alert=True)
                    return
                a = bookings.get_appointment(s, Actor.of(u), int(appt_id))
                slots = bookings.free_slots(s, a.master_id, a.service_id, day)
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        await cb.message.edit_text(f"🔁 {v.WEEKDAYS[day.weekday()]} {day:%d.%m} — новий час:",
                                   reply_markup=v.slots_kb(f"s:mvs:{appt_id}", slots, f"s:mv:{appt_id}"))
        await cb.answer()

    @router.callback_query(F.data.startswith("s:mvs:"))
    async def move_do(cb: CallbackQuery, db: Database, staff: StaffSessions):
        _, _, appt_id, dt = cb.data.split(":")
        try:
            with db.session() as s:
                u = current(s, staff, cb.message.chat.id)
                if not u:
                    await cb.answer(EXPIRED, show_alert=True)
                    return
                a = bookings.reschedule(s, Actor.of(u), int(appt_id), starts_at=v.parse_dt(dt))
                text = v.staff_appt_text(a, show_master=u.role == Role.DIRECTOR)
                markup = v.staff_appt_kb(a, u.role == Role.DIRECTOR)
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        await cb.message.edit_text("🔁 Перенесено\n" + text, reply_markup=markup)
        await cb.answer("Перенесено")

    # -------------------------------------------------- закриття замовлення

    @router.callback_query(F.data.startswith("s:cl:"))
    async def close_start(cb: CallbackQuery, db: Database, staff: StaffSessions, state: FSMContext):
        appt_id = int(cb.data.split(":")[2])
        try:
            with db.session() as s:
                u = current(s, staff, cb.message.chat.id)
                if not u:
                    await cb.answer(EXPIRED, show_alert=True)
                    return
                a = bookings.get_appointment(s, Actor.of(u), appt_id)
                price, title = a.price, f"{a.service.name} · {a.client.name}"
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        await state.set_state(CloseOrder.amount)
        await state.set_data({"appt_id": appt_id})
        await cb.message.edit_text(
            f"💳 <b>Закрити замовлення</b>\n{v.e(title)}\n\nСума: <b>{fmt(price)}</b>\n"
            "Оберіть спосіб оплати. Якщо сума інша — надішліть її числом (напр. 750).",
            reply_markup=v.pay_method_kb(appt_id, price),
        )
        await cb.answer()

    @router.message(CloseOrder.amount)
    async def close_amount(message: Message, state: FSMContext):
        data = await state.get_data()
        amount = _parse_amount(message.text or "")
        if amount is None:
            await message.answer("Надішліть суму числом, наприклад 750 або 750.50")
            return
        await message.answer(f"Сума: <b>{fmt(amount)}</b>. Оберіть спосіб оплати:",
                             reply_markup=v.pay_method_kb(data["appt_id"], amount))

    @router.callback_query(F.data.startswith("s:pm:"))
    async def close_do(cb: CallbackQuery, db: Database, staff: StaffSessions, state: FSMContext):
        _, _, appt_id, amount, method = cb.data.split(":")
        try:
            with db.session() as s:
                u = current(s, staff, cb.message.chat.id)
                if not u:
                    await cb.answer(EXPIRED, show_alert=True)
                    return
                o = orders.close_order(s, Actor.of(u), int(appt_id), amount=int(amount), payment_method=method,
                                       idempotency_key=f"tg-close-{appt_id}")
                text = (f"✅ <b>Замовлення закрито</b>\n{v.e(o.service.name)} · {v.e(o.client.name)}\n"
                        f"Сума: {fmt(o.amount)}\nЗаробіток майстра: <b>{fmt(o.master_earning)}</b>")
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        await state.clear()
        await cb.message.edit_text(text, reply_markup=v.kb([[("☰ Меню", "s:menu")]]))
        await cb.answer("Закрито")

    # ------------------------------------------------------- новий запис

    @router.callback_query(F.data == "s:add")
    async def add_start(cb: CallbackQuery, db: Database, staff: StaffSessions, state: FSMContext):
        await state.clear()
        with db.session() as s:
            u = current(s, staff, cb.message.chat.id)
            if not u:
                await cb.answer(EXPIRED, show_alert=True)
                return
            if u.role == Role.DIRECTOR:
                masters = users.list_masters(s)
                markup = v.kb([[(m.full_name, f"s:am:{m.id}")] for m in masters] + [[("« Меню", "s:menu")]])
                text = "➕ Новий запис — оберіть майстра:"
            else:
                text, markup = _services_step(s, u.id)
                text = "➕ " + text
        await cb.message.edit_text(text, reply_markup=markup)
        await cb.answer()

    def _services_step(s, master_id: int, path: list[int] | None = None):
        tree = catalog.service_tree(catalog.list_services(s))
        return v.tree_step(tree, path or [], f"s:t:{master_id}", lambda x: f"s:as:{master_id}:{x.id}", back_root="s:menu")

    @router.callback_query(F.data.startswith("s:t:"))
    async def add_tree(cb: CallbackQuery, db: Database, staff: StaffSessions):
        _, _, master_id, path = cb.data.split(":")
        with db.session() as s:
            if not current(s, staff, cb.message.chat.id):
                await cb.answer(EXPIRED, show_alert=True)
                return
            text, markup = _services_step(s, int(master_id), v.parse_path(path))
        await cb.message.edit_text("➕ " + text, reply_markup=markup)
        await cb.answer()

    @router.callback_query(F.data.startswith("s:am:"))
    async def add_master(cb: CallbackQuery, db: Database, staff: StaffSessions):
        master_id = int(cb.data.split(":")[2])
        with db.session() as s:
            if not current(s, staff, cb.message.chat.id):
                await cb.answer(EXPIRED, show_alert=True)
                return
            text, markup = _services_step(s, master_id)
        await cb.message.edit_text("➕ " + text, reply_markup=markup)
        await cb.answer()

    @router.callback_query(F.data.startswith("s:as:"))
    async def add_service(cb: CallbackQuery, db: Database, staff: StaffSessions):
        _, _, master_id, service_id = cb.data.split(":")
        with db.session() as s:
            if not current(s, staff, cb.message.chat.id):
                await cb.answer(EXPIRED, show_alert=True)
                return
            days = [d for d in _days(DAYS_AHEAD) if bookings.free_slots(s, int(master_id), int(service_id), d)]
        if not days:
            await cb.answer("Немає вільного часу найближчими днями", show_alert=True)
            return
        await cb.message.edit_text("➕ Оберіть день:", reply_markup=v.staff_days_kb(f"s:ad:{master_id}:{service_id}", days, "s:add"))
        await cb.answer()

    @router.callback_query(F.data.startswith("s:ad:"))
    async def add_day(cb: CallbackQuery, db: Database, staff: StaffSessions):
        _, _, master_id, service_id, day_s = cb.data.split(":")
        day = v.parse_day(day_s)
        with db.session() as s:
            if not current(s, staff, cb.message.chat.id):
                await cb.answer(EXPIRED, show_alert=True)
                return
            slots = bookings.free_slots(s, int(master_id), int(service_id), day)
        await cb.message.edit_text(f"➕ {v.WEEKDAYS[day.weekday()]} {day:%d.%m} — оберіть час:",
                                   reply_markup=v.slots_kb(f"s:at:{master_id}:{service_id}", slots, f"s:as:{master_id}:{service_id}"))
        await cb.answer()

    @router.callback_query(F.data.startswith("s:at:"))
    async def add_time(cb: CallbackQuery, db: Database, staff: StaffSessions, state: FSMContext):
        _, _, master_id, service_id, dt = cb.data.split(":")
        with db.session() as s:
            if not current(s, staff, cb.message.chat.id):
                await cb.answer(EXPIRED, show_alert=True)
                return
        await state.set_state(AddAppt.phone)
        await state.set_data({"master_id": int(master_id), "service_id": int(service_id), "dt": dt})
        await cb.message.edit_text(f"➕ {v.parse_dt(dt):%d.%m о %H:%M}\n📞 Надішліть телефон клієнта:")
        await cb.answer()

    async def _create(message: Message, db: Database, staff: StaffSessions, state: FSMContext, name: str, phone: str):
        data = await state.get_data()
        try:
            with db.session() as s:
                u = current(s, staff, message.chat.id)
                if not u:
                    await state.clear()
                    await message.answer(EXPIRED)
                    return
                a = bookings.create_appointment(
                    s, Actor.of(u), master_id=data["master_id"], service_id=data["service_id"],
                    starts_at=v.parse_dt(data["dt"]), client_name=name, client_phone=phone,
                )
                text = v.staff_appt_text(a, show_master=u.role == Role.DIRECTOR)
        except DomainError as e:
            await state.clear()
            await message.answer(f"⚠️ {e.message}", reply_markup=v.kb([[("➕ Спробувати ще", "s:add"), ("☰ Меню", "s:menu")]]))
            return
        await state.clear()
        await message.answer("✅ Запис створено\n" + text, reply_markup=v.kb([[("➕ Ще запис", "s:add"), ("☰ Меню", "s:menu")]]))

    @router.message(AddAppt.phone)
    async def add_phone(message: Message, db: Database, staff: StaffSessions, state: FSMContext):
        phone = (message.text or "").strip()
        try:
            with db.session() as s:
                c = find_client_by_phone(s, phone)
                known = c.name if c else None
        except DomainError as e:
            await message.answer(f"{e.message}. Надішліть номер, наприклад 0671234567")
            return
        if known:
            await _create(message, db, staff, state, known, phone)
            return
        await state.update_data(phone=phone)
        await state.set_state(AddAppt.name)
        await message.answer("Новий клієнт. Як його звати?")

    @router.message(AddAppt.name)
    async def add_name(message: Message, db: Database, staff: StaffSessions, state: FSMContext):
        name = (message.text or "").strip()
        if not name:
            await message.answer("Надішліть ім'я клієнта")
            return
        data = await state.get_data()
        await _create(message, db, staff, state, name, data["phone"])

    # -------------------------------------------------- заробіток і зміна

    @router.callback_query(F.data == "s:earn")
    async def earnings(cb: CallbackQuery, db: Database, staff: StaffSessions):
        with db.session() as s:
            u = current(s, staff, cb.message.chat.id)
            if not u or u.role != Role.MASTER:
                await cb.answer(EXPIRED, show_alert=True)
                return
            my = analytics.my_earnings(s, Actor.of(u))
        b = my["balance"]
        text = (
            "💰 <b>Мій заробіток</b>\n\n"
            f"Сьогодні: <b>{fmt(my['day']['earning'])}</b> ({my['day']['orders_count']} посл.)\n"
            f"Цей тиждень: <b>{fmt(my['week']['earning'])}</b> ({my['week']['orders_count']} посл.)\n"
            f"Цей місяць: <b>{fmt(my['month']['earning'])}</b> ({my['month']['orders_count']} посл.)\n\n"
            f"Нараховано всього: {fmt(b['accrued'])}\nВиплачено: {fmt(b['paid'])}\n"
            f"<b>До виплати: {fmt(b['balance'])}</b>"
        )
        await cb.message.edit_text(text, reply_markup=v.kb([[("☰ Меню", "s:menu")]]))
        await cb.answer()

    @router.callback_query(F.data == "s:shift")
    async def shift_ask(cb: CallbackQuery, db: Database, staff: StaffSessions):
        with db.session() as s:
            u = current(s, staff, cb.message.chat.id)
            if not u or u.role != Role.MASTER:
                await cb.answer(EXPIRED, show_alert=True)
                return
            sh = orders.current_shift(s, u.id)
            if not sh:
                await cb.answer("Відкритої зміни немає — вона відкривається з першим закритим замовленням.", show_alert=True)
                return
            r = orders.shift_report(s, sh)
        await cb.message.edit_text(
            f"🔒 Закрити зміну?\nПослуг: {r['services_count']}, виручка {fmt(r['revenue'])}, до виплати {fmt(r['payout_due'])}",
            reply_markup=v.kb([[("Так, закрити зміну", "s:shiftok"), ("« Меню", "s:menu")]]),
        )
        await cb.answer()

    @router.callback_query(F.data == "s:shiftok")
    async def shift_close(cb: CallbackQuery, db: Database, staff: StaffSessions):
        try:
            with db.session() as s:
                u = current(s, staff, cb.message.chat.id)
                if not u:
                    await cb.answer(EXPIRED, show_alert=True)
                    return
                r = orders.close_shift(s, Actor.of(u), u.id).report
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        lines = "\n".join(f"• {v.e(x['service'])} × {x['count']} — {fmt(x['revenue'])}" for x in r["services"])
        await cb.message.edit_text(
            "📋 <b>Звіт зміни</b>\n"
            f"Клієнтів: {r['clients_count']} · послуг: {r['services_count']} · скасувань: {r['cancellations']}\n"
            f"{lines}\n\nВиручка: <b>{fmt(r['revenue'])}</b>\nВаш заробіток: <b>{fmt(r['master_earning'])}</b>\n"
            f"До виплати: <b>{fmt(r['payout_due'])}</b>\n\nЗвіт надіслано директору.",
            reply_markup=v.kb([[("☰ Меню", "s:menu")]]),
        )
        await cb.answer()

    # ------------------------------------------------------- директор

    def _director(s, staff: StaffSessions, chat_id: int) -> User | None:
        u = current(s, staff, chat_id)
        return u if u and u.role == Role.DIRECTOR else None

    @router.callback_query(F.data == "s:fin")
    async def finance_menu(cb: CallbackQuery, db: Database, staff: StaffSessions):
        await _finance(cb, db, staff, "day")

    @router.callback_query(F.data.startswith("s:fin:"))
    async def finance(cb: CallbackQuery, db: Database, staff: StaffSessions):
        await _finance(cb, db, staff, cb.data.split(":")[2])

    async def _finance(cb: CallbackQuery, db: Database, staff: StaffSessions, period: str):
        with db.session() as s:
            u = _director(s, staff, cb.message.chat.id)
            if not u:
                await cb.answer(EXPIRED, show_alert=True)
                return
            a, b = analytics.period_range(period)
            d = analytics.dashboard(s, Actor.of(u), a, b)
            bm = analytics.by_master(s, Actor.of(u), a, b)
        names = {"day": "сьогодні", "week": "цей тиждень", "month": "цей місяць", "year": "цей рік"}
        masters = "\n".join(f"• {v.e(m['master_name'])}: {fmt(m['revenue'])} (майстру {fmt(m['masters_share'])})"
                            for m in bm if m["orders_count"])
        text = (
            f"📊 <b>Фінанси за {names.get(period, period)}</b>\n\n"
            f"Виручка: <b>{fmt(d['revenue'])}</b> ({d['orders_count']} замовл., сер. чек {fmt(d['average_check'])})\n"
            f"Частка майстрів: {fmt(d['masters_share'])}\nЧастка салону: {fmt(d['salon_share'])}\n"
            f"Матеріали: {fmt(d['materials'])}\n"
            f"Оренда / комунальні / реклама / інше: {fmt(d['expenses']['rent'])} / {fmt(d['expenses']['utilities'])} / "
            f"{fmt(d['expenses']['advertising'])} / {fmt(d['expenses']['other'])}\n\n"
            f"Валовий прибуток: <b>{fmt(d['gross_profit'])}</b>\nЧистий прибуток: <b>{fmt(d['net_profit'])}</b>\n"
            f"Виплачено майстрам: {fmt(d['payouts'])}\nБорг по зарплаті: {fmt(d['salary_debt_total'])}"
            + (f"\n\n<b>По майстрах</b>\n{masters}" if masters else "")
        )
        markup = v.kb([[("День", "s:fin:day"), ("Тиждень", "s:fin:week"), ("Місяць", "s:fin:month"), ("Рік", "s:fin:year")],
                       [("☰ Меню", "s:menu")]])
        try:
            await cb.message.edit_text(text, reply_markup=markup)
        except Exception:  # noqa: BLE001 — той самий текст (повторне натискання)
            pass
        await cb.answer()

    @router.callback_query(F.data == "s:pay")
    async def payouts(cb: CallbackQuery, db: Database, staff: StaffSessions):
        with db.session() as s:
            if not _director(s, staff, cb.message.chat.id):
                await cb.answer(EXPIRED, show_alert=True)
                return
        await _render_payouts(cb.message, db, staff)
        await cb.answer()

    async def _render_payouts(message: Message, db: Database, staff: StaffSessions) -> None:
        with db.session() as s:
            u = _director(s, staff, message.chat.id)
            shifts = orders.list_shifts(s, Actor.of(u), status=ShiftStatus.CLOSED)
            rows = [[(f"{sh.master.full_name} · {sh.closed_at:%d.%m} · {fmt(sh.payout_due or 0)}", f"s:po:{sh.id}")] for sh in shifts]
        text = "💸 <b>Зміни, що очікують виплати</b>\nНатисніть, щоб підтвердити виплату готівкою:" if rows else "💸 Немає змін, що очікують виплати."
        await message.edit_text(text, reply_markup=v.kb(rows + [[("☰ Меню", "s:menu")]]))

    @router.callback_query(F.data.startswith("s:po:"))
    async def payout_do(cb: CallbackQuery, db: Database, staff: StaffSessions):
        shift_id = int(cb.data.split(":")[2])
        try:
            with db.session() as s:
                u = _director(s, staff, cb.message.chat.id)
                if not u:
                    await cb.answer(EXPIRED, show_alert=True)
                    return
                sh = orders.get_shift(s, Actor.of(u), shift_id)
                p = orders.confirm_payout(s, Actor.of(u), master_id=sh.master_id, amount=sh.payout_due or 0, shift_id=sh.id)
                msg = f"Виплачено {sh.master.full_name}: {fmt(p.amount)}"
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        await _render_payouts(cb.message, db, staff)
        await cb.answer(msg, show_alert=True)

    return router
