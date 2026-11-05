from datetime import date, datetime

import pytest

from mds import clock
from mds.errors import Conflict, Forbidden
from mds.models import ShiftStatus
from mds.services import bookings, catalog, orders, schedule


def _book(s, actor, seed, hour, phone=None, service=None, master="m1"):
    return bookings.create_appointment(s, actor, master_id=seed[master], service_id=service or seed["haircut"],
                                       starts_at=datetime(2026, 11, 2, hour), client_name="Клієнт",
                                       client_phone=phone, confirm_off_hours=True)


def test_master_off_hours_booking_needs_confirmation(db, seed):
    m1 = seed["actors"]["m1"]
    with db.session() as s:
        with pytest.raises(Conflict) as e:
            bookings.create_appointment(s, m1, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 20), client_name="Пізній")
        assert e.value.code == "off_hours"
        a = bookings.create_appointment(s, m1, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 20), client_name="Пізній", confirm_off_hours=True)
        assert a.starts_at.hour == 20
        b = bookings.create_appointment(s, m1, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="Вчасний")
        assert b.id
        bookings.create_appointment(s, seed["actors"]["admin"], master_id=seed["m1"], service_id=seed["haircut"],
                                    starts_at=datetime(2026, 11, 2, 21), client_name="Адмін записав")


def test_booking_options_day_off_and_open_shift(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        schedule.set_day(s, d, seed["m1"], date(2026, 11, 2), off=True)
        r = bookings.booking_options(s, m1, seed["m1"], seed["haircut"], date(2026, 11, 2))
        assert r["working"] is False and r["slots"] == [] and r["off_hours"][0] == "08:00"
        assert r["shift_open"] is False
        orders.open_shift(s, m1, seed["m1"])
        r = bookings.booking_options(s, m1, seed["m1"], seed["haircut"], date(2026, 11, 2))
        assert r["working"] is True and r["shift_open"] is True and r["hours"] == ["09:00", "20:00"]
        assert r["slots"][0] == "09:00" and "08:00" in r["off_hours"] and "20:00" in r["off_hours"]
        a = bookings.create_appointment(s, m1, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="Після відкриття зміни")
        r = bookings.booking_options(s, m1, seed["m1"], seed["haircut"], date(2026, 11, 2))
        assert "10:00" not in r["slots"] and a.id
        with pytest.raises(Forbidden):
            bookings.booking_options(s, m1, seed["m2"], seed["haircut"], date(2026, 11, 2))


def test_incomplete_orders_block_shift_close_and_master_fills_them(db, seed):
    m1, m2, d = seed["actors"]["m1"], seed["actors"]["m2"], seed["actors"]["director"]
    with db.session() as s:
        dye = catalog.create_service(s, d, {"name": "Фарбування", "duration_min": 60, "price": 150000, "needs_materials": True})
        mat = catalog.create_material(s, d, {"name": "Фарба", "unit_cost": 20000, "stock": 10})
        a1 = _book(s, m1, seed, 9, service=dye.id, phone="0671112233")
        a2 = _book(s, m1, seed, 11)
        ids = a1.id, a2.id, mat.id
    clock.set_now(datetime(2026, 11, 2, 13))
    with db.session() as s:
        o1 = orders.close_order(s, m1, ids[0], amount=150000, payment_method="cash")
        o2 = orders.close_order(s, m1, ids[1], amount=50000, payment_method="cash")
        assert orders.order_public(o1)["missing"] == ["materials"]
        assert orders.order_public(o2)["missing"] == ["phone"]
        assert orders.order_public(o1)["closed_by_name"] == "Олена"
        oid1, oid2 = o1.id, o2.id
    with db.session() as s:
        with pytest.raises(Conflict) as e:
            orders.close_shift(s, m1, seed["m1"])
        assert e.value.code == "incomplete_orders" and "матеріали" in e.value.message and "телефон" in e.value.message
    with db.session() as s:
        with pytest.raises(Forbidden):
            orders.set_order_phone(s, m2, oid2, "0509998877")
        o2 = orders.set_order_phone(s, m1, oid2, "0509998877")
        assert o2.client.phone == "+380509998877"
        o1 = orders.correct_order(s, m1, oid1, {"materials": [{"material_id": ids[2], "qty": 1}]}, "")
        p = orders.order_public(o1)
        assert p["missing"] == [] and p["edited_by_name"] == "Олена" and p["edited_at"] == "2026-11-02T13:00:00"
        with pytest.raises(Forbidden):
            orders.set_order_phone(s, m1, oid2, "0501110000")
        hist = orders.order_history(s, m1, o2)
        assert hist[0]["action"] == "set_phone" and hist[0]["actor"] == "Олена"
        with pytest.raises(Forbidden):
            orders.order_history(s, m2, o2)
    with db.session() as s:
        sh = orders.close_shift(s, m1, seed["m1"])
        assert sh.status != ShiftStatus.OPEN
    with db.session() as s:
        with pytest.raises(Forbidden):
            orders.correct_order(s, m1, oid1, {"materials": []}, "")
        with pytest.raises(Forbidden):
            orders.set_order_phone(s, m1, oid2, "0671112244")
        o = orders.correct_order(s, d, oid1, {"amount": 140000}, "Знижка")
        assert o.editor.full_name == "Марина Д." and o.amount == 140000


def test_set_phone_links_order_to_existing_client(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        known = catalog.get_or_create_client(s, d, "Постійна", "0631234567")
        a = _book(s, m1, seed, 9)
        kid, aid = known.id, a.id
    clock.set_now(datetime(2026, 11, 2, 11))
    with db.session() as s:
        o = orders.close_order(s, m1, aid, amount=50000, payment_method="card")
        o = orders.set_order_phone(s, m1, o.id, "063 123 45 67")
        assert o.client_id == kid and o.appointment.client_id == kid and orders.order_missing(o) == []


def test_director_can_force_close_incomplete_shift(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        aid = _book(s, m1, seed, 9).id
    clock.set_now(datetime(2026, 11, 2, 11))
    with db.session() as s:
        orders.close_order(s, m1, aid, amount=50000, payment_method="cash")
    with db.session() as s:
        with pytest.raises(Conflict):
            orders.close_shift(s, d, seed["m1"])
        assert orders.close_shift(s, d, seed["m1"], force=True).status != ShiftStatus.OPEN
