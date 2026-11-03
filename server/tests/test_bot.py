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
    h.click_text("Сьогодні")
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
    h.click_text("👥 Будь-хто")
    h.click_text("Сьогодні")
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
    h.click_text("Сьогодні")
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
    assert "Завтра, 3 листопада о 10:00" in h.session.texts()[-1]
    h.click(f"mv:{appt_id}")
    h.click_text("Ср, 4 листопада")
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
    h.click_text("Сьогодні")
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
    h.click_text("Сьогодні")
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
    h.click_text("Сьогодні")
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


def test_price_from_shown_and_duration_hidden(h, db, seed):
    from mds.services import catalog
    with db.session() as s:
        catalog.create_service(s, seed["actors"]["director"], {"name": "Довге волосся", "category": "Жіночі стрижки",
                                                               "duration_min": 60, "price": 55000, "price_from": True})
    h.send("💅 Послуги та ціни")
    text = h.session.texts()[-1]
    assert "Довге волосся — <b>від 550 ₴</b>" in text
    assert "хв" not in text and "год" not in text


def test_booking_goes_section_then_type_then_service(h, db, seed):
    from mds.models import Service
    from mds.services import catalog
    d = seed["actors"]["director"]
    with db.session() as s:
        for x in s.query(Service):
            x.is_active = False
        for name, sec, cat, price in [("Модельна стрижка", "Чоловічі", "Стрижка", 45000),
                                      ("Коротке волосся", "Жіночі", "Стрижка", 40000),
                                      ("AirTouch", "Жіночі", "Фарбування", 300000)]:
            catalog.create_service(s, d, {"name": name, "section": sec, "category": cat, "price": price, "duration_min": 60})
    h.send("📅 Записатися")
    assert [b.text for b in h.session.buttons()] == ["💈 Чоловічі", "💇‍♀️ Жіночі"]
    h.click_text("💇‍♀️ Жіночі")
    assert [b.text for b in h.session.buttons()] == ["Стрижка", "Фарбування", "« Назад"]
    h.click_text("Фарбування")
    assert "Жіночі · Фарбування" in h.session.texts()[-1]
    assert [b.text for b in h.session.buttons()][0] == "AirTouch · 3 000 ₴"
    h.click_text("AirTouch")
    assert "оберіть майстра" in h.session.texts()[-1].lower()
    # у чоловічих один тип — крок «тип» пропускається
    h.send("📅 Записатися")
    h.click_text("💈 Чоловічі")
    assert [b.text for b in h.session.buttons()][0].startswith("Модельна стрижка")
    h.send("💅 Послуги та ціни")
    t = h.session.texts()[-1]
    assert t.index("ЧОЛОВІЧІ") < t.index("ЖІНОЧІ") < t.index("<i>Фарбування</i>")


def test_three_level_coloring_and_back_navigation(h, db, seed):
    from mds.models import Service
    from mds.services import catalog
    d = seed["actors"]["director"]
    rows = [
        ("Модельна", "Чоловічі", "Стрижки та послуги", None, 45000),
        ("Коротке волосся", "Жіночі", "Стрижка", None, 40000),
        ("До плечей", "Жіночі", "Фарбування", "В один тон", 150000),
        ("До плечей", "Жіночі", "Фарбування", "Мелірування", 300000),
        ("Нижче лопаток", "Жіночі", "Фарбування", "Мелірування", 550000),
    ]
    with db.session() as s:
        for x in s.query(Service):
            x.is_active = False
        for i, (name, sec, cat, sub, price) in enumerate(rows):
            catalog.create_service(s, d, {"name": name, "section": sec, "category": cat, "subcategory": sub,
                                          "price": price, "duration_min": 60, "sort_order": i,
                                          "price_from": name == "Нижче лопаток"})
    h.send("📅 Записатися")
    h.click_text("💇‍♀️ Жіночі")
    h.click_text("Фарбування")
    assert [b.text for b in h.session.buttons()] == ["В один тон", "Мелірування", "« Назад"]
    h.click_text("Мелірування")
    assert "Жіночі · Фарбування · Мелірування" in h.session.texts()[-1]
    assert [b.text for b in h.session.buttons()] == ["До плечей · 3 000 ₴", "Нижче лопаток · від 5 500 ₴", "« Назад"]
    h.click_text("« Назад")
    assert [b.text for b in h.session.buttons()][:2] == ["В один тон", "Мелірування"]
    h.click_text("« Назад")
    assert [b.text for b in h.session.buttons()][:2] == ["Стрижка", "Фарбування"]
    h.click_text("« Назад")
    assert [b.text for b in h.session.buttons()] == ["💈 Чоловічі", "💇‍♀️ Жіночі"]
    # у чоловічих один тип — одразу послуги, «Назад» веде до розділів
    h.click_text("💈 Чоловічі")
    assert [b.text for b in h.session.buttons()] == ["Модельна · 450 ₴", "« Назад"]
    h.send("💅 Послуги та ціни")
    t = h.session.texts()[-1]
    assert "<i>Мелірування</i>" in t and t.index("В один тон") < t.index("Мелірування")


def test_menu_has_quick_booking_and_no_my_bookings(h):
    h.send("/start")
    menu = h.session.last_markup()
    texts = [b.text for row in menu.keyboard for b in row]
    assert "⚡ Швидкий запис" in texts and "🗓 Мої записи" not in texts


def test_quick_booking_creates_callback_request_and_notifies(h, db, pins):
    from mds.models import CallbackRequest, Notification
    with db.session() as s:
        s.get(User, pins["director"]).telegram_chat_id = 9001
    h.send("⚡ Швидкий запис")
    assert "зателефонує" in h.session.texts()[-1]
    h.send("абв")
    assert "Не вдалося розпізнати номер" in h.session.texts()[-1]
    h.send("067 123 45 67")
    assert "зателефонуємо" in h.session.texts()[-1]
    h.send("⚡ Швидкий запис")
    h.send(contact=Contact(phone_number="380671234567", first_name="Катя", user_id=TG_ID))  # повтор — без дубля
    with db.session() as s:
        reqs = s.query(CallbackRequest).all()
        assert [r.phone for r in reqs] == ["+380671234567"]
        assert any("передзвонити" in n.text and n.chat_id == 9001 for n in s.query(Notification))
    # директор бачить заявку в боті й позначає опрацьованою
    staff_login(h, pins["director"], "9999")
    h.click("s:calls")
    assert "+380671234567" in h.session.texts()[-1]
    h.click(f"s:cbd:{reqs[0].id}")
    assert "Нових заявок на дзвінок немає" in h.session.texts()[-1]


def test_masters_show_first_name_and_nearest_free_time(h, db, seed):
    from mds.services import bookings, users as us
    d = seed["actors"]["director"]
    with db.session() as s:
        us.update_user(s, d, seed["m1"], {"full_name": "Олена Коваль"})
        # Ірина зайнята весь сьогоднішній день — її найближчий вільний час завтра
        us.update_user(s, d, seed["m2"], {"schedule": {"0": ["09:00", "10:00"], "1": ["09:00", "18:00"]}})
        bookings.create_appointment(s, seed["actors"]["admin"], master_id=seed["m2"], service_id=seed["haircut"],
                                    starts_at=datetime(2026, 11, 2, 9), client_name="X", client_phone="0500000000")
    h.send("📅 Записатися")
    h.click_text("Стрижка")
    labels = [b.text for b in h.session.buttons()]
    assert labels[0] == "👥 Будь-хто · сьогодні о 09:00"
    assert "Олена · сьогодні о 09:00" in labels  # без прізвища
    assert "Ірина · завтра о 09:00" in labels
    h.click_text("Олена")
    days = [b.text for b in h.session.buttons()]
    assert days[0].startswith("Сьогодні · ") and days[1].startswith("Завтра · ") and days[2].startswith("Ср, 4 листопада")
    h.click_text("Сьогодні")
    assert "Сьогодні, 2 листопада" in h.session.texts()[-1]
    labels = [b.text for b in h.session.buttons()]
    assert labels[0] == "🌅 Ранок" and "☀️ День" in labels and "🌙 Вечір" in labels
    h.click("nop")  # заголовок групи нічого не робить


# ---------------------------------------------- непідтверджені записи

def _pending_from_bot(db, seed, h, when=datetime(2026, 11, 2, 10)):
    from mds.services import bookings
    from mds.services.common import Actor as A
    with db.session() as s:
        a = bookings.create_appointment(s, A.telegram(TG_ID), master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=when, client_name="Катя", client_phone="0931234567")
        return a.id


def test_busy_reply_offers_free_slots_and_client_rebooks(h, db, seed):
    from mds.models import Notification
    from mds.services import bookings
    appt_id = _pending_from_bot(db, seed, h)
    with db.session() as s:
        assert [a.id for a in bookings.pending_list(s, seed["actors"]["m1"])] == [appt_id]
        assert bookings.pending_list(s, seed["actors"]["m2"]) == []
        r = bookings.decline_busy(s, seed["actors"]["m1"], appt_id)
        assert r["notified"] and len(r["slots"]) == 6
        assert "2026-11-02T10:00:00" not in r["slots"]  # зайнятий час не пропонуємо
        assert r["slots"][:2] == ["2026-11-02T09:00:00", "2026-11-02T12:00:00"]  # рознесено в часі
    with db.session() as s:
        assert s.get(Appointment, appt_id).status == ApptStatus.CANCELLED
        n = s.query(Notification).filter_by(chat_id=TG_ID).one()  # одне повідомлення, без дубля «скасовано салоном»
        assert "уже зайнятий" in n.text
        first_cb = n.buttons[0][0][1]
        assert first_cb.startswith(f"rb:{appt_id}:") and n.buttons[-1][0][1] == "book"
    # бот доставляє повідомлення з кнопками
    asyncio.run(deliver_outbox(h.bot, db))
    sent = [c for c in h.session.calls if isinstance(c, SendMessage) and c.chat_id == TG_ID][-1]
    assert sent.reply_markup.inline_keyboard[0][0].callback_data == first_cb
    # клієнт обирає запропонований час
    h.click(first_cb)
    assert "Вас записано" in h.session.texts()[-1]
    with db.session() as s:
        new = s.query(Appointment).order_by(Appointment.id.desc()).first()
        assert new.id != appt_id and new.master_id == seed["m1"] and new.client.telegram_user_id == TG_ID
    # чужий клієнт не може скористатися кнопкою
    other = BotHarness(db, user_id=42)
    other.click(first_cb)
    assert any("не знайдено" in a for a in other.session.alerts())


def test_master_proposes_other_time_and_client_accepts(h, db, seed):
    from mds.models import Notification
    from mds.services import bookings
    appt_id = _pending_from_bot(db, seed, h)
    with db.session() as s:
        a, notified = bookings.propose_time(s, seed["actors"]["m1"], appt_id, datetime(2026, 11, 2, 15))
        assert notified and a.awaiting_client and a.status == ApptStatus.PENDING
        n = s.query(Notification).filter_by(chat_id=TG_ID).one()
        assert "пропонує інший час" in n.text and "Пн 02.11 о 15:00" in n.text
        assert [row[0][1] for row in n.buttons] == [f"acc:{appt_id}", f"mv:{appt_id}", f"cnl:{appt_id}"]
    # час тримається за клієнтом — інший не може його зайняти
    from mds.errors import Conflict
    with db.session() as s, pytest.raises(Conflict):
        bookings.create_appointment(s, seed["actors"]["admin"], master_id=seed["m1"], service_id=seed["haircut"],
                                    starts_at=datetime(2026, 11, 2, 15), client_name="X", client_phone="0500000000")
    h.click(f"acc:{appt_id}")
    assert "запис підтверджено" in h.session.texts()[-1]
    with db.session() as s:
        a = s.get(Appointment, appt_id)
        assert a.status == ApptStatus.CONFIRMED and not a.awaiting_client
    h.click(f"acc:{appt_id}")  # повторно — пропозиція вже неактуальна
    assert any("неактуальна" in x for x in h.session.alerts())


def test_client_picks_own_time_after_proposal(h, db, seed):
    from mds.services import bookings
    appt_id = _pending_from_bot(db, seed, h)
    with db.session() as s:
        bookings.propose_time(s, seed["actors"]["m1"], appt_id, datetime(2026, 11, 2, 15))
    h.click(f"mv:{appt_id}")
    h.click_text("Завтра")
    h.click_text("12:00")
    with db.session() as s:
        a = s.get(Appointment, appt_id)
        assert a.starts_at == datetime(2026, 11, 3, 12) and not a.awaiting_client and a.status == ApptStatus.PENDING


def test_staff_bot_busy_and_propose(h, db, pins):
    appt_id = _pending_from_bot(db, pins, h)
    staff_login(h, pins["m1"], "1111")
    h.click("s:list")
    h.click("s:ld:20261102")
    labels = [b.text for b in h.session.buttons()]
    assert "⛔ Зайнято" in labels and "🕐 Інший час" in labels
    h.click(f"s:mv:{appt_id}")
    h.click_text("Сьогодні")
    h.click_text("16:00")
    assert "запропоновано новий час" in h.session.texts()[-1]
    with db.session() as s:
        assert s.get(Appointment, appt_id).awaiting_client
    h.click(f"s:busy:{appt_id}")
    assert "час зайнятий" in h.session.texts()[-1]


def test_bug_report_goes_to_director_with_screenshot(h, db, seed, tmp_path):
    import base64
    from aiogram.methods import SendPhoto
    from mds.models import AuditLog, Notification
    from mds.services import bugreport
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 100).decode()
    with db.session() as s:
        s.get(User, seed["director"]).telegram_chat_id = 9001
    from mds.errors import ValidationError
    with db.session() as s, pytest.raises(ValidationError):
        bugreport.create(s, seed["actors"]["m1"], tmp_path, text="x")
    with db.session() as s:
        r = bugreport.create(s, seed["actors"]["m1"], tmp_path, text="Не закривається замовлення", page="today",
                             context={"device": "iPhone", "screen": "390×844", "errors": ["POST /api → 500: збій"]},
                             image_b64="data:image/png;base64," + png)
        assert r == {"ok": True, "delivered": 1}
    with db.session() as s:
        n = s.query(Notification).one()
        assert n.chat_id == 9001 and n.photo_path.endswith(".png")
        assert "Не закривається замовлення" in n.text and "Сторінка: today" in n.text and "500: збій" in n.text
        assert s.query(AuditLog).filter_by(entity="bug_report").count() == 1
    asyncio.run(deliver_outbox(h.bot, db))
    assert any(isinstance(c, SendPhoto) and c.chat_id == 9001 for c in h.session.calls)


def test_bug_report_rejects_non_image(db, seed, tmp_path):
    import base64
    from mds.errors import ValidationError
    from mds.services import bugreport
    with db.session() as s, pytest.raises(ValidationError):
        bugreport.create(s, seed["actors"]["m1"], tmp_path, text="Щось не так",
                         image_b64=base64.b64encode(b"<script>alert(1)</script>").decode())


def _visits(db, seed, master_key, n, tg=TG_ID, start_h=9):
    from mds.services import bookings, orders
    from mds.services.common import Actor as A
    with db.session() as s:
        for i in range(n):
            a = bookings.create_appointment(s, A.telegram(tg), master_id=seed[master_key], service_id=seed["haircut"],
                                            starts_at=datetime(2026, 11, 2, start_h + i), client_name="Катя", client_phone="0931234567")
            orders.close_order(s, seed["actors"]["admin"], a.id, amount=50000, payment_method="cash")


def test_favorite_master_after_three_visits(h, db, seed):
    from mds.models import Client
    from mds.services import bookings
    _visits(db, seed, "m2", 2)
    with db.session() as s:
        cid = s.query(Client).one().id
        assert bookings.favorite_master(s, cid) is None  # 2 візити — ще не улюблений
    _visits(db, seed, "m2", 1, start_h=12)
    with db.session() as s:
        m, n = bookings.favorite_master(s, cid)
        assert m.id == seed["m2"] and n == 3
    h.send("📅 Записатися")
    h.click_text("Стрижка")
    labels = [b.text for b in h.session.buttons()]
    assert labels[0].startswith("⭐ Ваш майстер Ірина")
    assert "3 рази були в майстра Ірина" in h.session.texts()[-1]


def test_favorite_master_respects_service_and_threshold_setting(db, seed):
    from mds.models import Client
    from mds.services import bookings, users as us
    from mds.services.common import Actor as A, update_settings
    _visits(db, seed, "m1", 2)
    with db.session() as s:
        cid = s.query(Client).one().id
        update_settings(s, A.system(), {"favorite_master_visits": 2})
        assert bookings.favorite_master(s, cid)[0].id == seed["m1"]
        # Олена більше не робить манікюр → для манікюру не пропонуємо
        us.update_user(s, seed["actors"]["director"], seed["m1"], {"service_ids": [seed["haircut"]]})
        assert bookings.favorite_master(s, cid, seed["manicure"]) is None
        assert bookings.favorite_master(s, cid, seed["haircut"])[0].id == seed["m1"]


class _NotModifiedSession(FakeSession):
    """Telegram відповідає «message is not modified» на будь-яке редагування."""

    async def make_request(self, bot, method, timeout=None):
        from aiogram.exceptions import TelegramBadRequest
        if isinstance(method, (EditMessageText, EditMessageReplyMarkup)):
            self.calls.append(method)
            raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
        return await super().make_request(bot, method, timeout)


class _BrokenSession(FakeSession):
    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, EditMessageText):
            self.calls.append(method)
            raise RuntimeError("щось зламалося")
        return await super().make_request(bot, method, timeout)


def test_same_screen_twice_does_not_hang_button(db, seed):
    h = BotHarness(db)
    h.session = _NotModifiedSession()
    h.bot = make_bot("123456:TEST-TOKEN", session=h.session)
    h.click("book")  # не кидає виняток
    answers = [c for c in h.session.calls if isinstance(c, AnswerCallbackQuery)]
    assert answers, "кнопка має отримати відповідь, щоб не крутилася"


def test_unexpected_error_answers_user(db, seed):
    h = BotHarness(db)
    h.session = _BrokenSession()
    h.bot = make_bot("123456:TEST-TOKEN", session=h.session)
    h.click("book")
    assert any("Щось пішло не так" in (a or "") for a in h.session.alerts())
