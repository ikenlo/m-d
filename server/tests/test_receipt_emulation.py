"""Емулятори Checkbox і чекового принтера: Connect → чек → ESC/POS → принтер розбирає той самий текст."""
import base64
import importlib.util
import threading
from datetime import datetime
from pathlib import Path

import pytest

from mds import clock
from mds.config import Config
from mds.services import bookings, checkbox, orders, products

ROOT = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fake_printer = _load("fake_printer")
fake_checkbox = _load("fake_checkbox")


@pytest.mark.parametrize("cp,expected", [
    ("cp1251", "ФІСКАЛЬНИЙ ЧЕК\nСтрижка жіноча — Їжак ґудзик є\nСУМА 450.00 грн"),
    ("cp866", "ФIСКАЛЬНИЙ ЧЕК\nСтрижка жiноча - Їжак гудзик є\nСУМА 450.00 грн"),
])
def test_escpos_roundtrip_through_printer_parser(cp, expected):
    text = "ФІСКАЛЬНИЙ ЧЕК\nСтрижка жіноча — Їжак ґудзик є\nСУМА 450.00 ₴\n"
    p = fake_printer.parse(checkbox.escpos(text, codepage=cp))
    assert p["text"].rstrip() == expected and p["codepage"] == cp and p["reset"] and p["cut"]
    assert "?" not in p["text"]


def test_full_chain_with_checkbox_emulator(db, seed, tmp_path):
    srv = fake_checkbox.make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = Config(data_dir=tmp_path / "d", database_url=db.url, secret_key="x" * 40,
                 checkbox_api_url=f"http://127.0.0.1:{srv.server_address[1]}",
                 checkbox_login="emu", checkbox_password="emu", checkbox_license_key="emu")
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    try:
        clock.set_now(datetime(2026, 11, 5, 9))
        with db.session() as s:
            checkbox.update_settings(s, d, cfg, {"enabled": True})
            pid = products.create_product(s, d, {"name": "Шампунь відновлювальний", "cost_price_cents": 20000,
                                                 "sale_price_cents": 45000, "stock_qty": 5}).id
            a = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"],
                                            starts_at=datetime(2026, 11, 5, 10), client_name="Оля", client_phone="0671112233")
        with db.session() as s:
            oid = orders.close_order(s, m1, a.id, amount=50000, payment_method="cash", products=[{"product_id": pid, "qty": 1}]).id
        code, out = checkbox.fiscalize(db, cfg, m1, oid)
        assert code == 200 and out["status"] == "done" and out["fiscal_code"] == "EMU0000001"
        p = fake_printer.parse(base64.b64decode(out["print_base64"]))
        assert "Шампунь відновлювальний" in p["text"] and "СУМА" in p["text"] and "950.00" in p["text"]
        assert "ГОТІВКА" in p["text"] and "ІД 3456789012" in p["text"] and "?" not in p["text"]
        assert p["cut"] and p["codepage"] == "cp1251"
        assert all(len(x) <= 32 for x in p["text"].splitlines())
    finally:
        clock.set_now(None)
        srv.shutdown()
        srv.server_close()
