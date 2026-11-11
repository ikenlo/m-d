from datetime import datetime

import pytest

from mds import clock
from mds.errors import Forbidden, ValidationError
from mds.models import Appointment, Client, Order
from mds.services import bookings, catalog, orders
from mds.services.common import Actor


def test_staff_can_book_without_phone_client_cannot(db, seed):
    clock.set_now(datetime(2026, 11, 2, 8))
    adm = seed["actors"]["director"]
    with db.session() as s:
        a = bookings.create_appointment(s, adm, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="Перехожий")
        assert a.client.phone is None and a.client.name == "Перехожий"
        b = bookings.create_appointment(s, adm, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 11), client_name="Перехожий")
        assert b.client_id != a.client_id
        with pytest.raises(ValidationError):
            bookings.create_appointment(s, adm, master_id=seed["m1"], service_id=seed["haircut"], starts_at=datetime(2026, 11, 2, 12))
        with pytest.raises(ValidationError):
            bookings.create_appointment(s, Actor.telegram(5), master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 13), client_name="Х")
    clock.set_now(None)


def test_purge_clients_without_phone_keeps_history_and_future(db, seed):
    clock.set_now(datetime(2026, 11, 2, 8))
    adm, d = seed["actors"]["director"], seed["actors"]["director"]
    with db.session() as s:
        past = bookings.create_appointment(s, adm, master_id=seed["m1"], service_id=seed["haircut"],
                                           starts_at=datetime(2026, 11, 2, 9), client_name="Був")
        future = bookings.create_appointment(s, adm, master_id=seed["m2"], service_id=seed["haircut"],
                                             starts_at=datetime(2026, 11, 3, 10), client_name="Прийде")
        lonely = catalog.get_or_create_client(s, adm, "Без історії", None)
        catalog.get_or_create_client(s, adm, "З номером", "0671112233")
        ids = (past.id, future.client_id, lonely.id)
    clock.set_now(datetime(2026, 11, 2, 10))
    with db.session() as s:
        o = orders.close_order(s, seed["actors"]["m1"], ids[0], amount=50000, payment_method="cash")
        oid = o.id
    with db.session() as s:
        with pytest.raises(Forbidden):
            catalog.purge_clients_without_phone(s, seed["actors"]["m1"])
        assert catalog.purge_clients_without_phone(s, d, dry_run=True) == {"deleted": 1, "merged": 1, "kept_future": 1}
        r = catalog.purge_clients_without_phone(s, d)
        assert r == {"deleted": 1, "merged": 1, "kept_future": 1}
    with db.session() as s:
        holder = s.query(Client).filter_by(name=catalog.NO_PHONE_NAME).one()
        assert s.get(Order, oid).client_id == holder.id and s.get(Appointment, ids[0]).client_id == holder.id
        assert s.get(Client, ids[1]) is not None and s.get(Client, ids[2]) is None
        assert s.query(Client).filter(Client.phone.isnot(None)).count() == 1
        assert catalog.purge_clients_without_phone(s, d) == {"deleted": 0, "merged": 0, "kept_future": 1}
    clock.set_now(None)


def test_coloring_without_materials_flagged_and_master_adds_materials(db, seed):
    from mds.services import stock
    from mds.services.orders import needing_materials, order_public

    d, m1, m2 = seed["actors"]["director"], seed["actors"]["m1"], seed["actors"]["m2"]
    clock.set_now(datetime(2026, 11, 2, 8))
    with db.session() as s:
        svc = catalog.create_service(s, d, {"name": "Фарбування в один тон", "duration_min": 60, "price": 150000, "needs_materials": True})
        dye = catalog.create_material(s, d, {"name": "Igora", "tone": "7-1", "unit": "г", "pack_name": "туба", "pack_size": 60,
                                             "unit_cost": 30000, "stock": 120})
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=svc.id,
                                        starts_at=datetime(2026, 11, 2, 9), client_name="К", client_phone="0671239999")
        aid, dye_id = a.id, dye.id
    clock.set_now(datetime(2026, 11, 2, 10, 5))
    with db.session() as s:
        o = orders.close_order(s, m1, aid, amount=150000, payment_method="cash")
        assert order_public(o)["needs_materials"] is True
        assert [x.id for x in needing_materials(s, m1)] == [o.id] and needing_materials(s, m2) == []
        with pytest.raises(Forbidden):
            orders.correct_order(s, m2, o.id, {"materials": [{"material_id": dye_id, "qty": 60}]}, "")
        with pytest.raises(Forbidden):
            orders.correct_order(s, m1, o.id, {"amount": 1}, "x")
        orders.correct_order(s, m1, o.id, {"materials": [{"material_id": dye_id, "qty": 60}]}, "")
        assert order_public(o)["needs_materials"] is False and o.materials_cost == 30000
        assert stock.material_public(s.get(type(dye), dye_id))["stock"] == 60
        assert needing_materials(s, m1) == []
    clock.set_now(None)
