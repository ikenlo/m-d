import base64
import io
import json
import os
import time
from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from mds import clock
from mds.models import Order
from mds.services import analytics, backup, bookings, expenses, gdrive, orders, products
from mds.services.backup_crypto import decrypt_file, encrypt_file, load_key
from mds.services.gdrive import Resp

NOV = (date(2026, 11, 1), date(2026, 11, 30))


def _visit(s, seed, h, amount, price=None, master="m1", mats=None, status="completed", phone=None):
    d = seed["actors"]["director"]
    a = bookings.create_appointment(s, d, master_id=seed[master], service_id=seed["haircut"],
                                    starts_at=datetime(2026, 11, 2, h), client_name="К", client_phone=phone or f"06700000{h:02d}")
    return orders.close_order(s, d, a.id, amount=amount, price=price, payment_method="cash" if h % 2 else "card",
                              materials=mats or [], status=status)


def test_dashboard_canon_metrics(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        _visit(s, seed, 9, 50000)
        _visit(s, seed, 10, 40000, mats=[{"name": "Фарба", "qty": 1, "unit_cost": 5000}])
        _visit(s, seed, 11, 50000, status="unpaid")
        p = products.create_product(s, d, {"name": "Шампунь", "cost_price_cents": 20000, "sale_price_cents": 40000, "stock_qty": 3})
        products.sell_alone(s, d, master_id=seed["m1"], items=[{"product_id": p.id, "qty": 1}], payment_method="card")
        expenses.create_expense(s, d, category="rent", amount=30000, spent_on=date(2026, 11, 2))
        expenses.create_expense(s, d, category="equipment", amount=1000, spent_on=date(2026, 11, 2))
        r = analytics.dashboard(s, d, *NOV)
    assert r["revenue"] == 90000 and r["discounts"] == 10000 and r["masters_share"] == 50000
    assert r["salon_share"] == 40000 and r["materials"] == 5000
    assert r["products"] == {"revenue": 40000, "cost": 20000, "bonus": 4000, "margin": 16000, "lines": 1}
    assert r["gross_profit"] == 90000 - 50000 - 5000 + 16000
    assert r["expenses"]["other"] == 1000 and r["expenses"]["taxes"] == 0
    assert r["net_profit"] == r["gross_profit"] - 30000 - 1000
    assert r["real_revenue"] == 130000 and r["profitability_bp"] == r["net_profit"] * 10000 // 130000
    assert r["unpaid_orders"]["count"] == 1 and r["unpaid_orders"]["amount"] == 50000
    assert r["owed_to_masters"] == 75000 and r["product_bonus_accrued"] == 4000
    assert r["by_payment"]["cash"] == 50000 and r["by_payment"]["card"] == 40000 + 40000
    assert r["gross_details"]["materials_by_category"][0]["amount"] == 5000


def test_dashboard_equals_sum_of_transactions(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        for h in range(9, 15):
            _visit(s, seed, h, 30000 + h * 1000, price=50000)
        r = analytics.dashboard(s, d, *NOV)
        rows = s.execute(select(func.sum(Order.amount), func.sum(Order.master_earning), func.sum(Order.salon_earning))).one()
    assert (r["revenue"], r["masters_share"], r["salon_net_from_orders"]) == tuple(rows)


@pytest.mark.parametrize("bp,level", [(2600, "excellent"), (2000, "good"), (1500, "fair"), (900, "low"), (100, "critical"),
                                      (None, None)])
def test_profitability_scale(bp, level):
    assert analytics.profitability_level(bp) == level


def test_my_finance_and_logic_stats(db, seed):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    with db.session() as s:
        _visit(s, seed, 9, 50000)
        _visit(s, seed, 10, 40000)
        my = analytics.my_earnings(s, m1)
        ls = analytics.logic_stats(s, d)
    assert my["current_shift"]["earnings"] == 50000 and my["current_shift"]["orders_count"] == 2
    assert my["month_summary"]["earnings"] == 50000 and my["month_summary"]["debt"] == my["balance"]["balance"]
    assert len(my["last_7_days"]) == 7 and my["last_7_days"][-1]["earning"] == 50000
    assert my["top_services"] == [{"service": "Стрижка", "count": 2, "earnings": 50000}]
    assert len(my["history"]) == 2
    assert ls["avg_price"] == 50000 and ls["avg_paid"] == 45000 and ls["avg_master"] == 25000 and ls["avg_salon"] == 20000


def test_exports_csv_xlsx_pdf(client_api, db):
    c, seed, hd = client_api
    with db.session() as s:
        _visit(s, seed, 9, 50000)
    r = c.get("/api/exports/finance.csv?period=month", headers=hd)
    assert r.status_code == 200 and r.content.startswith("﻿".encode()) and "Чистий прибуток" in r.content.decode("utf-8-sig")
    from openpyxl import load_workbook
    r = c.get("/api/exports/orders.xlsx?period=month", headers=hd)
    ws = load_workbook(io.BytesIO(r.content)).active
    assert ws["A2"].value == "Дата" and ws.max_row == 3
    r = c.get("/api/exports/finance.pdf?period=month", headers=hd)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert c.get("/api/exports/finance.doc", headers=hd).status_code == 422
    m = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]}
    assert c.get("/api/exports/finance.csv", headers=m).status_code == 403


def test_reports_hidden_setting(client_api):
    c, seed, hd = client_api
    assert c.put("/api/settings", headers=hd, json={"reports_hidden": ["by_day", "pie"]}).status_code == 200
    assert c.put("/api/settings", headers=hd, json={"reports_hidden": ["nope"]}).status_code == 422
    dash = c.get("/api/finance/dashboard?period=month", headers=hd).json()
    assert dash["summary"]["hidden_reports"] == ["by_day", "pie"] and "by_day" in dash


def test_ai_rules_default_and_llama_fallback(client_api, db, monkeypatch):
    c, seed, hd = client_api
    with db.session() as s:
        _visit(s, seed, 9, 10000, mats=[{"name": "Фарба", "qty": 1, "unit_cost": 5000}])
    r = c.post("/api/analytics/recommend?period=month", headers=hd).json()
    assert r["provider"] == "rules" and r["cached"] is False and any(i["priority"] == "high" for i in r["items"])
    assert c.post("/api/analytics/recommend?period=month", headers=hd).json()["cached"] is True
    monkeypatch.setenv("MDS_AI_PROVIDER", "llama")
    monkeypatch.setenv("MDS_AI_LLAMA_URL", "http://127.0.0.1:9/v1")
    r = c.post("/api/analytics/recommend?period=month&refresh=true", headers=hd).json()
    assert r["provider"] == "rules" and r["items"]
    from mds.services import ai
    facts = ai.facts(__import__("mds.db").db.Database(db.url).session_factory(), seed["actors"]["director"], *NOV)
    blob = json.dumps(facts, ensure_ascii=False)
    assert "0670000009" not in blob and "К" not in blob.replace("Стрижка", "")


def test_llama_provider_parses_json(monkeypatch):
    from mds.services.ai.local_llama import LocalLlamaProvider

    class R:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    content = 'Ось: [{"priority":"high","title":"Ціни","text":"Підніміть ціну фарбування."}]'
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: R(body))
    items = LocalLlamaProvider("http://x/v1").recommend({"revenue": 1})
    assert items == [{"priority": "high", "title": "Ціни", "text": "Підніміть ціну фарбування."}]


def test_backup_encrypt_roundtrip_and_check(tmp_path, db, seed):
    key = base64.b64encode(os.urandom(32)).decode()
    url = db.url
    path = backup.make_backup(url, tmp_path / "b")
    enc = encrypt_file(path, load_key(key))
    assert enc.read_bytes()[:7] == b"MDSENC1" and b"SQLite format" not in enc.read_bytes()[:64]
    out = decrypt_file(enc, load_key(key), tmp_path / "restored.db")
    res = backup.check_backup(out)
    assert res["ok"] and res["counts"]["users"] == 3
    from cryptography.exceptions import InvalidTag
    with pytest.raises(InvalidTag):
        decrypt_file(enc, os.urandom(32), tmp_path / "bad.db")
    with pytest.raises(ValueError):
        load_key(base64.b64encode(b"short").decode())


def test_backup_retention_by_age(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    now = time.time()
    for i, age in enumerate((40, 31, 5, 0)):
        f = d / f"mds-2026110{i}-000000.db"
        f.write_bytes(b"x")
        os.utime(f, (now - age * 86400, now - age * 86400))
    assert backup.rotate(d, 30) == 2
    assert sorted(p.name for p in d.iterdir()) == ["mds-20261102-000000.db", "mds-20261103-000000.db"]


def test_backup_download_and_restore_cli(client_api, tmp_path, capsys):
    from mds.__main__ import main
    c, seed, hd = client_api
    name = c.post("/api/backups", headers=hd).json()["name"]
    r = c.get(f"/api/backups/{name}", headers=hd)
    assert r.status_code == 200 and r.content[:15] == b"SQLite format 3"
    assert c.get("/api/backups/..%2Fsecret.key", headers=hd).status_code == 404
    f = tmp_path / name
    f.write_bytes(r.content)
    main(["restore-check", str(f)])
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["ok"] is True


def test_service_account_upload_requires_key_and_encrypts(tmp_path, db, seed, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    monkeypatch.setenv("MDS_GDRIVE_SA_JSON", json.dumps({"client_email": "sa@x.iam.gserviceaccount.com", "private_key": pk}))
    monkeypatch.setenv("MDS_GDRIVE_FOLDER_ID", "shared1")
    monkeypatch.delenv("MDS_BACKUP_ENCRYPTION_KEY", raising=False)
    gdrive._sa_cache.clear()
    seen = {"put": None}

    def fake(method, url, headers=None, data=None):
        if url.startswith(gdrive.TOKEN_URL):
            assert b"jwt-bearer" in data
            return Resp(200, b'{"access_token":"sa-at","expires_in":3600}', {})
        if method == "POST" and url.startswith(gdrive.UPLOAD):
            assert json.loads(data)["parents"] == ["shared1"] and "supportsAllDrives=true" in url
            return Resp(200, b"", {"location": "https://upload-session/sa"})
        if method == "PUT":
            seen["put"] = data
            return Resp(200, b'{"id":"f","name":"x.enc"}', {})
        if method == "GET":
            return Resp(200, b'{"files":[]}', {})
        raise AssertionError(url)

    monkeypatch.setattr(gdrive, "http", fake)
    path = backup.make_backup(db.url, tmp_path / "b")
    assert gdrive.ready(tmp_path) and gdrive.sa_mode()
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        gdrive.upload(tmp_path, path)
    monkeypatch.setenv("MDS_BACKUP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    info = gdrive.upload(tmp_path, path)
    assert info["encrypted"] and seen["put"][:7] == b"MDSENC1"
    assert not list((tmp_path / "b").glob("*.enc"))
    _ = clock
