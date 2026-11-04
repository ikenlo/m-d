"""Швидкий запис без телефону (персонал) і прибирання клієнтів без номера."""

from datetime import datetime

import pytest

from mds import clock
from mds.errors import Forbidden, ValidationError
from mds.models import Appointment, Client, Order
from mds.services import bookings, catalog, orders
from mds.services.common import Actor


def test_staff_can_book_without_phone_client_cannot(db, seed):
    clock.set_now(datetime(2026, 11, 2, 8))
    adm = seed["actors"]["admin"]
    with db.session() as s:
        a = bookings.create_appointment(s, adm, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="Перехожий")
        assert a.client.phone is None and a.client.name == "Перехожий"
        b = bookings.create_appointment(s, adm, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 11), client_name="Перехожий")
        assert b.client_id != a.client_id  # без номера — окремі клієнти
        with pytest.raises(ValidationError):
            bookings.create_appointment(s, adm, master_id=seed["m1"], service_id=seed["haircut"], starts_at=datetime(2026, 11, 2, 12))
        with pytest.raises(ValidationError):  # клієнт у боті — лише з номером
            bookings.create_appointment(s, Actor.telegram(5), master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 13), client_name="Х")
    clock.set_now(None)


def test_purge_clients_without_phone_keeps_history_and_future(db, seed):
    clock.set_now(datetime(2026, 11, 2, 8))
    adm, d = seed["actors"]["admin"], seed["actors"]["director"]
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
            catalog.purge_clients_without_phone(s, adm)
        assert catalog.purge_clients_without_phone(s, d, dry_run=True) == {"deleted": 1, "merged": 1, "kept_future": 1}
        r = catalog.purge_clients_without_phone(s, d)
        assert r == {"deleted": 1, "merged": 1, "kept_future": 1}
    with db.session() as s:
        holder = s.query(Client).filter_by(name=catalog.NO_PHONE_NAME).one()
        assert s.get(Order, oid).client_id == holder.id and s.get(Appointment, ids[0]).client_id == holder.id
        assert s.get(Client, ids[1]) is not None and s.get(Client, ids[2]) is None
        assert s.query(Client).filter(Client.phone.isnot(None)).count() == 1
        # повторно — нічого (картка «Клієнти без номера» не чіпається)
        assert catalog.purge_clients_without_phone(s, d) == {"deleted": 0, "merged": 0, "kept_future": 1}
    clock.set_now(None)
