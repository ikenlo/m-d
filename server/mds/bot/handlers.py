"""Обробники бота. Уся логіка — у сервісному шарі; тут лише діалог."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select

from .. import clock
from ..db import Database
from ..errors import DomainError
from ..models import ApptStatus, Client, Role, Service, User
from ..services import bookings, callbacks, catalog, reminders, users
from ..services.common import Actor, all_settings, get_setting
from . import views as v


MAX_DAYS_SHOWN = 14


class Booking(StatesGroup):
    waiting_contact = State()
    confirming = State()


class QuickCall(StatesGroup):
    phone = State()


MENU_TEXTS = {v.BTN_BOOK, v.BTN_QUICK, v.BTN_SERVICES, v.BTN_FREE, v.BTN_CONTACTS, v.BTN_STAFF, v.BTN_MY}


def _client_by_tg(s, tg_id: int) -> Client | None:
    return s.scalar(select(Client).where(Client.telegram_user_id == tg_id))


def build_router() -> Router:
    """Новий роутер з усіма обробниками (роутер aiogram можна підключити лише до одного диспетчера)."""
    from .staff import build_staff_router

    router = Router(name="mds")
    router.include_router(build_staff_router())

    # ------------------------------------------------------------------ меню

    @router.message(CommandStart())
    async def start(message: Message, db: Database, state: FSMContext):
        await state.clear()
        with db.session() as s:
            salon = get_setting(s, "salon_name")
        await message.answer(
            f"Вітаємо в салоні <b>{salon}</b> ✨\n\n"
            "Тут можна переглянути послуги та ціни, записатися до майстра, "
            "перенести або скасувати свій запис.",
            reply_markup=v.main_menu(),
        )


    @router.message(Command("services"))
    @router.message(F.text == v.BTN_SERVICES)
    async def show_services(message: Message, db: Database):
        with db.session() as s:
            text = v.services_text(catalog.list_services(s), get_setting(s, "salon_name"))
        await message.answer(text, reply_markup=v.main_menu())


    # ---------------------------------------------------- швидкий запис
    @router.message(Command("call"))
    @router.message(F.text == v.BTN_QUICK)
    async def quick_start(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(QuickCall.phone)
        await message.answer(
            "⚡ <b>Швидкий запис</b>\n\nЗалиште номер телефону — адміністратор зателефонує вам "
            "і запише на зручний час.\n\nНатисніть кнопку нижче або напишіть номер, наприклад 0671234567.",
            reply_markup=v.quick_contact_kb(),
        )

    @router.message(QuickCall.phone)
    async def quick_phone(message: Message, db: Database, state: FSMContext):
        if message.text in MENU_TEXTS or message.text == v.BTN_CANCEL:
            await state.clear()
            await message.answer("Швидкий запис скасовано.", reply_markup=v.main_menu())
            return
        if message.contact:
            phone = message.contact.phone_number
            name = " ".join(x for x in (message.contact.first_name, message.contact.last_name) if x)
        else:
            phone, name = message.text or "", message.from_user.full_name
        try:
            with db.session() as s:
                callbacks.create_request(s, Actor.telegram(message.from_user.id), phone, name)
        except DomainError:
            await message.answer("Не вдалося розпізнати номер. Напишіть, наприклад, 0671234567 — або натисніть кнопку.",
                                 reply_markup=v.quick_contact_kb())
            return
        await state.clear()
        await message.answer("✅ Дякуємо! Ми зателефонуємо вам найближчим часом.", reply_markup=v.main_menu())

    @router.message(Command("contacts"))
    @router.message(F.text == v.BTN_CONTACTS)
    async def contacts(message: Message, db: Database):
        with db.session() as s:
            text = v.contacts_text(all_settings(s))
        await message.answer(text, reply_markup=v.main_menu())

    @router.message(Command("free"))
    @router.message(F.text == v.BTN_FREE)
    async def free_days(message: Message, state: FSMContext):
        await state.clear()
        days = v.horizon(clock.now().date(), 7)
        await message.answer(
            "🕐 <b>Вільний час</b>\nОберіть день — покажу, коли кожен майстер вільний:",
            reply_markup=v.days_kb("free", days, "book"),
        )

    @router.callback_query(F.data.startswith("free:"))
    async def free_day(cb: CallbackQuery, db: Database):
        day = v.parse_day(cb.data.split(":")[1])
        with db.session() as s:
            rows = [(m.full_name, bookings.free_windows(s, m.id, day)) for m in users.list_masters(s)]
        days = v.horizon(clock.now().date(), 7)
        days_rows = v.days_kb("free", days, "book").inline_keyboard[:-1]
        markup = InlineKeyboardMarkup(inline_keyboard=[*days_rows, *v.kb([[("📅 Записатися", "book")]]).inline_keyboard])
        await cb.message.edit_text(v.free_text(day, rows), reply_markup=markup)
        await cb.answer()

    # Запис: розділ (Чоловічі/Жіночі) → тип (Стрижка/Фарбування…) → вид → послуга → майстер → день → час.
    # Рівень пропускається, якщо в ньому лише один варіант (див. views.tree_step).
    def _step(s, path: list[int]):
        tree = catalog.service_tree(catalog.list_services(s))
        if not tree:
            return None, None
        return v.tree_step(tree, path, "t", lambda x: f"svc:{x.id}")

    @router.message(Command("book"))
    @router.message(F.text == v.BTN_BOOK)
    async def book_start(message: Message, db: Database, state: FSMContext):
        await state.clear()
        with db.session() as s:
            text, markup = _step(s, [])
        if text is None:
            await message.answer("Запис тимчасово недоступний.")
            return
        await message.answer(text, reply_markup=markup)

    @router.callback_query(F.data == "book")
    async def book_again(cb: CallbackQuery, db: Database, state: FSMContext):
        await state.clear()
        with db.session() as s:
            text, markup = _step(s, [])
        await cb.message.edit_text(text or "Запис тимчасово недоступний.", reply_markup=markup)
        await cb.answer()

    @router.callback_query(F.data.startswith("t:"))
    async def pick_node(cb: CallbackQuery, db: Database):
        with db.session() as s:
            text, markup = _step(s, v.parse_path(cb.data[2:]))
        await cb.message.edit_text(text or "Запис тимчасово недоступний.", reply_markup=markup)
        await cb.answer()

    @router.callback_query(F.data.startswith("svc:"))
    async def pick_service(cb: CallbackQuery, db: Database):
        service_id = int(cb.data.split(":")[1])
        with db.session() as s:
            svc = s.get(Service, service_id)
            if svc is None or not svc.is_active:
                await cb.answer("Послуга зараз недоступна", show_alert=True)
                return
            # показуємо лише майстрів, у яких є вільний час, і їхній найближчий вільний час
            free = [(m, t) for m in users.masters_for_service(s, service_id)
                    if (t := _nearest(s, service_id, m.id))]
            any_first = min((t for _, t in free), default=None)
            title = svc.name
            # улюблений майстер клієнта (3+ візити) — першим у списку
            fav_id, hint = None, ""
            client = _client_by_tg(s, cb.from_user.id)
            fav = bookings.favorite_master(s, client.id, service_id) if client else None
            if fav and any(m.id == fav[0].id for m, _ in free):
                fav_id = fav[0].id
                free.sort(key=lambda x: x[0].id != fav_id)
                hint = f"\n⭐ Ви вже {fav[1]} рази були в майстра {v.e(v.short_name(fav[0].full_name))} — записати знову?"
        if not free:
            await cb.answer("Найближчими днями вільного часу на цю послугу немає 😔", show_alert=True)
            return
        if len(free) == 1:
            # один вільний майстер — одразу дні, без зайвого кроку «Оберіть майстра»
            await _show_days(cb, db, service_id, free[0][0].id, back="book", title=title)
            return
        await cb.message.edit_text(f"<b>{v.e(title)}</b>{hint}\nОберіть майстра:",
                                   reply_markup=v.masters_kb(service_id, free, any_first, favorite_id=fav_id))
        await cb.answer()

    def _horizon(s) -> list:
        return v.horizon(clock.now().date(), min(int(get_setting(s, "booking_horizon_days")), MAX_DAYS_SHOWN))

    def _slots(s, service_id: int, master_id: int, d) -> list:
        if master_id:
            return bookings.free_slots(s, master_id, service_id, d, for_client=True)
        return list(bookings.free_slots_any(s, service_id, d, for_client=True))

    def _nearest(s, service_id: int, master_id: int):
        for d in _horizon(s):
            slots = _slots(s, service_id, master_id, d)
            if slots:
                return slots[0]
        return None

    def _available_days(s, service_id: int, master_id: int) -> dict:
        """{день: кількість вільних початків} — лише дні, де є вільний час."""
        out = {}
        for d in _horizon(s):
            n = len(_slots(s, service_id, master_id, d))
            if n:
                out[d] = n
        return out

    @router.callback_query(F.data == "nop")
    async def nop(cb: CallbackQuery):
        await cb.answer()

    async def _show_days(cb: CallbackQuery, db: Database, service_id: int, master_id: int, back: str,
                         title: str | None = None) -> None:
        with db.session() as s:
            days = _available_days(s, service_id, master_id)
            who = v.short_name(s.get(User, master_id).full_name) if master_id else "будь-який майстер"
        if not days:
            await cb.answer("Найближчими днями вільного часу немає 😔", show_alert=True)
            return
        head = f"<b>{v.e(title)}</b>\n" if title else ""
        await cb.message.edit_text(
            f"{head}Майстер: <b>{v.e(who)}</b>\n📅 Оберіть день (поруч — кількість вільних годин для запису):",
            reply_markup=v.days_kb(f"day:{service_id}:{master_id}", list(days), back, counts=days),
        )
        await cb.answer()

    @router.callback_query(F.data.startswith("mst:"))
    async def pick_master(cb: CallbackQuery, db: Database):
        _, service_id, master_id = cb.data.split(":")
        await _show_days(cb, db, int(service_id), int(master_id), back=f"svc:{service_id}")

    @router.callback_query(F.data.startswith("day:"))
    async def pick_day(cb: CallbackQuery, db: Database):
        _, service_id, master_id, day_s = cb.data.split(":")
        day = v.parse_day(day_s)
        with db.session() as s:
            slots = _slots(s, int(service_id), int(master_id), day)
        if not slots:
            await cb.answer("На цей день вільного часу вже немає", show_alert=True)
            return
        await cb.message.edit_text(
            f"📅 <b>{v.day_title(day)}</b>\n🕐 Оберіть зручний час:",
            reply_markup=v.slots_kb(f"slot:{service_id}:{master_id}", slots, f"mst:{service_id}:{master_id}"),
        )
        await cb.answer()

    async def _show_summary(target: Message, db: Database, state: FSMContext, edit: bool) -> None:
        data = await state.get_data()
        with db.session() as s:
            svc = s.get(Service, data["service_id"])
            master_name = s.get(User, data["master_id"]).full_name if data["master_id"] else "будь-який вільний"
            text = v.summary_text(svc, master_name, v.parse_dt(data["dt"]))
        await state.set_state(Booking.confirming)
        if edit:
            await target.edit_text(text, reply_markup=v.confirm_kb())
        else:
            await target.answer(text, reply_markup=v.confirm_kb())


    @router.callback_query(F.data.startswith("slot:"))
    async def pick_slot(cb: CallbackQuery, db: Database, state: FSMContext):
        _, service_id, master_id, dt = cb.data.split(":")
        await state.set_data({"service_id": int(service_id), "master_id": int(master_id), "dt": dt})
        with db.session() as s:
            client = _client_by_tg(s, cb.from_user.id)
            if client:
                await state.update_data(phone=client.phone, name=client.name)
        if client:
            await _show_summary(cb.message, db, state, edit=True)
        else:
            await state.set_state(Booking.waiting_contact)
            await cb.message.answer(
                "Щоб записатися, поділіться, будь ласка, своїм номером телефону — "
                "кнопка нижче 👇", reply_markup=v.contact_request(),
            )
        await cb.answer()


    @router.message(Booking.waiting_contact, F.contact)
    async def got_contact(message: Message, db: Database, state: FSMContext):
        c = message.contact
        if c.user_id != message.from_user.id:
            await message.answer("Будь ласка, надішліть саме свій номер кнопкою нижче.", reply_markup=v.contact_request())
            return
        name = " ".join(x for x in (c.first_name, c.last_name) if x) or message.from_user.full_name
        await state.update_data(phone=c.phone_number, name=name)
        await message.answer("Дякуємо! 🙏", reply_markup=v.main_menu())
        await _show_summary(message, db, state, edit=False)


    @router.message(Booking.waiting_contact)
    async def need_contact(message: Message):
        await message.answer("Натисніть кнопку «📱 Поділитися номером».", reply_markup=v.contact_request())


    @router.callback_query(F.data == "abort")
    async def abort(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await cb.message.edit_text("Запис скасовано. Повертайтеся, коли буде зручно 🌿")
        await cb.answer()


    @router.callback_query(Booking.confirming, F.data == "ok")
    async def confirm(cb: CallbackQuery, db: Database, state: FSMContext):
        data = await state.get_data()
        start = v.parse_dt(data["dt"])
        actor = Actor.telegram(cb.from_user.id)
        try:
            with db.session() as s:
                master_id = data["master_id"]
                if not master_id:
                    free = bookings.free_slots_any(s, data["service_id"], start.date(), for_client=True).get(start)
                    if not free:
                        raise DomainError("Цей час щойно зайняли. Оберіть інший, будь ласка.")
                    master_id = free[0]
                a = bookings.create_appointment(
                    s, actor, master_id=master_id, service_id=data["service_id"], starts_at=start,
                    client_name=data.get("name") or cb.from_user.full_name, client_phone=data["phone"],
                )
                text = v.appt_text(a)
                pending = a.status == ApptStatus.PENDING
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        await state.clear()
        tail = "\n\nАдміністратор підтвердить запис найближчим часом." if pending else ""
        await cb.message.edit_text(f"🎉 Вас записано!\n\n{text}{tail}")
        await cb.answer()
        await _ask_reminders(cb, db)


    @router.callback_query(F.data == "ok")
    async def confirm_stale(cb: CallbackQuery):
        await cb.answer("Сесія запису застаріла. Почніть спочатку: «📅 Записатися».", show_alert=True)


    # ------------------------------------------------------------ мої записи

    @router.message(Command("my"))
    @router.message(F.text == v.BTN_MY)
    async def my_bookings(message: Message, db: Database):
        with db.session() as s:
            items = [(v.appt_text(a), v.my_appt_kb(a)) for a in bookings.upcoming_for_telegram(s, message.from_user.id)]
        if not items:
            await message.answer("У вас немає майбутніх записів.", reply_markup=v.main_menu())
            return
        for text, kb in items:
            await message.answer(text, reply_markup=kb)


    @router.callback_query(F.data.startswith("acc:"))
    async def accept_proposal(cb: CallbackQuery, db: Database):
        """Клієнт погодився на час, запропонований майстром."""
        try:
            with db.session() as s:
                a = bookings.client_accept(s, Actor.telegram(cb.from_user.id), int(cb.data.split(":")[1]))
                text = v.appt_text(a)
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        await cb.message.edit_text(f"✅ Чудово, запис підтверджено!\n\n{text}")
        await cb.answer()

    @router.callback_query(F.data.startswith("rb:"))
    async def rebook(cb: CallbackQuery, db: Database):
        """Клієнт обрав запропонований вільний час після відповіді «зайнято»."""
        _, old_id, dt = cb.data.split(":")
        try:
            with db.session() as s:
                a = bookings.client_rebook(s, Actor.telegram(cb.from_user.id), int(old_id), v.parse_dt(dt))
                text = v.appt_text(a)
                pending = a.status == ApptStatus.PENDING
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        tail = "\n\nМайстер підтвердить запис найближчим часом." if pending else ""
        await cb.message.edit_text(f"🎉 Вас записано!\n\n{text}{tail}")
        await cb.answer()
        await _ask_reminders(cb, db)

    async def _ask_reminders(cb: CallbackQuery, db: Database) -> None:
        """Після першого запису — спитати, чи потрібні нагадування (один раз)."""
        with db.session() as s:
            ask = reminders.should_ask(s, cb.from_user.id)
        if ask:
            await cb.message.answer(reminders.ASK_TEXT, reply_markup=v.kb([[tuple(b) for b in row] for row in reminders.ASK_BUTTONS]))

    @router.callback_query(F.data.in_({"rem:1", "rem:0"}))
    async def reminders_choice(cb: CallbackQuery, db: Database):
        on = cb.data == "rem:1"
        try:
            with db.session() as s:
                reminders.set_choice(s, Actor.telegram(cb.from_user.id), on)
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        msg = ("🔔 Домовились! Нагадаємо за добу і за 2 години до візиту." if on
               else "🔕 Добре, нагадувань не буде. Увімкнути знову можна командою /remind")
        if cb.message.text and cb.message.text.startswith("🔔 Нагадати"):
            await cb.message.edit_text(msg)
        else:
            await cb.message.answer(msg)
        await cb.answer()

    @router.message(Command("remind"))
    async def remind_cmd(message: Message, db: Database):
        try:
            with db.session() as s:
                reminders.set_choice(s, Actor.telegram(message.from_user.id), True)
        except DomainError as e:
            await message.answer(e.message)
            return
        await message.answer("🔔 Нагадування увімкнено: за добу і за 2 години до візиту.")

    @router.callback_query(F.data.startswith("cnl:"))
    async def cancel_ask(cb: CallbackQuery):
        appt_id = int(cb.data.split(":")[1])
        await cb.message.edit_reply_markup(reply_markup=v.cancel_confirm_kb(appt_id))
        await cb.answer()


    @router.callback_query(F.data.startswith("cnlok:"))
    async def cancel_do(cb: CallbackQuery, db: Database):
        appt_id = int(cb.data.split(":")[1])
        try:
            with db.session() as s:
                a = bookings.set_status(s, Actor.telegram(cb.from_user.id), appt_id, ApptStatus.CANCELLED)
                text = v.appt_text(a)
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        await cb.message.edit_text(f"Запис скасовано.\n\n{text}")
        await cb.answer()


    @router.callback_query(F.data == "noop")
    async def noop(cb: CallbackQuery, db: Database):
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("Запис залишено без змін")


    @router.callback_query(F.data.startswith("mv:"))
    async def move_start(cb: CallbackQuery, db: Database):
        appt_id = int(cb.data.split(":")[1])
        try:
            with db.session() as s:
                a = bookings.get_appointment(s, Actor.telegram(cb.from_user.id), appt_id)
                days = _available_days(s, a.service_id, a.master_id)
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        if not days:
            await cb.answer("У майстра немає вільного часу найближчими днями", show_alert=True)
            return
        await cb.message.edit_text("Оберіть новий день:", reply_markup=v.days_kb(f"mvd:{appt_id}", days, "noop"))
        await cb.answer()


    @router.callback_query(F.data.startswith("mvd:"))
    async def move_day(cb: CallbackQuery, db: Database):
        _, appt_id, day_s = cb.data.split(":")
        day = v.parse_day(day_s)
        try:
            with db.session() as s:
                a = bookings.get_appointment(s, Actor.telegram(cb.from_user.id), int(appt_id))
                slots = bookings.free_slots(s, a.master_id, a.service_id, day, for_client=True)
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        if not slots:
            await cb.answer("На цей день вільного часу немає", show_alert=True)
            return
        await cb.message.edit_text(
            f"📅 <b>{v.day_title(day)}</b>\n🕐 Новий час:",
            reply_markup=v.slots_kb(f"mvs:{appt_id}", slots, f"mv:{appt_id}"),
        )
        await cb.answer()


    @router.callback_query(F.data.startswith("mvs:"))
    async def move_do(cb: CallbackQuery, db: Database):
        _, appt_id, dt = cb.data.split(":")
        try:
            with db.session() as s:
                a = bookings.reschedule(s, Actor.telegram(cb.from_user.id), int(appt_id), starts_at=v.parse_dt(dt))
                text = v.appt_text(a)
        except DomainError as e:
            await cb.answer(e.message, show_alert=True)
            return
        await cb.message.edit_text(f"🔁 Запис перенесено.\n\n{text}")
        await cb.answer()


    # ------------------------------------------------------------- персонал

    @router.message(Command("link"))
    async def link_staff(message: Message, command: CommandObject, db: Database):
        if not command.args:
            await message.answer("Використання: /link КОД (код — у профілі програми MDS Connect)")
            return
        try:
            with db.session() as s:
                u = users.link_telegram(s, command.args, message.chat.id)
                name = u.full_name
        except DomainError as e:
            await message.answer(e.message)
            return
        await message.answer(f"✅ {v.e(name)}, Telegram підключено. Сюди надходитимуть сповіщення про записи та звіти.")


    @router.message(Command("today"))
    async def staff_today(message: Message, db: Database):
        with db.session() as s:
            u = s.scalar(select(User).where(User.telegram_chat_id == message.chat.id, User.is_active.is_(True)))
            if u is None or u.role == "client":
                await message.answer("Команда доступна лише персоналу салону.")
                return
            actor = Actor.of(u)
            today = clock.now().date()
            items = bookings.list_appointments(
                s, actor, date_from=today, date_to=today,
                master_id=u.id if u.role == Role.MASTER else None, include_cancelled=False,
            )
            # дані клієнтів екрануються: бот відповідає в режимі HTML
            lines = [f"{a.starts_at:%H:%M} {v.e(a.service.name)} — {v.e(a.client.name)} {v.e(a.client.phone)}"
                     + ("" if u.role == Role.MASTER else f" ({v.e(a.master.full_name)})") for a in items]
        await message.answer("📋 Сьогодні:\n" + ("\n".join(lines) if lines else "записів немає"))

    return router
