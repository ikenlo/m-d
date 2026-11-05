"""Службовий API /api/ext/v1 для MDs manager: ключ, каталог, слоти, запис з атрибуцією, візити, реклама."""
from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from mds.models import Appointment, AppointmentAttribution, AuditLog, Client, Expense
from mds.services import integration, orders
from mds.services.common import Actor

TOKEN = "integration-test-token-0123456789"
H = {"X-MDS-Integration-Token": TOKEN}


@pytest.fixture
def ext(db, seed, tmp_path):
    from mds.api.app import create_app
    from mds.config import Config

    cfg = Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="test-secret-key-for-unit-tests-only-0123456789",
                 integration_token=TOKEN)
    with TestClient(create_app(cfg, db)) as c:
        yield c, seed


def _book(c, ids, **kw):
    body = {
        "channel": "instagram", "service_id": ids["haircut"], "master_id": ids["m1"], "starts_at": "2026-11-02T12:00",
        "client": {"name": "Олена", "phone": "0671234567", "instagram_user_id": "ig-1"},
        "external_ref": "conv-1-msg-5",
        "attribution": {"lead_source": "ig_reels_1201", "utm_source": "instagram", "utm_campaign": "nov_haircut"},
    }
    body.update(kw)
    return c.post("/api/ext/v1/appointments", headers=H, json=body)


def test_requires_token(ext):
    c, _ = ext
    assert c.get("/api/ext/v1/catalog").status_code == 401
    assert c.get("/api/ext/v1/catalog", headers={"X-MDS-Integration-Token": "wrong"}).status_code == 401
    assert c.get("/api/ext/v1/catalog", headers=H).status_code == 200


def test_disabled_without_configured_token(client_api):
    c, _, _ = client_api
    assert c.get("/api/ext/v1/catalog", headers=H).status_code == 404


def test_staff_jwt_does_not_open_ext(client_api):
    c, _, auth = client_api
    assert c.get("/api/ext/v1/catalog", headers=auth).status_code == 404


def test_catalog_and_slots(ext):
    c, ids = ext
    cat = c.get("/api/ext/v1/catalog", headers=H).json()
    haircut = next(x for x in cat["services"] if x["id"] == ids["haircut"])
    assert haircut["price"] == 50000 and haircut["duration_min"] == 60
    assert set(haircut["master_ids"]) >= {ids["m1"], ids["m2"]}
    slots = c.get("/api/ext/v1/slots", headers=H, params={"service_id": ids["haircut"], "date_from": "2026-11-02",
                                                          "date_to": "2026-11-02"}).json()
    times = [x["starts_at"] for x in slots]
    assert "2026-11-02T08:00" not in times  # мінімальний запас часу для клієнтів
    assert "2026-11-02T12:00" in times
    assert c.get("/api/ext/v1/slots", headers=H, params={"service_id": ids["haircut"], "date_from": "2026-11-02",
                                                         "date_to": "2026-12-30"}).status_code == 422


def test_book_with_attribution_and_idempotency(ext):
    c, ids = ext
    r = _book(c, ids)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["created"] is True and a["source"] == "instagram" and a["status"] in ("pending", "confirmed")
    again = _book(c, ids).json()
    assert again["id"] == a["id"] and again["created"] is False
    with c.app.state.db.session() as s:
        attr = s.get(AppointmentAttribution, a["id"])
        assert (attr.channel, attr.lead_source, attr.utm_campaign) == ("instagram", "ig_reels_1201", "nov_haircut")
        cl = s.get(Appointment, a["id"]).client
        assert cl.instagram_user_id == "ig-1" and cl.source == "instagram" and cl.phone == "+380671234567"
        assert s.query(AuditLog).filter_by(entity="appointment", entity_id=a["id"], action="attribution").count() == 1


def test_book_respects_slot_and_antispam(ext):
    c, ids = ext
    assert _book(c, ids).status_code == 200
    r = _book(c, ids, external_ref="other", client={"name": "Ірина", "phone": "0931112233"})
    assert r.status_code == 409 and r.json()["error"] == "slot_taken"
    assert _book(c, ids, channel="sms", external_ref="x3").status_code == 422
    r = _book(c, ids, starts_at="2026-11-01T12:00", external_ref="past")
    assert r.status_code == 422


def test_existing_client_keeps_first_source(ext):
    c, ids = ext
    with c.app.state.db.session() as s:
        s.add(Client(name="Олена", phone="+380671234567", source="рекомендація"))
    _book(c, ids)
    with c.app.state.db.session() as s:
        cl = s.query(Client).filter_by(phone="+380671234567").one()
        assert cl.source == "рекомендація" and cl.instagram_user_id == "ig-1"


def test_lookup_and_cancel_only_own(ext):
    c, ids = ext
    a = _book(c, ids).json()
    mine = c.post("/api/ext/v1/appointments/lookup", headers=H, json={"instagram_user_id": "ig-1"}).json()
    assert [x["id"] for x in mine] == [a["id"]]
    assert c.post("/api/ext/v1/appointments/lookup", headers=H, json={"phone": "0999999999"}).json() == []
    r = c.post(f"/api/ext/v1/appointments/{a['id']}/cancel", headers=H, json={"phone": "0999999999"})
    assert r.status_code == 404
    r = c.post(f"/api/ext/v1/appointments/{a['id']}/cancel", headers=H, json={"telegram_user_id": None, "phone": "0671234567"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_visits_and_ad_spend(ext, db):
    c, ids = ext
    a = _book(c, ids).json()
    with db.session() as s:
        d = Actor.of(s.get(__import__("mds.models", fromlist=["User"]).User, ids["director"]))
        appt = s.get(Appointment, a["id"])
        appt.status = "confirmed"
        orders.close_order(s, d, a["id"], amount=55000, payment_method="cash", materials=[])
        s.add(Expense(category="advertising", amount=120000, spent_on=date(2026, 11, 1), description="Meta Ads",
                      created_by=ids["director"]))
        s.add(Expense(category="rent", amount=999999, spent_on=date(2026, 11, 1), created_by=ids["director"]))
    v = c.get("/api/ext/v1/visits", headers=H, params={"date_from": "2026-11-02", "date_to": "2026-11-02"}).json()
    assert len(v) == 1
    assert v[0]["paid_amount"] == 55000 and v[0]["status"] == "completed"
    assert v[0]["attribution"]["lead_source"] == "ig_reels_1201"
    spend = c.get("/api/ext/v1/ad-spend", headers=H, params={"date_from": "2026-11-01", "date_to": "2026-11-30"}).json()
    assert spend["total"] == 120000 and len(spend["items"]) == 1


def test_service_layer_book_telegram_ai(db, seed):
    with db.session() as s:
        a, created = integration.book(s, channel="telegram-ai", service_id=seed["haircut"], master_id=seed["m2"],
                                      starts_at=datetime(2026, 11, 2, 14), client_name="Дарина", client_phone="0501112233",
                                      telegram_user_id=777)
        assert created and a.source == "telegram-ai" and a.client.telegram_user_id == 777
        assert a.client.source == "telegram-бот"
