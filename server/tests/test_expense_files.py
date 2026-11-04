"""Витрати: спосіб оплати, нові категорії, фото/файли документів, права."""

from datetime import date

import pytest

from mds.errors import Forbidden, ValidationError
from mds.services import analytics, expenses

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
PDF = b"%PDF-1.4\n" + b"0" * 100


def test_payment_method_categories_dashboard(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        e = expenses.create_expense(s, d, category="equipment", amount=500000, spent_on=date(2026, 3, 2),
                                    description="Фен", payment_method="card")
        expenses.create_expense(s, d, category="household", amount=30000, spent_on=date(2026, 3, 3), payment_method="cash")
        with pytest.raises(ValidationError):
            expenses.create_expense(s, d, category="rent", amount=1, spent_on=date(2026, 3, 1), payment_method="crypto")
        expenses.update_expense(s, d, e.id, {"payment_method": "account"}, "оплачено з рахунку ФОП")
        assert expenses.expense_public(e)["payment_method"] == "account"
        dash = analytics.dashboard(s, d, date(2026, 3, 1), date(2026, 3, 31))
    assert dash["expenses"]["equipment"] == 500000 and dash["expenses"]["household"] == 30000
    assert dash["operating_expenses"] == 530000


def test_files_validation_and_permissions(db, seed):
    d = seed["actors"]["director"]
    with db.session() as s:
        e = expenses.create_expense(s, d, category="rent", amount=900000, spent_on=date(2026, 3, 1))
        f = expenses.add_file(s, d, e.id, filename="../чек оренда.png", content_type="image/png", data=PNG)
        expenses.add_file(s, d, e.id, filename="акт.pdf", content_type="application/pdf", data=PDF)
        assert f.filename == ".. чек оренда.png" and f.size == len(PNG)
        for bad in (dict(content_type="text/html", data=b"<html>"), dict(content_type="image/png", data=PDF),
                    dict(content_type="application/pdf", data=b""), dict(content_type="image/jpeg", data=b"\xff\xd8" + b"0" * (5 * 1024 * 1024))):
            with pytest.raises(ValidationError):
                expenses.add_file(s, d, e.id, filename="x", **bad)
        assert [x["filename"] for x in expenses.files_for(s, [e.id])[e.id]] == [".. чек оренда.png", "акт.pdf"]
        with pytest.raises(Forbidden):
            expenses.add_file(s, seed["actors"]["admin"], e.id, filename="x.png", content_type="image/png", data=PNG)
        with pytest.raises(Forbidden):
            expenses.get_file(s, seed["actors"]["m1"], f.id)
        expenses.delete_file(s, d, f.id)
        assert len(expenses.files_for(s, [e.id])[e.id]) == 1


def test_files_api(client_api):
    c, ids, hd = client_api
    e = c.post("/api/expenses", headers=hd, json={"category": "household", "amount": 12000, "spent_on": "2026-03-05",
                                                  "payment_method": "cash", "description": "Миючі засоби"}).json()
    r = c.post(f"/api/expenses/{e['id']}/files", params={"filename": "чек.png"}, content=PNG,
               headers=hd | {"Content-Type": "image/png"})
    assert r.status_code == 201
    fid = r.json()["id"]
    lst = c.get("/api/expenses", params={"date_from": "2026-03-01", "date_to": "2026-03-31"}, headers=hd).json()
    assert lst[0]["payment_method"] == "cash" and lst[0]["files"][0]["filename"] == "чек.png"
    g = c.get(f"/api/expense-files/{fid}", headers=hd)
    assert g.status_code == 200 and g.content == PNG and g.headers["content-type"] == "image/png"
    tok = c.post("/api/auth/login", json={"username": "olena", "password": "Master123"}).json()["token"]
    assert c.get(f"/api/expense-files/{fid}", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    assert c.delete(f"/api/expense-files/{fid}", headers=hd).status_code == 204
