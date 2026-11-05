from datetime import datetime, timedelta

from aiogram.types import Contact

from mds import clock
from mds.models import Appointment, ApptStatus, Client, Notification
from mds.services import bookings, reminders
from mds.services.common import Actor, update_settings
from tests.test_bot import TG_ID, BotHarness, h  # noqa: F401


def _appt(db, seed, starts, created, tg=TG_ID, agree=True, status=ApptStatus.CONFIRMED, phone="0931234567"):
    clock.set_now(created)
    with db.session() as s:
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=starts, client_name="Катя", client_phone=phone)
        a.client.telegram_user_id = tg
        a.client.reminders = agree
        a.status = status
        return a.id


def _run(db, at):
    clock.set_now(at)
    with db.session() as s:
        return reminders.send_due(s)


def _texts(db):
    with db.session() as s:
        return [n.text for n in s.query(Notification).filter_by(chat_id=TG_ID).order_by(Notification.id)]


def test_day_and_two_hour_reminders_once(db, seed):
    visit = datetime(2026, 11, 4, 15, 0)
    _appt(db, seed, visit, created=datetime(2026, 11, 2, 9, 0))
    assert _run(db, datetime(2026, 11, 3, 14, 0)) == 0
    assert _run(db, datetime(2026, 11, 3, 15, 0)) == 1
    assert _run(db, datetime(2026, 11, 3, 18, 0)) == 0
    assert _run(db, datetime(2026, 11, 4, 13, 0)) == 1
    assert _run(db, datetime(2026, 11, 4, 14, 0)) == 0
    t = _texts(db)
    assert len(t) == 2 and "Завтра о 15:00" in t[0] and "Сьогодні о 15:00" in t[1] and "Олена" in t[0]
    with db.session() as s:
        n = s.query(Notification).filter_by(chat_id=TG_ID).first()
        assert [b[1] for b in n.buttons[0]] == [f"mv:{1}", f"cnl:{1}"] and n.buttons[1][0][1] == "rem:0"


def test_no_reminders_without_consent_or_for_pending_or_late_booking(db, seed):
    _appt(db, seed, datetime(2026, 11, 4, 10), created=datetime(2026, 11, 2, 9), agree=None, tg=1, phone="0670000001")
    _appt(db, seed, datetime(2026, 11, 4, 12), created=datetime(2026, 11, 2, 9), agree=False, tg=2, phone="0670000002")
    _appt(db, seed, datetime(2026, 11, 4, 14), created=datetime(2026, 11, 2, 9), status=ApptStatus.PENDING,
          tg=3, phone="0670000003")
    _appt(db, seed, datetime(2026, 11, 4, 18), created=datetime(2026, 11, 3, 22))
    assert _run(db, datetime(2026, 11, 3, 22, 30)) == 0
    assert _run(db, datetime(2026, 11, 4, 16, 0)) == 1
    assert len(_texts(db)) == 1


def test_reschedule_resets_and_director_can_disable(db, seed):
    aid = _appt(db, seed, datetime(2026, 11, 4, 15), created=datetime(2026, 11, 2, 9))
    assert _run(db, datetime(2026, 11, 3, 16)) == 1
    with db.session() as s:
        bookings.reschedule(s, seed["actors"]["director"], aid, starts_at=datetime(2026, 11, 5, 15))
        assert s.get(Appointment, aid).reminded_day is False
    assert _run(db, datetime(2026, 11, 4, 15, 30)) == 1
    with db.session() as s:
        update_settings(s, seed["actors"]["director"], {"reminders_enabled": False})
    assert _run(db, datetime(2026, 11, 5, 13, 30)) == 0


def test_bot_asks_once_and_saves_choice(h, db):  # noqa: F811
    h.send("📅 Записатися")
    h.click_text("Стрижка")
    h.click_text("Олена")
    h.click_text("Сьогодні")
    h.click_text("10:00")
    h.send(contact=Contact(phone_number="380931234567", first_name="Катя", user_id=TG_ID))
    h.click("ok")
    assert "Нагадати вам про візит" in h.session.texts()[-1]
    assert [b.callback_data for b in h.session.buttons()] == ["rem:1", "rem:0"]
    h.click("rem:1")
    assert "Нагадаємо за добу" in h.session.texts()[-1]
    with db.session() as s:
        assert s.query(Client).one().reminders is True
    h.send("📅 Записатися")
    h.click_text("Стрижка")
    h.click_text("Олена")
    h.click_text("Сьогодні")
    h.click_text("12:00")
    h.click("ok")
    assert "Нагадати вам про візит" not in h.session.texts()[-1]
    h.click("rem:0")
    with db.session() as s:
        assert s.query(Client).one().reminders is False
    h.send("/remind")
    assert "увімкнено" in h.session.texts()[-1]
    with db.session() as s:
        assert s.query(Client).one().reminders is True


def test_unknown_client_cannot_toggle(db, seed):
    other = BotHarness(db, user_id=42)
    other.click("rem:1")
    assert any("Спочатку запишіться" in a for a in other.session.alerts())
    with db.session() as s:
        assert s.query(Client).count() == 0
        _ = Actor
        _ = timedelta
