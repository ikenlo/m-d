from datetime import date, datetime

import pytest

from mds.errors import Conflict, Forbidden, NotFound, ValidationError
from mds.models import Appointment, ApptStatus, AuditLog, Notification, User
from mds.services import bookings, users
from mds.services.common import Actor

DAY = date(2026, 11, 2)


def at(h, m=0, d=DAY):
    return datetime(d.year, d.month, d.day, h, m)


def book(s, actor, master, service, start, phone="0671112233", name="Анна", **kw):
    return bookings.create_appointment(
        s, actor, master_id=master, service_id=service, starts_at=start,
        client_name=name, client_phone=phone, **kw,
    )


def test_create_and_prevent_double_booking(db, seed):
    adm = seed["actors"]["admin"]
    with db.session() as s:
        a = book(s, adm, seed["m1"], seed["manicure"], at(10))
        assert a.ends_at == at(11, 30)
        assert a.status == ApptStatus.CONFIRMED
        assert a.client.phone == "+380671112233"
    for start in (at(10), at(11), at(9, 30), at(9, 0)):
        with db.session() as s, pytest.raises(Conflict):
            book(s, adm, seed["m1"], seed["manicure"], start, phone="0500000000")
    with db.session() as s:
        book(s, adm, seed["m2"], seed["manicure"], at(10), phone="0500000000")
        book(s, adm, seed["m1"], seed["haircut"], at(11, 30), phone="0500000001")


def test_cancelled_frees_slot(db, seed):
    adm = seed["actors"]["admin"]
    with db.session() as s:
        a = book(s, adm, seed["m1"], seed["manicure"], at(10))
        bookings.set_status(s, adm, a.id, ApptStatus.CANCELLED, "клієнт захворів")
    with db.session() as s:
        b = book(s, adm, seed["m1"], seed["manicure"], at(10), phone="0501234567")
        assert b.id


def test_free_slots(db, seed):
    adm = seed["actors"]["admin"]
    with db.session() as s:
        book(s, adm, seed["m1"], seed["haircut"], at(12))
    with db.session() as s:
        slots = bookings.free_slots(s, seed["m1"], seed["haircut"], DAY)
        times = [t.strftime("%H:%M") for t in slots]
        assert "09:00" in times and "11:00" in times
        assert "11:30" not in times and "12:00" not in times and "12:30" not in times
        assert "13:00" in times
        assert times[-1] == "19:00"
        assert bookings.free_slots(s, seed["m1"], seed["haircut"], DAY, for_client=True)[0] == at(9)
        assert bookings.free_slots(s, seed["m1"], seed["haircut"], date(2026, 11, 8)) == []


def test_master_schedule_override(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        users.update_user(s, d, seed["m1"], {"schedule": {"0": ["14:00", "16:00"]}})
    with db.session() as s:
        times = [t.strftime("%H:%M") for t in bookings.free_slots(s, seed["m1"], seed["haircut"], DAY)]
        assert times == ["14:00", "14:30", "15:00"]
        assert bookings.free_slots(s, seed["m1"], seed["haircut"], date(2026, 11, 3)) == []


def test_reschedule_checks_conflicts_and_audits(db, seed):
    adm = seed["actors"]["admin"]
    with db.session() as s:
        book(s, adm, seed["m1"], seed["haircut"], at(10))
        b = book(s, adm, seed["m1"], seed["haircut"], at(12), phone="0509999999")
    with db.session() as s, pytest.raises(Conflict):
        bookings.reschedule(s, adm, b.id, starts_at=at(10, 30))
    with db.session() as s:
        bookings.reschedule(s, adm, b.id, starts_at=at(15))
        bookings.reschedule(s, adm, b.id, starts_at=at(15, 30))
    with db.session() as s:
        b2 = s.get(Appointment, b.id)
        assert b2.starts_at == at(15, 30)
        logs = s.query(AuditLog).filter_by(entity="appointment", entity_id=b.id, action="update").all()
        assert len(logs) == 2


def test_master_sees_and_edits_only_own(db, seed):
    adm, m1, m2 = seed["actors"]["admin"], seed["actors"]["m1"], seed["actors"]["m2"]
    with db.session() as s:
        a = book(s, adm, seed["m1"], seed["haircut"], at(10))
        book(s, adm, seed["m2"], seed["haircut"], at(10), phone="0501111111")
    with db.session() as s:
        own = bookings.list_appointments(s, m1, date_from=DAY, date_to=DAY, master_id=seed["m2"])
        assert [x.master_id for x in own] == [seed["m1"]]
        with pytest.raises(Forbidden):
            bookings.get_appointment(s, m2, a.id)
        with pytest.raises(Forbidden):
            book(s, m2, seed["m1"], seed["haircut"], at(14))
        book(s, m2, seed["m2"], seed["haircut"], at(14), phone="0502222222")


def test_status_transitions(db, seed):
    adm = seed["actors"]["admin"]
    with db.session() as s:
        a = book(s, adm, seed["m1"], seed["haircut"], at(10), status=ApptStatus.PENDING)
        bookings.set_status(s, adm, a.id, ApptStatus.CONFIRMED)
        bookings.set_status(s, adm, a.id, ApptStatus.IN_PROGRESS)
        with pytest.raises(Conflict):
            bookings.set_status(s, adm, a.id, ApptStatus.NO_SHOW)
        bookings.set_status(s, adm, a.id, ApptStatus.CANCELLED)
        with pytest.raises(Conflict):
            bookings.reschedule(s, adm, a.id, starts_at=at(15))


def test_telegram_client_flow(db, seed):
    tg = Actor.telegram(777)
    with db.session() as s:
        a = book(s, tg, seed["m1"], seed["haircut"], at(10), name="Катя", phone="+380931234567")
        assert a.status == ApptStatus.PENDING and a.source == "telegram"
        assert a.client.telegram_user_id == 777
    with db.session() as s:
        mine = bookings.upcoming_for_telegram(s, 777)
        assert [x.id for x in mine] == [a.id]
        assert bookings.upcoming_for_telegram(s, 888) == []
        with pytest.raises(NotFound):
            bookings.get_appointment(s, Actor.telegram(888), a.id)
        with pytest.raises(Forbidden):
            bookings.set_status(s, tg, a.id, ApptStatus.CONFIRMED)
        with pytest.raises(ValidationError):
            book(s, tg, seed["m1"], seed["haircut"], at(7), phone="+380931234567")
        with pytest.raises(ValidationError):
            book(s, tg, seed["m1"], seed["haircut"], datetime(2026, 11, 1, 10), phone="+380931234567")
        bookings.reschedule(s, tg, a.id, starts_at=at(16))
        bookings.set_status(s, tg, a.id, ApptStatus.CANCELLED)
    with db.session() as s:
        assert s.get(Appointment, a.id).status == ApptStatus.CANCELLED
        assert s.get(Appointment, a.id).cancel_reason == "Скасовано клієнтом"


def test_notifications_go_to_director_and_master(db, seed):
    with db.session() as s:
        s.get(User, seed["director"]).telegram_chat_id = 1001
        s.get(User, seed["m1"]).telegram_chat_id = 2001
    with db.session() as s:
        book(s, Actor.telegram(777), seed["m1"], seed["haircut"], at(10), phone="0931234567")
    with db.session() as s:
        chats = sorted(n.chat_id for n in s.query(Notification).all())
        assert chats == [1001, 2001]
        assert "Новий запис" in s.query(Notification).first().text
