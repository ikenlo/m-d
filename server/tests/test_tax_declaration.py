import io
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest
from lxml import etree

from mds import clock
from mds.errors import Forbidden, ValidationError
from mds.services import bookings, orders, tax_declaration
from mds.services.common import update_settings

XSD = Path(__file__).parent / "fixtures" / "dps"
PROFILE = {
    "fop_tin": "1234567890", "fop_name": "Тестова Маргарита Іванівна", "fop_address": "м. Київ, вул. Тестова, 1",
    "fop_sti_name": "ГУ ДПС у м. Києві", "fop_c_reg": 26, "fop_c_raj": 50, "fop_c_sti": 2650,
    "fop_single_tax_month": 33280, "fop_min_wage": 864700, "fop_email": "salon@example.com", "fop_phone": "+380671234567",
}


def _schema(name):
    return etree.XMLSchema(etree.parse(str(XSD / name)))


def _visit(db, seed, at, amount, pay="cash", phone="0670000001"):
    clock.set_now(at.replace(hour=7))
    with db.session() as s:
        aid = bookings.create_appointment(s, seed["actors"]["director"], master_id=seed["m1"], service_id=seed["haircut"],
                                          starts_at=at, client_name="К", client_phone=phone).id
    clock.set_now(at.replace(hour=at.hour + 1))
    with db.session() as s:
        orders.close_order(s, seed["actors"]["m1"], aid, amount=amount, payment_method=pay)
    clock.set_now(None)


@pytest.fixture
def profile(db, seed):
    with db.session() as s:
        update_settings(s, seed["actors"]["director"], PROFILE)
    _visit(db, seed, datetime(2026, 2, 10, 10), 50000)
    _visit(db, seed, datetime(2026, 7, 1, 12), 65050, "card", phone="0670000002")
    _visit(db, seed, datetime(2025, 12, 30, 10), 99900, phone="0670000003")


def test_data_exact_sums(db, seed, profile):
    with db.session() as s:
        d = tax_declaration.declaration_data(s, seed["actors"]["director"], 2026)
    assert d["ready"] and d["income"] == 115050
    assert d["single_tax_by_quarter"] == {1: 99840, 2: 99840, 3: 99840, 4: 99840}
    assert d["military_levy"] == 86470 * 12
    assert (d["esv_month"], d["esv_year"]) == (190234, 2282808)


def test_xml_valid_against_official_xsd(db, seed, profile):
    with db.session() as s:
        files = tax_declaration.declaration_files(s, seed["actors"]["director"], 2026, date(2027, 2, 10))
    (main_name, main), (app_name, app) = files
    assert main_name == "26501234567890F0103407100000000151220262650.xml"
    assert app_name == "26501234567890F0134107100000000151220262650.xml"
    for data, xsd in ((main, "F0103407.xsd"), (app, "F0134107.xsd")):
        doc = etree.fromstring(data)
        schema = _schema(xsd)
        assert schema.validate(doc), schema.error_log
    doc = etree.fromstring(main)
    val = lambda x: doc.findtext(f".//{x}")  # noqa: E731
    assert (val("R001G3"), val("R008G3"), val("R02G1"), val("R022G3"), val("R021G3")) == (
        "1150.50", "1150.50", "998.40", "10376.40", "22828.08")
    assert val("HNAME") == "Тестова Маргарита Іванівна" and val("HNACTL") == "0"
    assert doc.find(".//LINKED_DOCS/DOC/FILENAME").text == app_name
    a = etree.fromstring(app)
    assert (a.findtext(".//R091G4"), a.findtext(".//R09G4"), a.findtext(".//R081G1")) == ("1902.34", "22828.08", "6")
    assert main.startswith(b'<?xml version="1.0" encoding="windows-1251"?>')


def test_zip_and_escaping(db, seed, profile):
    with db.session() as s:
        update_settings(s, seed["actors"]["director"], {"fop_address": "вул. Ш & К <1>"})
        body, name = tax_declaration.declaration_zip(s, seed["actors"]["director"], 2026, date(2027, 1, 15))
    assert name == "deklaratsiia-fop-2026.zip"
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = z.namelist()
        main = z.read(names[0])
    assert len(names) == 2 and _schema("F0103407.xsd").validate(etree.fromstring(main))
    assert etree.fromstring(main).findtext(".//HLOC") == "вул. Ш & К <1>"


def test_missing_profile_and_validation(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        data = tax_declaration.declaration_data(s, d, 2026)
        assert not data["ready"] and "РНОКПП (податковий номер)" in data["missing"]
        with pytest.raises(ValidationError):
            tax_declaration.declaration_files(s, d, 2026, date(2027, 1, 1))
        for bad in ({"fop_tin": "12345"}, {"fop_kved": "96"}, {"fop_c_reg": 120}, {"fop_c_sti": 12},
                    {"fop_min_wage": -1}, {"fop_email": "ні"}, {"fop_name": 5}):
            with pytest.raises(ValidationError):
                update_settings(s, d, bad)
        for who in ("admin", "m1"):
            with pytest.raises(Forbidden):
                tax_declaration.declaration_data(s, seed["actors"][who], 2026)


def test_api(client_api):
    c, seed, hd = client_api
    assert c.get("/api/reports/declaration", params={"year": 2026}, headers=hd).json()["ready"] is False
    assert c.get("/api/reports/declaration.zip", params={"year": 2026}, headers=hd).status_code == 422
    assert c.put("/api/settings", json=PROFILE, headers=hd).status_code == 200
    r = c.get("/api/reports/declaration.zip", params={"year": 2026}, headers=hd)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    tok = c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]
    assert c.get("/api/reports/declaration", params={"year": 2026},
                 headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_rates_by_date_override_settings(db, seed, profile):
    from mds.services import taxes

    d = seed["actors"]["director"]
    with db.session() as s:
        taxes.add_rate(s, d, key="min_wage", valid_from=date(2025, 1, 1), amount=800000)
        taxes.add_rate(s, d, key="single_tax", valid_from=date(2025, 1, 1), amount=30280)
        taxes.add_rate(s, d, key="min_wage", valid_from=date(2026, 1, 1), amount=864700)
        d25 = tax_declaration.declaration_data(s, d, 2025)
        d26 = tax_declaration.declaration_data(s, d, 2026)
    assert (d25["min_wage"], d25["single_tax_month"], d25["esv_month"], d25["military_levy"]) == (800000, 30280, 176000, 960000)
    assert d26["min_wage"] == 864700 and d26["single_tax_month"] == 30280
    assert d25["rates_source"] == "ставки з датами"
