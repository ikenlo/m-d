from datetime import date, datetime

import pytest

from mds.errors import Conflict, ValidationError
from mds.models import Role
from mds.services import bookings, common, schedule, users
from mds.services.common import Actor

MON = date(2026, 11, 2)


def _book(s, actor, seed, hour, minute=0, master="m1", dur=None, confirm=False, name="К"):
    return bookings.create_appointment(s, actor, master_id=seed[master], service_id=seed["haircut"],
                                       starts_at=datetime(2026, 11, 2, hour, minute), client_name=name,
                                       duration_min=dur, confirm_off_hours=confirm)


def test_off_hours_policy_forbid_allow_and_free_place(db, seed):
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        common.update_settings(s, d, {"off_hours_booking": "forbid"})
        with pytest.raises(Conflict) as e:
            _book(s, m1, seed, 20, confirm=True)
        assert e.value.code == "off_hours_forbidden"
        r = bookings.booking_options(s, m1, seed["m1"], seed["haircut"], MON)
        assert r["off_hours"] == [] and r["off_policy"] == "forbid"
        common.update_settings(s, d, {"off_hours_booking": "allow"})
        assert _book(s, m1, seed, 20, confirm=True).id


def test_day_off_booking_only_when_place_is_free(db, seed):
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        schedule.set_day(s, d, seed["m1"], MON, off=True)
        common.update_settings(s, d, {"workplaces": 1})
        r = bookings.booking_options(s, m1, seed["m1"], seed["haircut"], MON)
        assert r["working"] is False and r["slots"] == [] and "10:00" not in r["off_hours"]
        with pytest.raises(Conflict) as e:
            _book(s, m1, seed, 10, confirm=True)
        assert e.value.code == "no_place"
        common.update_settings(s, d, {"workplaces": 2})
        r = bookings.booking_options(s, m1, seed["m1"], seed["haircut"], MON)
        assert "10:00" in r["off_hours"]
        assert _book(s, m1, seed, 10, confirm=True).id


def test_free_windows_follow_custom_durations(db, seed):
    adm, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        _book(s, adm, seed, 10, dur=45)
        _book(s, adm, seed, 11, 0, dur=83, name="К2")
        times = [t.strftime("%H:%M") for t in bookings.free_slots(s, seed["m1"], seed["haircut"], MON)]
        assert "10:45" not in times
        assert "12:23" in times
        r = bookings.booking_options(s, m1, seed["m1"], seed["haircut"], MON, duration_min=15)
        assert "10:45" in r["slots"] and "12:23" in r["slots"]


def test_schedule_respects_workplaces(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        m3 = users.create_user(s, Actor.system(), username="m3", password="Master123", full_name="Третя", role=Role.MASTER)
        with pytest.raises(ValidationError):
            schedule.set_day(s, d, m3.id, MON, start="10:00", end="18:00")
        schedule.set_day(s, d, seed["m2"], MON, off=True)
        assert schedule.set_day(s, d, m3.id, MON, start="10:00", end="18:00")


def test_max_masters_limit(db, seed):
    d, sysa = seed["actors"]["director"], Actor.system()
    with db.session() as s:
        common.update_settings(s, d, {"max_masters": 3})
        users.create_user(s, sysa, username="m3", password="Master123", full_name="Третя", role=Role.MASTER)
        with pytest.raises(ValidationError):
            users.create_user(s, sysa, username="m4", password="Master123", full_name="Четверта", role=Role.MASTER)
        adm = users.create_user(s, sysa, username="dir2", password="Director123", full_name="Директор 2", role=Role.DIRECTOR)
        with pytest.raises(ValidationError):
            users.update_user(s, d, adm.id, {"role": Role.MASTER})


def test_reschedule_into_off_hours_needs_confirmation(db, seed):
    adm = seed["actors"]["director"]
    with db.session() as s:
        a = _book(s, adm, seed, 10)
        with pytest.raises(Conflict) as e:
            bookings.reschedule(s, adm, a.id, starts_at=datetime(2026, 11, 2, 20))
        assert e.value.code == "off_hours"
        assert bookings.reschedule(s, adm, a.id, starts_at=datetime(2026, 11, 2, 20), confirm_off_hours=True).starts_at.hour == 20
