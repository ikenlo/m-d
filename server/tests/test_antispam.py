"""Захист від спаму записів: бот і сайт (публічний API)."""

from datetime import datetime

import pytest

from mds import clock
from mds.api.routers import public
from mds.models import Appointment, CallbackRequest, Client
from mds.services import bookings, callbacks, catalog
from mds.services.antispam import TooMany
from mds.services.common import Actor, update_settings

from .test_api import client  # noqa: F401, F811 — фікстура

TG = Actor.telegram(555)


@pytest.fixture(autouse=True)
def _reset_limits():
    public.READ_LIMIT.reset()
    public.BOOK_LIMIT.reset()
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
        # скасування не звільняє добовий ліміт (захист від «записав/скасував»)
        a = s.query(Appointment).first()
        bookings.set_status(s, seed["actors"]["director"], a.id, "cancelled")
        _book(s, seed, 14)  # третій за добу — ще можна
        b = s.query(Appointment).filter_by(status="pending").first()
        bookings.set_status(s, seed["actors"]["director"], b.id, "cancelled")
        with pytest.raises(TooMany, match="за добу"):
            _book(s, seed, 16)
        # адміністратор записує без обмежень
        _book(s, seed, 16, actor=seed["actors"]["admin"])
        # блокування клієнта
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
        # заявки «передзвоніть»: 2 за добу з номера (повтор протягом години — та сама заявка)
        callbacks.create_request(s, TG, "0671112233", "Оля")
        clock.set_now(datetime(2026, 11, 2, 10))
        callbacks.create_request(s, TG, "0671112233", "Оля")
        clock.set_now(datetime(2026, 11, 2, 12))
        with pytest.raises(TooMany):
            callbacks.create_request(s, TG, "0671112233", "Оля")
        assert s.query(CallbackRequest).count() == 2


def _payload(**kw):
    p = {"name": "Оля", "phone": "067 111 22 33", "service_id": 0, "master_id": None, "date": "2026-11-03",
         "time": "10:00", "website": "", "elapsed_ms": 9000}
    p.update(kw)
    return p


def test_public_api_disabled_then_booking_with_protections(client, db):  # noqa: F811
    seed = client.ids
    clock.set_now(datetime(2026, 11, 2, 8))
    assert client.get("/api/public/services").status_code == 503
    with db.session() as s:
        update_settings(s, Actor.system(), {"public_booking_enabled": True})
    svc = client.get("/api/public/services").json()
    assert any(x["id"] == seed["haircut"] for x in svc)
    r = client.get(f"/api/public/slots?date=2026-11-03&service={seed['haircut']}")
    times = [x["time"] for x in r.json()["slots"]]
    assert "10:00" in times
    p = _payload(service_id=seed["haircut"])
    # пастка для ботів і надто швидке заповнення
    assert client.post("/api/public/bookings", json={**p, "website": "http://spam"}).status_code == 422
    assert client.post("/api/public/bookings", json={**p, "elapsed_ms": 500}).status_code == 422
    assert client.post("/api/public/bookings", json={**p, "name": "Купи www.spam.com"}).status_code == 422
    assert client.post("/api/public/bookings", json={**p, "phone": "+1 555 123 4567"}).status_code == 422
    public.BOOK_LIMIT.reset()
    r = client.post("/api/public/bookings", json=p)
    assert r.status_code == 201 and r.json()["status"] == "pending"
    with db.session() as s:
        a = s.get(Appointment, r.json()["id"])
        assert a.source == "site" and a.client.phone == "+380671112233"
    # той самий час знову — зайнято (інший майстер вільний → запишеться до нього, потім — 409)
    r2 = client.post("/api/public/bookings", json={**p, "phone": "0670000009"})
    assert r2.status_code == 201
    assert client.post("/api/public/bookings", json={**p, "phone": "0670000008"}).status_code == 409
    # слот зник зі списку
    times = [x["time"] for x in client.get(f"/api/public/slots?date=2026-11-03&service={seed['haircut']}").json()["slots"]]
    assert "10:00" not in times
    # минулий час сьогодні не показується
    clock.set_now(datetime(2026, 11, 3, 13, 5))
    times = [x["time"] for x in client.get(f"/api/public/slots?date=2026-11-03&service={seed['haircut']}").json()["slots"]]
    assert times and all(t >= "13:30" for t in times)


def test_public_rate_limit_per_ip_and_cors(client, db, monkeypatch):  # noqa: F811
    seed = client.ids
    clock.set_now(datetime(2026, 11, 2, 8))
    with db.session() as s:
        update_settings(s, Actor.system(), {"public_booking_enabled": True})
    codes = [client.post("/api/public/bookings", json=_payload(service_id=seed["haircut"], website="x")).status_code for _ in range(6)]
    assert codes[:5] == [422] * 5 and codes[5] == 429
    # CORS лише для дозволених доменів і лише /api/public
    assert "access-control-allow-origin" not in client.get("/api/public/info", headers={"Origin": "https://evil.example"}).headers


def _cb(**kw):
    p = {"name": "Оля", "phone": "067 111 22 33", "service": "Фарбування", "when": "Завтра", "comment": "",
         "website": "", "elapsed_ms": 6000}
    p.update(kw)
    return p


def test_public_quick_callback_from_site(client, db):  # noqa: F811
    clock.set_now(datetime(2026, 11, 2, 9))
    assert client.post("/api/public/callbacks", json=_cb()).status_code == 503
    with db.session() as s:
        update_settings(s, Actor.system(), {"public_booking_enabled": True})
    assert client.post("/api/public/callbacks", json=_cb(website="x")).status_code == 422
    assert client.post("/api/public/callbacks", json=_cb(elapsed_ms=800)).status_code == 422
    assert client.post("/api/public/callbacks", json=_cb(comment="дивіться t.me/spam")).status_code == 422
    assert client.post("/api/public/callbacks", json=_cb(phone="+1 555 123 4567")).status_code == 422
    public.BOOK_LIMIT.reset()
    r = client.post("/api/public/callbacks", json=_cb(comment="  довге  волосся "))
    assert r.status_code == 201, r.text
    # повтор протягом години — та сама заявка, без дубля
    assert client.post("/api/public/callbacks", json=_cb()).json()["id"] == r.json()["id"]
    with db.session() as s:
        req = s.query(CallbackRequest).one()
        assert (req.phone, req.name, req.source) == ("+380671112233", "Оля", "site")
        assert req.topic == "Фарбування · Завтра · довге волосся"
    # ліміт спроб з однієї IP
    codes = [client.post("/api/public/callbacks", json=_cb(phone=f"06700000{i:02d}")).status_code for i in range(6)]
    assert 429 in codes
