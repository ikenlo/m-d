"""Обробники бота. Уся логіка — у сервісному шарі; тут лише діалог."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from .. import clock
from ..db import Database
from ..errors import DomainError
from ..models import ApptStatus, Client, Role, Service, User
from ..services import bookings, catalog, users
from ..services.common import Actor, get_setting
from . import views as v


MAX_DAYS_SHOWN = 14


class Booking(StatesGroup):
    waiting_contact = State()
    confirming = State()


def _client_by_tg(s, tg_id: int) -> Client | None:
    return s.scalar(select(Client).where(Client.telegram_user_id == tg_id))


def build_router() -> Router:
    """Новий роутер з усіма обробниками (роутер aiogram можна підключити лише до одного диспетчера)."""
    router = Router(name="mds")

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


    @router.message(Command("book"))
    @router.message(F.text == v.BTN_BOOK)
    async def book_start(message: Message, db: Database, state: FSMContext):
        await state.clear()
        with db.session() as s:
            services = catalog.list_services(s)
            kb = v.services_kb(services)
        if not services:
            await message.answer("Запис тимчасово недоступний.")
            return
        await message.answer("Оберіть послугу:", reply_markup=kb)


    @router.callback_query(F.data == "book")
    async def book_again(cb: CallbackQuery, db: Database, state: FSMContext):
        await state.clear()
        with db.session() as s:
            kb = v.services_kb(catalog.list_services(s))
        await cb.message.edit_text("Оберіть послугу:", reply_markup=kb)
        await cb.answer()


    @router.callback_query(F.data.startswith("svc:"))
    async def pick_service(cb: CallbackQuery, db: Database):
        service_id = int(cb.data.split(":")[1])
        with db.session() as s:
            svc = s.get(Service, service_id)
            masters = users.masters_for_service(s, service_id)
            if svc is None or not svc.is_active or not masters:
                await cb.answer("Послуга зараз недоступна", show_alert=True)
                return
            kb = v.masters_kb(service_id, masters)
            title = svc.name
        await cb.message.edit_text(f"<b>{title}</b>\nОберіть майстра:", reply_markup=kb)
        await cb.answer()


    def _available_days(s, service_id: int, master_id: int) -> list:
        horizon = min(int(get_setting(s, "booking_horizon_days")), MAX_DAYS_SHOWN)
        out = []
        for d in v.horizon(clock.now().date(), horizon):
            if master_id:
                ok = bool(bookings.free_slots(s, master_id, service_id, d, for_client=True))
            else:
                ok = bool(bookings.free_slots_any(s, service_id, d, for_client=True))
            if ok:
                out.append(d)
        return out


    @router.callback_query(F.data.startswith("mst:"))
    async def pick_master(cb: CallbackQuery, db: Database):
        _, service_id, master_id = cb.data.split(":")
        with db.session() as s:
            days = _available_days(s, int(service_id), int(master_id))
        if not days:
            await cb.answer("Найближчими днями вільного часу немає 😔", show_alert=True)
            return
        await cb.message.edit_text(
            "Оберіть день:", reply_markup=v.days_kb(f"day:{service_id}:{master_id}", days, f"svc:{service_id}")
        )
        await cb.answer()


    @router.callback_query(F.data.startswith("day:"))
    async def pick_day(cb: CallbackQuery, db: Database):
        _, service_id, master_id, day_s = cb.data.split(":")
        day = v.parse_day(day_s)
        with db.session() as s:
            if int(master_id):
                slots = bookings.free_slots(s, int(master_id), int(service_id), day, for_client=True)
            else:
                slots = list(bookings.free_slots_any(s, int(service_id), day, for_client=True))
        if not slots:
            await cb.answer("На цей день вільного часу вже немає", show_alert=True)
            return
        await cb.message.edit_text(
            f"{v.WEEKDAYS[day.weekday()]} {day:%d.%m} — оберіть час:",
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
            f"{v.WEEKDAYS[day.weekday()]} {day:%d.%m} — новий час:",
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
        await message.answer(f"✅ {name}, Telegram підключено. Сюди надходитимуть сповіщення про записи та звіти.")


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
            lines = [f"{a.starts_at:%H:%M} {a.service.name} — {a.client.name} {a.client.phone}"
                     + ("" if u.role == Role.MASTER else f" ({a.master.full_name})") for a in items]
        await message.answer("📋 Сьогодні:\n" + ("\n".join(lines) if lines else "записів немає"))

    return router
