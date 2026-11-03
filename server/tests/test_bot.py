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
