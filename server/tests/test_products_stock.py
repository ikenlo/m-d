from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from mds import clock
from mds.errors import Conflict, Forbidden, ValidationError
from mds.models import BonusAccrual, LedgerEntry, Material, OrderStatus, Payout, Product, StockMove
from mds.services import bookings, catalog, orders, products, stock


def mk(s, seed, master="m1", service="haircut", h=10, phone="0671112233"):
    return bookings.create_appointment(s, seed["actors"]["director"], master_id=seed[master], service_id=seed[service],
                                       starts_at=datetime(2026, 11, 2, h), client_name="Клієнт", client_phone=phone)


@pytest.fixture
def shampoo(db, seed):
    with db.session() as s:
        p = products.create_product(s, seed["actors"]["director"], {"name": "Шампунь", "cost_price_cents": 20000,
                                                                     "sale_price_cents": 45000, "stock_qty": 10})
        return p.id


def test_sell_with_service_bonus_10_percent(db, seed, shampoo):
    m1 = seed["actors"]["m1"]
    with db.session() as s:
        a = mk(s, seed)
        o = orders.close_order(s, m1, a.id, amount=50000, payment_method="cash", products=[{"product_id": shampoo, "qty": 2}])
        assert len(o.products) == 1 and o.products[0].total == 90000 and o.products[0].master_bonus_cents == 9000
        assert o.master_earning == 25000
        assert s.get(Product, shampoo).stock_qty == 8
        mv = s.scalar(select(StockMove).where(StockMove.reason == "sale"))
        assert mv.item_type == "product" and mv.qty_milli == -2000 and mv.unit_price == 20000 and mv.order_id == o.id
        assert products.unpaid_bonus(s, seed["m1"]) == 9000
        r = orders.shift_report(s, orders.current_shift(s, seed["m1"]))
        assert r["products_revenue"] == 90000 and r["cash"]["from_clients"] == 140000
        assert r["master_earning"] == 25000 and r["cash"]["master_takes"] == 25000
        assert orders.master_balance(s, seed["m1"])["accrued"] == 25000


def test_price_is_fixed_and_master_only_while_shift_open(db, seed, shampoo):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        a = mk(s, seed)
        o = orders.close_order(s, m1, a.id, amount=50000, payment_method="card")
        oid = o.id
        products.update_product(s, d, shampoo, {"sale_price_cents": 50000})
        op = products.sell(s, m1, oid, shampoo, 1)
        assert op.sale_price_snapshot_cents == 50000
    with db.session() as s, pytest.raises(Forbidden):
        products.sell(s, seed["actors"]["m2"], oid, shampoo, 1)
    with db.session() as s:
        orders.close_shift(s, m1, seed["m1"])
    with db.session() as s, pytest.raises(Forbidden):
        products.sell(s, m1, oid, shampoo, 1)
    with db.session() as s:
        products.sell(s, d, oid, shampoo, 1)
    with db.session() as s, pytest.raises(ValidationError):
        products.sell(s, d, oid, shampoo, 0)


def test_product_only_order_and_refund_nets_to_zero(db, seed, shampoo):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        o = products.sell_alone(s, m1, master_id=seed["m1"], items=[{"product_id": shampoo, "qty": 1}], payment_method="card")
        assert o.appointment_id is None and o.service_id is None and o.status == OrderStatus.COMPLETED
        assert o.amount == 0 and o.master_earning == 0
        line = o.products[0].id
        pub = orders.order_public(o)
        assert pub["service_name"] == "Продаж товарів" and pub["products_total"] == 45000
    with db.session() as s, pytest.raises(Forbidden):
        products.refund_line(s, m1, line, "x")
    with db.session() as s:
        products.refund_line(s, d, line, "брак")
        assert s.get(Product, shampoo).stock_qty == 10
        assert products.unpaid_bonus(s, seed["m1"]) == 0
        lg = s.execute(select(func.sum(LedgerEntry.amount), func.sum(LedgerEntry.master_amount))
                       .where(LedgerEntry.kind.in_(("product_sale", "product_refund")))).one()
        assert tuple(lg) == (0, 0)
    with db.session() as s, pytest.raises(Conflict):
        products.refund_line(s, d, line, "ще")


def test_refund_after_bonus_paid_deducts_next_month(db, seed, shampoo):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        o = products.sell_alone(s, m1, master_id=seed["m1"], items=[{"product_id": shampoo, "qty": 2}], payment_method="cash")
        line = o.products[0].id
    with db.session() as s, pytest.raises(Forbidden):
        products.pay_bonuses(s, m1, master_id=seed["m1"])
    with db.session() as s:
        p = products.pay_bonuses(s, d, master_id=seed["m1"])
        assert p.amount == 9000 and p.tag == "product_bonus"
        assert products.pay_bonuses(s, d, master_id=seed["m1"]) is None
        assert orders.master_balance(s, seed["m1"])["paid"] == 0
    with db.session() as s:
        products.refund_line(s, d, line, "повернула")
        assert products.unpaid_bonus(s, seed["m1"]) == -9000
        o2 = products.sell_alone(s, m1, master_id=seed["m1"], items=[{"product_id": shampoo, "qty": 3}], payment_method="cash")
        assert o2.products[0].master_bonus_cents == 13500
        assert products.unpaid_bonus(s, seed["m1"]) == 4500
        assert products.pay_bonuses(s, d, master_id=seed["m1"]).amount == 4500
        assert s.scalar(select(func.count(BonusAccrual.id)).where(BonusAccrual.payout_id.is_(None))) == 0


def test_order_refund_also_refunds_products(db, seed, shampoo):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        a = mk(s, seed)
        o = orders.close_order(s, m1, a.id, amount=50000, payment_method="cash", products=[{"product_id": shampoo, "qty": 1}])
        oid = o.id
    with db.session() as s:
        orders.refund_order(s, d, oid, "клієнт повернувся")
        assert s.get(Product, shampoo).stock_qty == 10 and products.unpaid_bonus(s, seed["m1"]) == 0


def test_idempotent_close_does_not_double_sell(db, seed, shampoo):
    m1 = seed["actors"]["m1"]
    with db.session() as s:
        a = mk(s, seed)
        aid = a.id
    for _ in range(2):
        with db.session() as s:
            orders.close_order(s, m1, aid, amount=50000, payment_method="cash", idempotency_key="k",
                               products=[{"product_id": shampoo, "qty": 1}])
    with db.session() as s:
        assert s.get(Product, shampoo).stock_qty == 9


def test_average_cost_on_receipt(db, seed):
    d = seed["actors"]["director"]
    assert stock.average_pack_cost(0, 0, 60000, 32000, 60) == 32000
    assert stock.average_pack_cost(60000, 32000, 60000, 40000, 60) == 36000
    with db.session() as s:
        m = catalog.create_material(s, d, {"name": "Фарба", "unit": "г", "pack_name": "туба", "pack_size": 60, "unit_cost": 32000})
        stock.receive(s, d, m.id, 60, cost=32000)
        stock.receive(s, d, m.id, 60, cost=40000)
        assert s.get(Material, m.id).unit_cost == 36000
        mvs = stock.moves(s, d, material_id=m.id)
        assert {x["reason"] for x in mvs} == {"receipt"} and mvs[0]["unit_price"] == 36000
    with db.session() as s:
        products.create_product(s, d, {"name": "Маска", "cost_price_cents": 10000, "sale_price_cents": 25000})
        pid = s.scalar(select(Product.id).where(Product.name == "Маска"))
        products.receive(s, d, pid, 2, cost=20000)
        products.receive(s, d, pid, 2, cost=30000)
        assert s.get(Product, pid).cost_price_cents == 12500


def test_markup_is_helper_not_part_of_split(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        m = catalog.create_material(s, d, {"name": "Фольга", "unit_cost": 1000, "markup_bp": 15000})
        assert stock.material_public(m)["suggested_price"] == 2500
        with pytest.raises(ValidationError):
            catalog.update_material(s, d, m.id, {"markup_bp": -1})
        p = products.create_product(s, d, {"name": "Крем", "cost_price_cents": 10000})
        assert p.sale_price_cents == 20000


def test_inventory_endpoint_and_stock_reasons(client_api):
    c, seed, hd = client_api
    pid = c.post("/api/products", headers=hd, json={"name": "Лак", "cost_price_cents": 5000, "sale_price_cents": 12000,
                                                     "stock_qty": 5}).json()["id"]
    r = c.post("/api/stock/inventory", headers=hd, json={"items": [{"product_id": pid, "actual": 3},
                                                                   {"material_id": seed["gel"], "actual": 2}], "note": "облік"})
    assert r.status_code == 200 and r.json()[0]["diff"] == -2
    reasons = {m["reason"] for m in c.get("/api/stock/moves", headers=hd).json()}
    assert {"receipt", "inventory"} <= reasons
    assert c.post("/api/stock/inventory", headers=hd, json={"items": [{"actual": 1}]}).status_code == 422
    m = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]}
    assert c.post("/api/stock/inventory", headers=m, json={"items": [{"product_id": pid, "actual": 1}]}).status_code == 403
    assert c.post("/api/products", headers=m, json={"name": "x"}).status_code == 403
    r = c.post("/api/orders/products-only", headers=m, json={"master_id": seed["m1"], "payment_method": "cash",
                                                              "items": [{"product_id": pid, "qty": 1}]})
    assert r.status_code == 201 and r.json()["products_total"] == 12000
    assert c.get("/api/bonuses/my", headers=m).json()["unpaid"] == 1200


def test_force_close_requires_materials_but_not_phone(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        catalog.update_service(s, d, seed["haircut"], {"needs_materials": True})
        a = mk(s, seed)
        o = orders.close_order(s, m1, a.id, amount=50000, payment_method="cash")
        assert o.materials_not_entered
        assert [x.id for x in orders.list_orders(s, d, materials_not_entered=True)] == [o.id]
        oid = o.id
    with db.session() as s, pytest.raises(Conflict) as e:
        orders.close_shift(s, d, seed["m1"], force=True)
    assert e.value.code == "materials_required"
    with db.session() as s:
        orders.correct_order(s, d, oid, {"materials": [{"material_id": seed["gel"], "qty": 1}]}, "внесено директором")
        assert not orders.list_orders(s, d, materials_not_entered=True)
        b = mk(s, seed, h=12, phone="0501112233")
        b.client.phone = None
        orders.close_order(s, m1, b.id, amount=50000, payment_method="cash",
                           materials=[{"material_id": seed["gel"], "qty": 1}])
    with db.session() as s, pytest.raises(Conflict):
        orders.close_shift(s, m1, seed["m1"])
    with db.session() as s:
        assert orders.close_shift(s, d, seed["m1"], force=True).status in ("closed", "paid")
    clock.set_now(datetime(2026, 11, 2, 8, 0))


def test_payout_bonus_not_counted_in_service_balance(db, seed, shampoo):
    d = seed["actors"]["director"]
    with db.session() as s:
        products.sell_alone(s, d, master_id=seed["m1"], items=[{"product_id": shampoo, "qty": 1}], payment_method="cash")
        products.pay_bonuses(s, d, master_id=seed["m1"], until=date(2026, 11, 30))
        assert s.scalar(select(func.count(Payout.id))) == 1
        assert orders.master_balance(s, seed["m1"]) == {"accrued": 0, "paid": 0, "balance": 0}
