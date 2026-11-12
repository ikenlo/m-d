"""Read-only API для MDs Manager: /api/ext/v1, ключ X-API-Key (D-5)."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from mds.api.app import create_app
from mds.config import Config
from mds.services import bookings, orders, products

KEY = "manager-test-key-0123456789abcdef"
H = {"X-API-Key": KEY}
SENSITIVE = {"name", "phone", "phone2", "birthday", "telegram_user_id", "telegram_chat_id", "telegram_username",
             "instagram_user_id", "notes", "wishes", "email"}


def _app(db, tmp_path, key=KEY):
    cfg = Config(data_dir=tmp_path / "data", database_url=db.url,
                 secret_key="test-secret-key-for-unit-tests-only-0123456789", manager_api_key=key)
    return TestClient(create_app(cfg, db))


@pytest.fixture
def data(db, seed):
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        pid = products.create_product(s, d, {"name": "Шампунь", "cost_price_cents": 20000,
                                             "sale_price_cents": 45000, "stock_qty": 10}).id
        a1 = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["manicure"],
                                         starts_at=datetime(2026, 11, 2, 10), client_name="Оксана Секретна",
                                         client_phone="0671112233")
        o1 = orders.close_order(s, m1, a1.id, amount=55000, price=60000, payment_method="cash",
                                materials=[{"material_id": seed["gel"], "qty": 2}],
                                products=[{"product_id": pid, "qty": 1}])
        a2 = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"],
                                         starts_at=datetime(2026, 11, 2, 12), client_name="Інна",
                                         client_phone="0671112244")
        o2 = orders.close_order(s, m1, a2.id, amount=50000, payment_method="card")
        return {"product": pid, "o1": o1.id, "o2": o2.id,
                "o1_split": (o1.price_snapshot, o1.amount, o1.materials_cost, o1.master_earning, o1.salon_earning)}


def test_disabled_without_key(db, seed, tmp_path):
    with _app(db, tmp_path, key="") as c:
        assert c.get("/api/ext/v1/orders", headers=H).status_code == 404


def test_wrong_or_missing_key_and_jwt_not_accepted(db, seed, tmp_path):
    with _app(db, tmp_path) as c:
        assert c.get("/api/ext/v1/orders").status_code == 401
        assert c.get("/api/ext/v1/orders", headers={"X-API-Key": "wrong"}).status_code == 401
        tok = c.post("/api/auth/login", json={"username": "director", "password": "Director123"}).json()["token"]
        assert c.get("/api/ext/v1/orders", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
        # ключ Manager не відкриває звичайний API
        assert c.get("/api/products", headers=H).status_code == 401


def test_read_only(db, seed, tmp_path):
    with _app(db, tmp_path) as c:
        for path in ("orders", "stock-moves", "products", "clients", "daily", "services"):
            assert c.post(f"/api/ext/v1/{path}", headers=H, json={}).status_code == 405
            assert c.delete(f"/api/ext/v1/{path}", headers=H).status_code == 405


def test_orders(db, seed, data, tmp_path):
    with _app(db, tmp_path) as c:
        r = c.get("/api/ext/v1/orders", headers=H, params={"date_from": "2026-11-01", "date_to": "2026-11-02"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2 and [x["id"] for x in body["items"]] == [data["o1"], data["o2"]]
        o = body["items"][0]
        for k in ("price_snapshot", "materials_cost", "master_earning", "salon_earning", "created_at"):
            assert k in o
        p, a, mat, master, salon = data["o1_split"]
        assert (o["price_snapshot"], o["amount"], o["materials_cost"], o["master_earning"], o["salon_earning"]) == \
            (p, a, mat, master, salon)
        assert p == 60000 and a == 55000 and master == 30000 and o["discount"] == 5000
        assert a == master + mat + salon
        assert o["products_total"] == 45000 and o["service_name"]
        assert not SENSITIVE & set(o)
        # пагінація і фільтр
        r = c.get("/api/ext/v1/orders", headers=H, params={"date_from": "2026-11-02", "date_to": "2026-11-02",
                                                         "limit": 1, "offset": 1})
        assert r.json()["total"] == 2 and r.json()["items"][0]["id"] == data["o2"]
        assert c.get("/api/ext/v1/orders", headers=H, params={"status": "refunded", "date_from": "2026-11-02",
                                                              "date_to": "2026-11-02"}).json()["total"] == 0


def test_validation(db, seed, tmp_path):
    with _app(db, tmp_path) as c:
        assert c.get("/api/ext/v1/orders", headers=H, params={"date_from": "2026-11-05", "date_to": "2026-11-01"}).status_code == 422
        assert c.get("/api/ext/v1/orders", headers=H, params={"date_from": "2025-01-01", "date_to": "2026-11-01"}).status_code == 422
        assert c.get("/api/ext/v1/orders", headers=H, params={"limit": 5000}).status_code == 422
        assert c.get("/api/ext/v1/orders", headers=H, params={"status": "bogus"}).status_code == 422
        assert c.get("/api/ext/v1/stock-moves", headers=H, params={"item_type": "x"}).status_code == 422


def test_stock_moves_and_products(db, seed, data, tmp_path):
    with _app(db, tmp_path) as c:
        mv = c.get("/api/ext/v1/stock-moves", headers=H, params={"date_from": "2026-11-02", "date_to": "2026-11-02"}).json()
        reasons = {(x["item_type"], x["reason"]) for x in mv["items"]}
        assert ("product", "sale") in reasons and ("material", "consumption") in reasons
        sale = next(x for x in mv["items"] if x["reason"] == "sale")
        assert sale["qty"] == -1 and sale["order_id"] == data["o1"] and sale["name"] == "Шампунь"
        only = c.get("/api/ext/v1/stock-moves", headers=H, params={"item_type": "product", "date_from": "2026-11-02",
                                                                   "date_to": "2026-11-02"}).json()
        assert {x["item_type"] for x in only["items"]} == {"product"}
        pr = c.get("/api/ext/v1/products", headers=H).json()
        p = next(x for x in pr if x["id"] == data["product"])
        assert p["cost_price_cents"] == 20000 and p["sale_price_cents"] == 45000 and p["stock_qty"] == 9
        sv = c.get("/api/ext/v1/services", headers=H).json()
        assert {x["id"] for x in sv} >= {seed["manicure"], seed["haircut"]}


def test_clients_have_no_personal_data(db, seed, data, tmp_path):
    with _app(db, tmp_path) as c:
        r = c.get("/api/ext/v1/clients", headers=H)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 2
        for x in body["items"]:
            assert not SENSITIVE & set(x)
        raw = r.text
        assert "Оксана" not in raw and "Секретна" not in raw and "0671112233" not in raw and "671112233" not in raw
        visits = sorted(x["visits"] for x in body["items"])
        assert visits[-1] == 1 and sum(x["spent"] for x in body["items"]) == 105000


def test_daily(db, seed, data, tmp_path):
    with _app(db, tmp_path) as c:
        days = c.get("/api/ext/v1/daily", headers=H, params={"date_from": "2026-11-01", "date_to": "2026-11-03"}).json()
        assert [d["date"] for d in days] == ["2026-11-01", "2026-11-02", "2026-11-03"]
        d = days[1]
        assert d["orders"] == 2 and d["revenue"] == 105000 and d["price_total"] == 110000 and d["discounts"] == 5000
        assert d["cash"] == 55000 and d["noncash"] == 50000
        assert d["revenue"] == d["masters_share"] + d["materials"] + d["salon"]
        assert d["products_revenue"] == 45000 and d["products_cost"] == 20000 and d["products_bonus"] == 4500
        assert days[0]["orders"] == 0 and days[0]["revenue"] == 0
