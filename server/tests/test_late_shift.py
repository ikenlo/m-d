from datetime import datetime

import pytest

from mds import clock
from mds.errors import Forbidden
from mds.models import Appointment, Client, Notification
from mds.services import bookings, reminders
from mds.services.antispam import TooMany
from mds.services.common import Actor, update_settings

from .test_api import client, login  # noqa: F401


def _appt(s, seed, hour, minute=0, phone="0671110000", actor=None, tg=None):
    a = bookings.create_appointment(s, actor or seed["actors"]["admin"], master_id=seed["m1"], service_id=seed["haircut"],
                                    starts_at=datetime(2026, 11, 2, hour, minute), client_name="Клієнт", client_phone=phone)
    if tg:
        a.client.telegram_user_id = tg
    return a


def test_shift_moves_following_appointments_and_notifies(db, seed):
    clock.set_now(datetime(2026, 11, 2, 9, 15))
    with db.session() as s:
        first = _appt(s, seed, 9, 0, "0671110001")
        a2 = _appt(s, seed, 10, 0, "0671110002", tg=7002)
        a3 = _appt(s, seed, 11, 0, "0671110003")
        ids = (first.id, a2.id, a3.id)
    with db.session() as s:
        r = bookings.shift_appointments(s, seed["actors"]["m1"], master_id=seed["m1"], minutes=15)
        assert [x["starts_at"][11:16] for x in r["shifted"]] == ["10:15", "11:15"]
        assert r["telegram"] == 1 and len(r["sms"]) == 1 and "11:15" in r["sms"][0]["text"]
        assert [s.get(Appointment, i).starts_at.strftime("%H:%M") for i in ids] == ["09:00", "10:15", "11:15"]
        assert any(n.chat_id == 7002 and "10:15" in n.text for n in s.query(Notification))
        with pytest.raises(Forbidden):
            bookings.shift_appointments(s, seed["actors"]["m2"], master_id=seed["m1"], minutes=15)
    clock.set_now(None)


def test_shift_api_and_unclosed_list_and_reminder(client, db):  # noqa: F811
    seed = client.ids
    clock.set_now(datetime(2026, 11, 2, 9, 0))
    with db.session() as s:
        a = _appt(s, seed, 9, 0, "0671110010")
        _appt(s, seed, 10, 0, "0671110011")
        s.get(type(a.master), seed["m1"]).telegram_chat_id = 9002
        aid = a.id
    m1 = login(client, "olena", "Master123")
    r = client.post("/api/appointments/shift", headers=m1, json={"master_id": seed["m1"], "minutes": 20, "from_appt_id": aid}).json()
    assert [x["starts_at"][11:16] for x in r["shifted"]] == ["09:20", "10:20"]
    assert r["sms_sent"] == 0 and len(r["call"]) == 2
    clock.set_now(datetime(2026, 11, 2, 10, 40))
    lst = client.get("/api/appointments/unclosed", headers=m1).json()
    assert [x["id"] for x in lst] == [aid]
    with db.session() as s:
        assert reminders.remind_unclosed(s) == 1
        assert reminders.remind_unclosed(s) == 0
        assert any("Не закрито замовлення" in n.text and n.chat_id == 9002 for n in s.query(Notification))
    clock.set_now(None)


def test_whitelist_skips_spam_limits(db, seed):
    clock.set_now(datetime(2026, 11, 2, 8))
    tg = Actor.telegram(1)
    with db.session() as s:
        for h in (10, 12, 14, 16):
            _appt(s, seed, h, phone="0687166910", actor=tg)
        assert s.query(Client).filter_by(phone="+380687166910").one()
        update_settings(s, seed["actors"]["director"], {"antispam_whitelist": ["067 000 11 22"]})
        with pytest.raises(TooMany):
            _appt(s, seed, 18, phone="0687166910", actor=tg)
    clock.set_now(None)
