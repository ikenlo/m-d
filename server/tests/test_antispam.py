from datetime import datetime

import pytest

from mds import clock
from mds.models import Appointment, CallbackRequest, Client
from mds.services import bookings, callbacks, catalog
from mds.services.antispam import TooMany
from mds.services.common import Actor, update_settings

TG = Actor.telegram(555)


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    clock.set_now(None)


def _book(s, seed, hour, phone="0671112233", actor=TG, master="m1"):
    return bookings.create_appointment(s, actor, master_id=seed[master], service_id=seed["haircut"],
                                       starts_at=datetime(2026, 11, 3, hour), client_name="Оля", client_phone=phone)


def test_bot_limits_active_per_day_and_block(db, seed):
    clock.set_now(datetime(2026, 11, 2, 8))
    with db.session() as s:
        _book(s, seed, 10)
        _book(s, seed, 12)
        with pytest.raises(TooMany, match="2 активні"):
            _book(s, seed, 14)
        a = s.query(Appointment).first()
        bookings.set_status(s, seed["actors"]["director"], a.id, "cancelled")
        _book(s, seed, 14)
        b = s.query(Appointment).filter_by(status="pending").first()
        bookings.set_status(s, seed["actors"]["director"], b.id, "cancelled")
        with pytest.raises(TooMany, match="за добу"):
            _book(s, seed, 16)
        _book(s, seed, 16, actor=seed["actors"]["director"])
        c = s.query(Client).one()
        catalog.update_client(s, seed["actors"]["director"], c.id, {"blocked": True, "blocked_reason": "спам"})
        update_settings(s, seed["actors"]["director"], {"antispam_max_active": 0, "antispam_max_per_day": 0})
        with pytest.raises(TooMany, match="недоступний"):
            _book(s, seed, 18)


def test_pending_queue_limit_and_callbacks(db, seed):
    clock.set_now(datetime(2026, 11, 2, 8))
    d = seed["actors"]["director"]
    with db.session() as s:
        update_settings(s, d, {"antispam_max_pending_total": 2})
        _book(s, seed, 10, phone="0670000001", actor=Actor.telegram(1))
        _book(s, seed, 12, phone="0670000002", actor=Actor.telegram(2))
        with pytest.raises(TooMany, match="багато нових записів"):
            _book(s, seed, 14, phone="0670000003", actor=Actor.telegram(3))
        callbacks.create_request(s, TG, "0671112233", "Оля")
        clock.set_now(datetime(2026, 11, 2, 10))
        callbacks.create_request(s, TG, "0671112233", "Оля")
        clock.set_now(datetime(2026, 11, 2, 12))
        with pytest.raises(TooMany):
            callbacks.create_request(s, TG, "0671112233", "Оля")
        assert s.query(CallbackRequest).count() == 2
