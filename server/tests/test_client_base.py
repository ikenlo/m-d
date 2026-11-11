from datetime import date, datetime

import pytest

from mds import clock
from mds.errors import Conflict, Forbidden, NotFound, ValidationError
from mds.models import Appointment, Client, Order
from mds.services import bookings, catalog, client_base as cb, orders
from mds.services.common import update_settings


def _visit(db, seed, day, hour, phone, name="Олена Коваль", master="m1", amount=50000):
    clock.set_now(datetime(2026, day[0], day[1], 7))
    with db.session() as s:
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed[master], service_id=seed["haircut"],
                                        starts_at=datetime(2026, day[0], day[1], hour), client_name=name, client_phone=phone)
        aid = a.id
    clock.set_now(datetime(2026, day[0], day[1], hour + 1))
    with db.session() as s:
        orders.close_order(s, seed["actors"][master], aid, amount=amount, payment_method="cash")
    return aid


def test_stats_segments_and_card(db, seed):
    for i, (m, d) in enumerate([(8, 1), (8, 31), (9, 30)]):
        _visit(db, seed, (m, d), 10, "0671110001", amount=50000 + i * 10000)
    clock.set_now(datetime(2026, 10, 5, 12))
    d = seed["actors"]["director"]
    with db.session() as s:
        cid = s.query(Client).filter_by(phone="+380671110001").one().id
        a = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m2"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 10, 10, 10), client_id=cid)
        bookings.set_status(s, d, a.id, "cancelled")
        c = cb.card(s, d, cid)
        assert (c["visits"], c["spent"], c["average_check"]) == (3, 180000, 60000)
        assert c["first_visit"][:10] == "2026-08-01" and c["last_visit"][:10] == "2026-09-30"
        assert c["avg_interval_days"] == 30 and c["cancelled"] == 1 and c["no_show"] == 0
        assert c["segment"] == "regular" and c["top_services"][0]["count"] == 3 and c["top_masters"][0]["id"] == seed["m1"]
        assert c["finance"]["spent"] == 180000
        update_settings(s, d, {"client_seg_vip_spent": 150000})
        assert cb.card(s, d, cid)["segment"] == "vip"
    clock.set_now(datetime(2026, 11, 20, 12))
    with db.session() as s:
        assert cb.card(s, d, cid)["segment"] == "lapsed"
        lst = cb.list_clients(s, d, segment_key="lapsed")
        assert [r["id"] for r in lst["items"]] == [cid] and lst["segments"]["lapsed"] == 1
        mc = cb.card(s, seed["actors"]["m1"], cid)
        assert "finance" not in mc and "spent" not in mc and mc["visits"] == 3
        with pytest.raises(Forbidden):
            cb.list_clients(s, seed["actors"]["m1"])
    with db.session() as s:
        with pytest.raises(NotFound):
            cb.card(s, seed["actors"]["m2"], _other_client(s, seed))
    clock.set_now(None)


def _other_client(s, seed):
    c = catalog.get_or_create_client(s, seed["actors"]["director"], "Без візитів до Ірини", "0670009999")
    s.flush()
    return c.id


def test_search_cyrillic_lookup_and_no_duplicates(db, seed):
    adm, d = seed["actors"]["director"], seed["actors"]["director"]
    clock.set_now(datetime(2026, 11, 2, 8))
    with db.session() as s:
        c = catalog.get_or_create_client(s, adm, "Ірина Шевченко", "067 222 33 44")
        cb.update_card(s, adm, c.id, {"phone2": "0503334455", "birthday": date(1990, 11, 4), "source": "instagram",
                                      "marketing_consent": True, "telegram_username": "@ira"})
        assert [r["id"] for r in cb.list_clients(s, adm, q="шевч")["items"]] == [c.id]
        assert [r["id"] for r in cb.list_clients(s, adm, q="33344")["items"]] == [c.id]
        assert cb.find_by_phone(s, adm, "+380503334455").id == c.id
        again = catalog.get_or_create_client(s, adm, "Іра", "0503334455")
        assert again.id == c.id and s.query(Client).count() == 1
        other = catalog.get_or_create_client(s, adm, "Хтось", "0661112233")
        with pytest.raises(Conflict):
            cb.update_card(s, adm, other.id, {"phone2": "0672223344"})
        card = cb.card(s, d, c.id)
        assert card["telegram_username"] == "ira" and card["birthday"] == "1990-11-04" and card["marketing_consent"] is True
        assert [r["id"] for r in cb.list_clients(s, adm, birthdays_days=7)["items"]] == [c.id]
    clock.set_now(None)


def test_merge_duplicates_and_anonymize(db, seed):
    a1 = _visit(db, seed, (10, 1), 10, "0671119991", name="Марія Іванова", amount=40000)
    _visit(db, seed, (10, 2), 10, "0671119992", name="марія  іванова", amount=60000)
    clock.set_now(datetime(2026, 10, 3, 9))
    d = seed["actors"]["director"]
    with db.session() as s:
        keep = s.get(Appointment, a1).client_id
        dup = s.query(Client).filter_by(phone="+380671119992").one().id
        groups = cb.duplicates(s, d)
        assert any({x["id"] for x in g} == {keep, dup} for g in groups)
        with pytest.raises(Forbidden):
            cb.merge(s, seed["actors"]["m1"], keep, dup)
        m = cb.merge(s, d, keep, dup)
        assert m.phone2 == "+380671119992" and s.get(Client, dup) is None
        assert s.query(Order).filter_by(client_id=keep).count() == 2
        assert cb.card(s, d, keep)["spent"] == 100000
        with pytest.raises(ValidationError):
            cb.anonymize(s, d, keep, "")
        z = cb.anonymize(s, d, keep, "Запит клієнта")
        assert z.phone is None and z.phone2 is None and z.name.startswith("Знеособлений")
        assert cb.card(s, d, keep)["spent"] == 100000
    clock.set_now(None)


def test_analytics_and_export(db, seed):
    _visit(db, seed, (10, 1), 10, "0671000001", amount=50000)
    _visit(db, seed, (10, 15), 10, "0671000001", amount=70000)
    _visit(db, seed, (10, 20), 10, "0671000002", name="Новий Клієнт", amount=30000)
    clock.set_now(datetime(2026, 10, 31, 20))
    d = seed["actors"]["director"]
    with db.session() as s:
        r = cb.analytics(s, d, date(2026, 10, 10), date(2026, 10, 31))
        assert (r["visits"], r["active_clients"], r["new_clients"], r["repeat_visits"]) == (2, 2, 1, 1)
        assert r["revenue"] == 100000 and r["average_check"] == 50000 and r["repeat_share_bp"] == 5000
        assert r["top_clients"][0]["spent"] == 70000
        with pytest.raises(Forbidden):
            cb.analytics(s, seed["actors"]["m1"], date(2026, 10, 1), date(2026, 10, 31))
        csv_text = cb.export_csv(s, d)
        assert "Новий Клієнт" in csv_text and ";1200.0;600.0;" in csv_text
    clock.set_now(None)
