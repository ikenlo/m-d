from datetime import date, datetime

import pytest

from mds.errors import Forbidden, ValidationError
from mds.models import Expense, Material, StockMove
from mds.services import bookings, catalog, orders, schedule, stock

DAY = date(2026, 11, 2)  # понеділок


def mk_dye(s, d, tone="7-1", stock_g=120):
    # фарба: туба 60 г за 300 грн; облік у грамах
    return catalog.create_material(s, d, {"name": "Igora Royal", "tone": tone, "category": "Фарби", "unit": "г",
                                          "pack_name": "туба", "pack_size": 60, "unit_cost": 30000,
                                          "min_stock": 60, "stock": stock_g})


def test_material_pack_cost_and_initial_stock(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        m = mk_dye(s, d)
        p = stock.material_public(m)
        assert p["display_name"] == "Igora Royal 7-1"
        assert p["stock"] == 120 and p["stock_packs"] == 2 and not p["low"]
        assert stock.line_cost(m, 30_000) == 15000  # 30 г = пів туби = 150 грн
        assert stock.line_cost(m, 60_000) == 30000


def test_closing_coloring_writes_off_stock_by_tone(db, seed):
    d, adm, m1 = seed["actors"]["director"], seed["actors"]["admin"], seed["actors"]["m1"]
    with db.session() as s:
        t71 = mk_dye(s, d, "7-1")
        t80 = mk_dye(s, d, "8-0", stock_g=60)
        a = bookings.create_appointment(s, adm, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="К", client_phone="0500000000")
        # ціла туба 7-1 + 30 г 8-0
        o = orders.close_order(s, m1, a.id, amount=200000, payment_method="card",
                               materials=[{"material_id": t71.id, "qty": 60}, {"material_id": t80.id, "qty": 30}])
        assert o.materials_cost == 30000 + 15000
        assert [x.name for x in o.materials] == ["Igora Royal 7-1", "Igora Royal 8-0"]
    with db.session() as s:
        a71, a80 = s.get(Material, t71.id), s.get(Material, t80.id)
        assert a71.stock_milli == 60_000 and a80.stock_milli == 30_000
        assert stock.material_public(a80)["low"] is True  # 30 г ≤ мінімум 60 г
        assert s.query(StockMove).filter_by(kind="order").count() == 2


def test_order_correction_returns_and_rewrites_stock(db, seed):
    d, adm, m1 = seed["actors"]["director"], seed["actors"]["admin"], seed["actors"]["m1"]
    with db.session() as s:
        t = mk_dye(s, d)
        a = bookings.create_appointment(s, adm, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="К", client_phone="0500000000")
        o = orders.close_order(s, m1, a.id, amount=100000, payment_method="cash", materials=[{"material_id": t.id, "qty": 60}])
        orders.correct_order(s, d, o.id, {"materials": [{"material_id": t.id, "qty": 20}]}, "Насправді 20 г")
    with db.session() as s:
        assert s.get(Material, t.id).stock_milli == 100_000
        kinds = [mv.kind for mv in s.query(StockMove).order_by(StockMove.id)]
        assert kinds == ["receipt", "order", "order_return", "order"]


def test_receive_adjust_writeoff_and_expense(db, seed):
    d, m1, adm = seed["actors"]["director"], seed["actors"]["m1"], seed["actors"]["admin"]
    with db.session() as s:
        t = mk_dye(s, d, stock_g=0)
        with pytest.raises(Forbidden):
            stock.receive(s, m1, t.id, 60)
        stock.receive(s, adm, t.id, 180)  # адміністратор може приймати товар
        stock.receive(s, d, t.id, 60, cost=28000, as_expense=True, note="Накладна 15")
        assert s.get(Material, t.id).stock_milli == 240_000
        e = s.query(Expense).one()
        assert e.category == "materials" and e.amount == 28000
        stock.adjust(s, d, t.id, 230, "Інвентаризація")
        with pytest.raises(ValidationError):
            stock.writeoff(s, d, t.id, 10, "")
        stock.writeoff(s, d, t.id, 10, "Зіпсована туба")
        assert s.get(Material, t.id).stock_milli == 220_000
        assert [x["kind"] for x in stock.moves(s, d, t.id)][:2] == ["writeoff", "adjust"]


def test_schedule_day_overrides_template(db, seed):
    d, adm = seed["actors"]["director"], seed["actors"]["admin"]
    tue = date(2026, 11, 3)
    with db.session() as s:
        # вівторок за шаблоном салону 09–20; робимо 12–16, а понеділок — вихідний
        schedule.set_day(s, adm, seed["m1"], tue, start="12:00", end="16:00")
        schedule.set_day(s, d, seed["m1"], DAY, off=True)
        with pytest.raises(ValidationError):
            schedule.set_day(s, d, seed["m1"], tue, start="18:00", end="10:00")
        with pytest.raises(Forbidden):
            schedule.set_day(s, seed["actors"]["m1"], seed["m1"], tue, off=True)
    with db.session() as s:
        assert bookings.free_slots(s, seed["m1"], seed["haircut"], DAY) == []
        times = [t.strftime("%H:%M") for t in bookings.free_slots(s, seed["m1"], seed["haircut"], tue)]
        assert times[0] == "12:00" and times[-1] == "15:00"
        g = schedule.grid(s, d, DAY, date(2026, 11, 4))
        cells = g["cells"][seed["m1"]]
        assert cells["2026-11-02"]["off"] and cells["2026-11-02"]["source"] == "day"
        assert cells["2026-11-03"]["start"] == "12:00"
        assert cells["2026-11-04"] == {"start": "09:00", "end": "20:00", "off": False, "source": "salon",
                                       "template": ["09:00", "20:00"]}
        # вихідний на дату: шаблон дня лишається робочим (для перемикача в сітці)
        assert cells["2026-11-02"]["template"] == ["09:00", "20:00"]
        assert g["masters"][0]["usual_hours"] == ["09:00", "20:00"]
        # майстер бачить лише свій рядок
        assert [m["id"] for m in schedule.grid(s, seed["actors"]["m2"], DAY, DAY)["masters"]] == [seed["m2"]]
        schedule.reset_day(s, d, seed["m1"], DAY)
    with db.session() as s:
        assert bookings.free_slots(s, seed["m1"], seed["haircut"], DAY)


def test_copy_week(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        schedule.set_day(s, d, seed["m1"], date(2026, 11, 3), off=True)
        n = schedule.copy_week(s, d, seed["m1"], DAY, weeks=2)
        assert n == 14
        g = schedule.grid(s, d, date(2026, 11, 9), date(2026, 11, 17))["cells"][seed["m1"]]
        assert g["2026-11-10"]["off"] and g["2026-11-17"]["off"] and not g["2026-11-09"]["off"]


def test_stock_and_schedule_api(client_api):
    c, ids, d = client_api
    r = c.post("/api/materials", headers=d, json={"name": "Wella Koleston", "tone": "9/1", "unit": "г", "pack_name": "туба",
                                                  "pack_size": 60, "unit_cost": 32000, "stock": 60, "min_stock": 30})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert c.post(f"/api/stock/{mid}/receive", headers=d, json={"qty": 120}).json()["stock_packs"] == 3
    assert c.post(f"/api/stock/{mid}/adjust", headers=d, json={"actual": 150}).json()["stock"] == 150
    assert c.get("/api/stock/moves", headers=d).json()[0]["kind"] == "adjust"
    r = c.put(f"/api/schedule/{ids['m1']}/2026-11-05", headers=d, json={"start": "10:00", "end": "14:00"})
    assert r.status_code == 200 and r.json()["start"] == "10:00"
    g = c.get("/api/schedule?date_from=2026-11-02&date_to=2026-11-08", headers=d).json()
    assert len(g["days"]) == 7 and g["cells"][str(ids["m1"])]["2026-11-05"]["source"] == "day"
    assert c.delete(f"/api/schedule/{ids['m1']}/2026-11-05", headers=d).json() == {"ok": True}


def test_material_validation_no_server_errors(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        m = catalog.create_material(s, d, {"name": "Igora Royal", "tone": "6-0", "unit": "г", "pack_name": "туба",
                                           "pack_size": 60})
        assert m.unit_cost == 0  # ціну можна вказати пізніше
        for bad in ({"name": ""}, {"unit": ""}, {"pack_name": " "}, {"min_stock": -1}):
            with pytest.raises(ValidationError):
                catalog.update_material(s, d, m.id, bad)
        for q in (1e300, float("inf"), "abc"):
            with pytest.raises(ValidationError):
                stock.receive(s, d, m.id, q)
        with pytest.raises(ValidationError):
            stock.adjust(s, d, m.id, 1e300)
        stock.receive(s, d, m.id, "1,5")
        assert m.stock_milli == 1500


def test_material_units_packs_and_unit_change_guard(db, seed):
    """Одиниці лише г/мл/шт; упаковка за замовчуванням; одиницю не можна змінити, коли є залишок або рух."""
    d = seed["actors"]["director"]
    with db.session() as s:
        with pytest.raises(ValidationError):
            catalog.create_material(s, d, {"name": "Фарба", "unit": "кг"})
        a = catalog.create_material(s, d, {"name": "Шампунь", "unit": "мл", "pack_size": 1000, "unit_cost": 85000})
        assert a.pack_name == "упаковка"
        b = catalog.create_material(s, d, {"name": "Фольга", "unit": "шт", "pack_name": "рулон", "unit_cost": 42000})
        assert (b.pack_name, b.pack_size) == ("рулон", 1)
        c = catalog.create_material(s, d, {"name": "Рукавички", "unit": "шт", "pack_size": 100})
        assert c.pack_name == "упаковка"
        # без руху — одиницю змінити можна
        catalog.update_material(s, d, a.id, {"unit": "г"})
        stock.receive(s, d, a.id, "500")
        with pytest.raises(ValidationError):
            catalog.update_material(s, d, a.id, {"unit": "мл"})
        catalog.update_material(s, d, a.id, {"pack_name": "флакон", "pack_size": 500})  # упаковку — можна
        assert stock.material_public(a)["stock_packs"] == 1.0


def test_auto_writeoff_of_tiny_remainder(db, seed):
    """Лишилось менше 0,1 упаковки (напр. 0,09 туби) — залишок списується автоматично; виправлення замовлення повертає його."""
    from mds.services.common import update_settings

    d, adm, m1 = seed["actors"]["director"], seed["actors"]["admin"], seed["actors"]["m1"]
    with db.session() as s:
        t = mk_dye(s, d, stock_g=125)  # туба 60 г; 125 г
        a = bookings.create_appointment(s, adm, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="К", client_phone="0500000000")
        # 125 − 120 = 5 г = 0,083 туби < 0,1 → списується автоматично
        o = orders.close_order(s, m1, a.id, amount=100000, payment_method="cash", materials=[{"material_id": t.id, "qty": 120}])
    with db.session() as s:
        assert s.get(Material, t.id).stock_milli == 0
        auto = s.query(StockMove).filter_by(kind="writeoff").one()
        assert auto.qty_milli == -5000 and auto.order_id == o.id and "Автоматичне списання" in auto.note
        assert auto.cost == 2500  # 5 г × 300 ₴ / 60 г
    with db.session() as s:
        # виправили: насправді 60 г → повертаються 120 г і 5 г крихти, списується 60 г; лишається 65 г
        orders.correct_order(s, d, o.id, {"materials": [{"material_id": t.id, "qty": 60}]}, "Насправді одна туба")
    with db.session() as s:
        assert s.get(Material, t.id).stock_milli == 65_000
        orders.correct_order(s, d, o.id, {"materials": [{"material_id": t.id, "qty": 61}]}, "61 г")
    with db.session() as s:
        assert s.get(Material, t.id).stock_milli == 64_000  # крихту не повернуто вдруге
        # ручне списання до 6 г (0,1 туби) — не крихта; до 5 г — списується
        stock.writeoff(s, d, t.id, "58", "зіпсовано")
        assert s.get(Material, t.id).stock_milli == 6_000
        stock.writeoff(s, d, t.id, "1", "зіпсовано")
        assert s.get(Material, t.id).stock_milli == 0
        update_settings(s, d, {"stock_auto_writeoff_milli": 0})  # вимкнено
        stock.receive(s, d, t.id, "10")
        stock.writeoff(s, d, t.id, "8", "x")
        assert s.get(Material, t.id).stock_milli == 2_000


def test_schedule_even_odd_days(db, seed):
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        n = schedule.parity_pattern(s, d, seed["m1"], "even", date(2026, 11, 1), date(2026, 11, 10), "10:00", "19:00")
        assert n == 10
        g = schedule.grid(s, d, date(2026, 11, 1), date(2026, 11, 10))["cells"][seed["m1"]]
        assert [k[-2:] for k, c in sorted(g.items()) if not c["off"]] == ["02", "04", "06", "08", "10"]
        assert g["2026-11-04"]["start"] == "10:00"
        schedule.parity_pattern(s, d, seed["m1"], "odd", date(2026, 11, 1), date(2026, 11, 4))
        g = schedule.grid(s, d, date(2026, 11, 1), date(2026, 11, 4))["cells"][seed["m1"]]
        assert [k[-2:] for k, c in sorted(g.items()) if not c["off"]] == ["01", "03"]
        with pytest.raises(Forbidden):
            schedule.parity_pattern(s, m1, seed["m1"], "odd", date(2026, 11, 1), date(2026, 11, 4))
        with pytest.raises(ValidationError):
            schedule.parity_pattern(s, d, seed["m1"], "x", date(2026, 11, 1), date(2026, 11, 4))
