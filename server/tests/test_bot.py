"""Наскрізні тести бота: справжній Dispatcher + підроблена сесія Telegram."""

from __future__ import annotations

import asyncio
from datetime import datetime
from itertools import count

import pytest
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update, User as TgUser

from mds.bot.runner import build_dispatcher, deliver_outbox, make_bot
from mds.models import Appointment, ApptStatus, Notification, User

TG_ID = 555000111
_ids = count(1)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=next(_ids), date=datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                           text=method.text)
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    # зручні помічники
    def texts(self):
        return [m.text for m in self.calls if isinstance(m, (SendMessage, EditMessageText))]

    def last_markup(self):
        for m in reversed(self.calls):
            if isinstance(m, (SendMessage, EditMessageText, EditMessageReplyMarkup)) and m.reply_markup is not None:
                return m.reply_markup
        return None

    def buttons(self):
        mk = self.last_markup()
        return [b for row in getattr(mk, "inline_keyboard", []) for b in row]

    def alerts(self):
        return [m.text for m in self.calls if isinstance(m, AnswerCallbackQuery) and m.text]


class BotHarness:
    def __init__(self, db, user_id=TG_ID):
        self.session = FakeSession()
        self.bot = make_bot("123456:TEST-TOKEN", session=self.session)
        self.dp = build_dispatcher(db)
        self.user = TgUser(id=user_id, is_bot=False, first_name="Катя")
        self.chat = Chat(id=user_id, type="private")

    def _msg(self, **kw):
        return Message(message_id=next(_ids), date=datetime.now(), chat=self.chat, from_user=self.user, **kw)

    def send(self, text=None, contact=None):
        upd = Update(update_id=next(_ids), message=self._msg(text=text, contact=contact))
        asyncio.run(self.dp.feed_update(self.bot, upd))

    def click(self, data):
        cb = CallbackQuery(id=str(next(_ids)), from_user=self.user, chat_instance="x", data=data,
                           message=self._msg(text="..."))
        asyncio.run(self.dp.feed_update(self.bot, Update(update_id=next(_ids), callback_query=cb)))

    def click_text(self, prefix):
        for b in self.session.buttons():
            if b.text.startswith(prefix):
                return self.click(b.callback_data)
        raise AssertionError(f"Кнопку '{prefix}' не знайдено: {[b.text for b in self.session.buttons()]}")


@pytest.fixture
def h(db, seed):
    harness = BotHarness(db)
    harness.ids = seed
    return harness


def test_services_list(h):
    h.send("/start")
    h.send("💅 Послуги та ціни")
    assert "Манікюр" in h.session.texts()[-1] and "600 ₴" in h.session.texts()[-1]


def test_full_booking_flow_and_notifications(h, db):
    with db.session() as s:
        s.get(User, h.ids["director"]).telegram_chat_id = 9001
        s.get(User, h.ids["m1"]).telegram_chat_id = 9002
    h.send("📅 Записатися")
    h.click_text("Стрижка")
    h.click_text("Олена")
    h.click_text("Пн 02.11")
    h.click_text("10:00")
    assert "номером" in h.session.texts()[-1]
    # чужий контакт не приймається
    h.send(contact=Contact(phone_number="380671234567", first_name="Хтось", user_id=1))
    assert "саме свій" in h.session.texts()[-1]
    h.send(contact=Contact(phone_number="380931234567", first_name="Катя", user_id=TG_ID))
    assert "Перевірте запис" in h.session.texts()[-1]
    h.click("ok")
    assert "Вас записано" in h.session.texts()[-1]
    with db.session() as s:
        a = s.query(Appointment).one()
        assert a.starts_at == datetime(2026, 11, 2, 10, 0)
        assert a.status == ApptStatus.PENDING and a.source == "telegram"
        assert a.client.phone == "+380931234567" and a.client.telegram_user_id == TG_ID
        assert sorted(n.chat_id for n in s.query(Notification)) == [9001, 9002]

    # доставка черги сповіщень
    sent = asyncio.run(deliver_outbox(h.bot, db))
    assert sent == 2
    with db.session() as s:
        assert all(n.sent_at for n in s.query(Notification))

    # повторний запис: клієнт уже відомий — без запиту номера
    h.send("📅 Записатися")
    h.click_text("Манікюр")
    h.click_text("👥 Будь-який")
    h.click_text("Пн 02.11")
    slot_texts = [b.text for b in h.session.buttons()]
    assert "10:00" in slot_texts  # Ірина вільна о 10:00
    h.click_text("10:00")
    assert "Перевірте запис" in h.session.texts()[-1]
    h.click("ok")
    with db.session() as s:
        b = s.query(Appointment).order_by(Appointment.id.desc()).first()
        assert b.master_id == h.ids["m2"]


def test_slot_taken_between_steps(h, db, seed):
    h.send("📅 Записатися")
    h.click_text("Стрижка")
    h.click_text("Олена")
    h.click_text("Пн 02.11")
    h.click_text("10:00")
    h.send(contact=Contact(phone_number="380931234567", first_name="Катя", user_id=TG_ID))
    # поки клієнт думав, адміністратор зайняв цей час
    from mds.services import bookings
    with db.session() as s:
        bookings.create_appointment(s, seed["actors"]["admin"], master_id=seed["m1"], service_id=seed["haircut"],
                                    starts_at=datetime(2026, 11, 2, 10), client_name="X", client_phone="0500000000")
    h.click("ok")
    assert any("зайнятий" in a for a in h.session.alerts())
    with db.session() as s:
        assert s.query(Appointment).count() == 1


def test_my_bookings_cancel_and_move(h, db, seed):
    from mds.services import bookings
    from mds.services.common import Actor
    with db.session() as s:
        a = bookings.create_appointment(s, Actor.telegram(TG_ID), master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 3, 10), client_name="Катя", client_phone="0931234567")
        appt_id = a.id
    h.send("🗓 Мої записи")
    assert "03.11.2026 о 10:00" in h.session.texts()[-1]
    h.click(f"mv:{appt_id}")
    h.click_text("Ср 04.11")
    h.click_text("15:00")
    assert "перенесено" in h.session.texts()[-1]
    with db.session() as s:
        assert s.get(Appointment, appt_id).starts_at == datetime(2026, 11, 4, 15)
    # чужий клієнт не може скасувати
    other = BotHarness(db, user_id=42)
    other.click(f"cnlok:{appt_id}")
    assert any("не знайдено" in a for a in other.session.alerts())
    h.click(f"cnl:{appt_id}")
    h.click(f"cnlok:{appt_id}")
    with db.session() as s:
        assert s.get(Appointment, appt_id).status == ApptStatus.CANCELLED
    h.send("🗓 Мої записи")
    assert "немає майбутніх" in h.session.texts()[-1]


def test_staff_link_and_today(h, db, seed):
    from mds.services import users
    with db.session() as s:
        code = users.new_telegram_link_code(s, seed["actors"]["m1"], seed["m1"])
    h.send("/link WRONG")
    assert "не знайдено" in h.session.texts()[-1]
    h.send(f"/link {code}")
    assert "Telegram підключено" in h.session.texts()[-1]
    with db.session() as s:
        assert s.get(User, seed["m1"]).telegram_chat_id == TG_ID
    h.send("/today")
    assert "Сьогодні" in h.session.texts()[-1]
    # код одноразовий
    h2 = BotHarness(db, user_id=77)
    h2.send(f"/link {code}")
    assert "не знайдено" in h2.session.texts()[-1]
    # клієнт не бачить /today
    h2.send("/today")
    assert "лише персоналу" in h2.session.texts()[-1]



def test_env_aware_session_uses_proxy_env_and_custom_ca(monkeypatch):
    """Бот має працювати за корпоративним проксі зі власним сертифікатом (HTTPS_PROXY, SSL_CERT_FILE)."""
    import certifi

    from mds.bot.runner import EnvAwareSession

    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    s = EnvAwareSession()

    async def check():
        cs = await s.create_session()
        try:
            return cs.trust_env
        finally:
            await s.close()

    assert asyncio.run(check()) is True
    assert s._connector_init["ssl"] is not None


# ---------------------------------------------------------------- персонал

@pytest.fixture
def pins(db, seed):
    from mds.services import users as us
    from mds.services.common import Actor as A
    with db.session() as s:
        us.set_bot_pin(s, A.system(), seed["m1"], "1111")
        us.set_bot_pin(s, A.system(), seed["director"], "9999")
    return seed


def staff_login(h, uid, pin, code="0000"):
    h.send("👩‍🎨 Для майстрів")
    h.send(code)
    h.click(f"s:who:{uid}")
    h.send(pin)


def test_client_free_time_and_contacts(h, db, seed):
    from mds.services import bookings
    from mds.services.common import Actor as A, update_settings
    with db.session() as s:
        bookings.create_appointment(s, seed["actors"]["admin"], master_id=seed["m1"], service_id=seed["haircut"],
                                    starts_at=datetime(2026, 11, 2, 12), client_name="X", client_phone="0500000000")
        update_settings(s, A.system(), {"salon_address": "Київ, вул. Хрещатик, 1", "salon_phone": "+380671234567"})
    h.send("🕐 Вільний час")
    h.click("free:20261102")
    text = h.session.texts()[-1]
    assert "Олена</b>: 09:00–12:00, 13:00–20:00" in text
    assert "Ірина</b>: 09:00–20:00" in text
    h.send("📍 Контакти")
    assert "Хрещатик" in h.session.texts()[-1] and "Неділя: вихідний" in h.session.texts()[-1]


def test_staff_wrong_code_is_rejected(h, pins):
    h.send("👩‍🎨 Для майстрів")
    assert "код доступу" in h.session.texts()[-1]
    h.send("1234")
    assert "Цей розділ не для вас" in h.session.texts()[-1]
    # без коду вибір людини не працює
    h.click(f"s:who:{pins['m1']}")
    assert any("не для вас" in a for a in h.session.alerts())


def test_staff_code_shows_only_people_with_pin(h, pins):
    h.send("👩‍🎨 Для майстрів")
    h.send("0000")
    names = [b.text for b in h.session.buttons()]
    assert any("Олена" in n for n in names) and any("Марина" in n for n in names)
    assert not any("Ірина" in n for n in names)  # немає PIN


def test_staff_pin_bruteforce_lock(h, pins):
    h.send("👩‍🎨 Для майстрів")
    h.send("0000")
    for _ in range(5):
        h.click(f"s:who:{pins['m1']}")
        h.send("0001")
    assert "Забагато" in h.session.texts()[-1]
    h.send("👩‍🎨 Для майстрів")
    assert "Забагато" in h.session.texts()[-1]


def test_master_flow_add_list_close_earn_shift(h, db, pins):
    from aiogram.methods import DeleteMessage
    staff_login(h, pins["m1"], "1111")
    assert "Олена" in h.session.texts()[-1] and "Оберіть дію" in h.session.texts()[-1]
    # код і PIN видаляються з чату
    assert sum(isinstance(c, DeleteMessage) for c in h.session.calls) >= 2
    with db.session() as s:
        assert s.get(User, pins["m1"]).telegram_chat_id == TG_ID

    # додати запис
    h.click("s:add")
    h.click_text("Стрижка")
    h.click_text("Пн 02.11")
    h.click_text("10:00")
    h.send("0671112233")
    assert "Новий клієнт" in h.session.texts()[-1]
    h.send("Анна")
    assert "Запис створено" in h.session.texts()[-1]
    with db.session() as s:
        a = s.query(Appointment).one()
        assert a.master_id == pins["m1"] and a.client.name == "Анна" and a.status == ApptStatus.CONFIRMED
        appt_id = a.id

    # мої записи на сьогодні
    h.click("s:list")
    h.click("s:ld:20261102")
    assert "Анна" in h.session.texts()[-1]

    # закрити замовлення з іншою сумою
    h.click(f"s:cl:{appt_id}")
    h.send("620")
    h.click_text("💵 Готівка")
    assert "Замовлення закрито" in h.session.texts()[-1] and "310 ₴" in h.session.texts()[-1]
    h.click(f"s:pm:{appt_id}:62000:cash")  # повторне натискання — без задвоєння
    with db.session() as s:
        from mds.models import Order
        assert s.query(Order).count() == 1

    h.click("s:earn")
    assert "Сьогодні: <b>310 ₴</b>" in h.session.texts()[-1]
    h.click("s:shift")
    h.click("s:shiftok")
    assert "Звіт зміни" in h.session.texts()[-1]

    # майстер не має доступу до фінансів директора
    h.click("s:fin")
    assert any("Сесія" in a for a in h.session.alerts())

    h.click("s:logout")
    h.click("s:list")
    assert any("Сесія завершилась" in a for a in h.session.alerts())


def test_director_flow(h, db, pins):
    from mds.services import bookings, orders
    adm = pins["actors"]["admin"]
    with db.session() as s:
        a = bookings.create_appointment(s, adm, master_id=pins["m1"], service_id=pins["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="Анна", client_phone="0671112233")
        b = bookings.create_appointment(s, adm, master_id=pins["m2"], service_id=pins["haircut"],
                                        starts_at=datetime(2026, 11, 2, 11), client_name="Юля", client_phone="0501112233")
        orders.close_order(s, pins["actors"]["m2"], b.id, amount=50000, payment_method="card")
        sh = orders.close_shift(s, pins["actors"]["m2"], pins["m2"])
        shift_id = sh.id
    staff_login(h, pins["director"], "9999")
    assert "Усі записи" in str(h.session.last_markup())

    h.click("s:list")
    h.click("s:ld:20261102")
    texts = h.session.texts()
    assert any("Анна" in t and "Олена" in t for t in texts)

    # перенести
    h.click(f"s:mv:{a.id}")
    h.click_text("Пн 02.11")
    h.click_text("15:00")
    assert "Перенесено" in h.session.texts()[-1]
    # видалити (скасування з історією)
    h.click(f"s:del:{a.id}")
    h.click(f"s:delok:{a.id}")
    with db.session() as s:
        x = s.get(Appointment, a.id)
        assert x.status == ApptStatus.CANCELLED and "директором" in x.cancel_reason
        assert x.starts_at == datetime(2026, 11, 2, 15)

    # додати запис іншому майстру
    h.click("s:add")
    h.click(f"s:am:{pins['m2']}")
    h.click_text("Манікюр")
    h.click_text("Пн 02.11")
    h.click_text("14:00")
    h.send("0671112233")  # відомий клієнт — запис одразу
    assert "Запис створено" in h.session.texts()[-1]

    h.click("s:fin")
    assert "Виручка: <b>500 ₴</b>" in h.session.texts()[-1]
    h.click("s:pay")
    h.click(f"s:po:{shift_id}")
    assert any("Виплачено" in a for a in h.session.alerts())
    h.click(f"s:po:{shift_id}")  # вдруге — відмова
    assert any("вже виплачена" in a for a in h.session.alerts())
