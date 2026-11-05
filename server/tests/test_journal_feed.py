from datetime import date, datetime

import pytest

from mds import clock
from mds.errors import Forbidden
from mds.services import bookings, expenses, journal_feed, orders, users
from mds.services.common import update_settings


def test_feed_is_human_readable(db, seed):
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        a = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"],
                                        starts_at=datetime(2026, 11, 2, 10), client_name="Анна", client_phone="0671112233")
        aid = a.id
    clock.set_now(datetime(2026, 11, 2, 11))
    with db.session() as s:
        o = orders.close_order(s, m1, aid, amount=65000, payment_method="card", client_name="Анна Коваленко")
        orders.correct_order(s, d, o.id, {"amount": 70000}, "Доплата за укладку")
        expenses.create_expense(s, d, category="rent", amount=2500000, spent_on=date(2026, 11, 2), description="Оренда листопад")
        users.update_user(s, d, seed["m2"], {"share_bp": 4500})
        update_settings(s, d, {"slot_step_min": 15})

    with db.session() as s:
        f = journal_feed.feed(s, d, date_from=date(2026, 11, 2), date_to=date(2026, 11, 2))
    by_title = {x["title"]: x for x in f}
    assert "Новий запис" in by_title and "Анна" in by_title["Новий запис"]["text"]
    assert "Стрижка" in by_title["Новий запис"]["text"] and "Олена" in by_title["Новий запис"]["text"]
    closed = by_title["Замовлення закрито"]
    assert closed["amount"] == 65000 and "картка" in closed["text"] and "Майстру 325 ₴" in closed["text"]
    assert closed["who"] == "Олена" and closed["category"] == "money"
    fixed = by_title["Замовлення виправлено"]
    assert fixed["reason"] == "Доплата за укладку"
    assert {"field": "Сума", "before": "650 ₴", "after": "700 ₴"} in fixed["changes"]
    assert by_title["Уточнено ім'я клієнта"]["text"] == "Анна → Анна Коваленко"
    assert by_title["Витрата: Оренда"]["amount"] == 2500000
    assert {"field": "Відсоток майстра", "before": "40%", "after": "45%"} in by_title["Акаунт змінено"]["changes"]
    assert {"field": "Крок слотів, хв", "before": "30", "after": "15"} in by_title["Налаштування змінено"]["changes"]
    assert not any(":" in x["title"] and x["title"].split(":")[0] in ("order", "appointment", "user") for x in f)

    with db.session() as s:
        money = journal_feed.feed(s, d, date_from=date(2026, 11, 2), date_to=date(2026, 11, 2), category="money")
        assert {x["category"] for x in money} == {"money"}
        found = journal_feed.feed(s, d, date_from=date(2026, 11, 2), date_to=date(2026, 11, 2), q="укладку")
        assert [x["title"] for x in found] == ["Замовлення виправлено"]
        with pytest.raises(Forbidden):
            journal_feed.feed(s, m1, date_from=date(2026, 11, 2), date_to=date(2026, 11, 2))


def test_feed_api(client_api):
    c, ids, d = client_api
    r = c.get("/api/journal/feed?date_from=2026-11-01&date_to=2026-11-30", headers=d)
    assert r.status_code == 200 and isinstance(r.json(), list)
    assert c.get("/api/journal/feed?date_from=2020-01-01&date_to=2026-11-30", headers=d).status_code == 422


def test_schedule_event_shows_date_without_time(db, seed):
    from mds.services import schedule
    d = seed["actors"]["director"]
    with db.session() as s:
        schedule.set_day(s, d, seed["m1"], date(2026, 11, 8), off=True)
        f = journal_feed.feed(s, d, date_from=date(2026, 11, 2), date_to=date(2026, 11, 2), category="records")
    assert f[0]["title"] == "Графік змінено" and f[0]["text"] == "Олена · 08.11.2026 · вихідний"
