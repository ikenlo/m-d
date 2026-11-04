"""Тестовий (демо) рік роботи салону — ЛИШЕ для тестового середовища.

Запуск: `python -m mds demo-year` або змінна сервера `MDS_DEMO_YEAR=yes` (тоді `start` запускає це
в окремому процесі після старту сайту — власний годинник `clock` не впливає на роботу сервера).

Правила:
- нічого не видаляє і не змінює наявні дані — лише ДОДАЄ: 4 демо-майстрині, клієнтів, записи, замовлення,
  зміни, виплати, витрати; використовує наявні послуги салону;
- перед початком — резервна копія бази;
- повторний запуск нічого не дублює (якщо демо-майстрині вже є — пропуск);
- сповіщення в Telegram під час заповнення не ставляться в чергу;
- демо-дані розпізнаються: логіни `demo_*`, «(демо)» в іменах і описах, телефони клієнтів 0679990000–0679999999.
"""

from __future__ import annotations

import logging
import random
import secrets
from datetime import date, datetime, timedelta

from sqlalchemy import select

from . import clock
from .db import Database
from .models import ApptStatus, Role, Service, User

log = logging.getLogger("mds.demo")

DEMO_PREFIX = "demo_"
PHONE_BASE = 679990000  # 0679990000 + i
SEASON = {11: 1.0, 12: 1.3, 1: 0.8, 2: 0.95, 3: 1.2, 4: 1.05, 5: 1.1, 6: 0.95, 7: 0.85, 8: 0.85, 9: 1.05, 10: 1.1}
# логін, ім'я, частка bp, ключові слова категорій послуг (за назвою категорії), вихідний день (0 = пн)
MASTERS = [
    ("olena", "Олена Коваль (демо)", 5000, ("стрижк", "зачіск"), 0),
    ("iryna", "Ірина Савчук (демо)", 4500, ("фарбув", "реконструкц"), 1),
    ("natalia", "Наталія Бондар (демо)", 5000, ("стрижк", "фарбув"), 2),
    ("viktoria", "Вікторія Мельник (демо)", 4500, ("брови", "вії", "макіяж", "зачіск"), 3),
]
FIRST = ["Анна", "Олена", "Марія", "Ірина", "Тетяна", "Наталія", "Юлія", "Оксана", "Катерина", "Світлана", "Віталій",
         "Андрій", "Олександр", "Дмитро", "Сергій", "Максим", "Іван", "Софія", "Дарина", "Вікторія", "Людмила", "Роман"]
LAST = ["Шевченко", "Коваленко", "Бондаренко", "Ткаченко", "Кравченко", "Олійник", "Шевчук", "Поліщук", "Лисенко",
        "Мороз", "Марченко", "Петренко", "Руденко", "Савченко", "Гончаренко", "Левченко", "Павленко", "Клименко"]


def _weight(svc: Service) -> float:
    """Дешеві й короткі послуги замовляють частіше."""
    return max(0.3, 6.0 - svc.price / 100000)


def seed_year(db: Database, start: date = date(2025, 11, 1), end: date = date(2026, 10, 31), *,
              seed: int = 2026, clients: int = 650) -> dict:
    from .services import bookings, expenses, notifications, orders, users
    from .services.common import Actor

    with db.session() as s:
        if s.scalar(select(User).where(User.username.like(DEMO_PREFIX + "%"))):
            log.info("Демо-дані вже є — пропуск")
            return {"skipped": True}
        director = s.scalar(select(User).where(User.role == Role.DIRECTOR, User.is_active.is_(True)).order_by(User.id))
        if director is None:
            raise RuntimeError("Немає директора")
        services = list(s.scalars(select(Service).where(Service.is_active.is_(True)).order_by(Service.id)))
        if not services:
            raise RuntimeError("Немає послуг — спершу заповніть прайс")
        svc = {x.id: (x.duration_min, x.price, _weight(x), (x.category or "").lower()) for x in services}
        da = Actor.of(director)

    R = random.Random(seed)
    # сповіщення в Telegram під час заповнення — вимкнено (лише в цьому процесі)
    real_enqueue = notifications.enqueue
    notifications.enqueue = lambda *a, **k: False
    stats = {"orders": 0, "cancelled": 0, "no_show": 0, "shifts": 0, "expenses": 0}
    try:
        masters = []
        with db.session() as s:
            for login, name, share, cats, off in MASTERS:
                ids = [i for i, v in svc.items() if any(c in v[3] for c in cats)] or list(svc)
                u = users.create_user(s, Actor.system(), username=DEMO_PREFIX + login, password=secrets.token_urlsafe(12),
                                      full_name=name, role=Role.MASTER, share_bp=share)
                masters.append({"id": u.id, "actor": Actor.of(u), "services": ids, "off": off})
        people = [(f"{R.choice(FIRST)} {R.choice(LAST)}", f"0{PHONE_BASE + i}") for i in range(clients)]
        weights = [5 if i < clients // 4 else 1 for i in range(clients)]

        day = start
        while day <= end:
            at = datetime.combine(day, datetime.min.time())
            if day.day == 1:
                clock.set_now(at.replace(hour=8))
                with db.session() as s:
                    for cat, amount, shift, text in (
                        ("rent", 1800000, 0, "Оренда приміщення"),
                        ("utilities", R.randint(350000, 650000) + (200000 if day.month in (12, 1, 2) else 0), 9, "Електроенергія, вода"),
                        ("advertising", R.choice([200000, 300000, 500000]), 4, "Реклама в Instagram"),
                        ("materials", R.randint(800000, 1500000), 2, "Закупівля фарб і матеріалів"),
                        ("other", R.randint(80000, 200000), 14, "Господарські товари"),
                    ):
                        expenses.create_expense(s, da, category=cat, amount=amount, spent_on=day + timedelta(days=shift),
                                                description=f"{text} (демо)")
                        stats["expenses"] += 1
            if day.weekday() == 6:  # неділя — вихідний
                day += timedelta(days=1)
                continue
            for mm in masters:
                if day.weekday() == mm["off"] or R.random() < 0.03:
                    continue
                n = max(1, round(R.uniform(3, 7) * SEASON[day.month]))
                t, plan = at.replace(hour=9), []
                clock.set_now(at.replace(hour=8))
                with db.session() as s:
                    for _ in range(n):
                        sid = R.choices(mm["services"], weights=[svc[i][2] for i in mm["services"]])[0]
                        dur = svc[sid][0]
                        if t + timedelta(minutes=dur) > at.replace(hour=20):
                            break
                        name, phone = people[R.choices(range(clients), weights=weights)[0]]
                        a = bookings.create_appointment(s, da, master_id=mm["id"], service_id=sid, starts_at=t,
                                                        client_name=name, client_phone=phone)
                        plan.append((a.id, sid, t, dur))
                        t += timedelta(minutes=dur + R.choice([0, 0, 15, 30, 60]))
                closed = False
                for aid, sid, st, dur in plan:
                    r = R.random()
                    clock.set_now(st + timedelta(minutes=dur))
                    with db.session() as s:
                        if r < 0.05:
                            bookings.set_status(s, da, aid, ApptStatus.CANCELLED, "Клієнт скасував", notify_client=False)
                            stats["cancelled"] += 1
                            continue
                        if r < 0.07:
                            bookings.set_status(s, da, aid, ApptStatus.NO_SHOW, "Не прийшов", notify_client=False)
                            stats["no_show"] += 1
                            continue
                        price = svc[sid][1]
                        amount = max(0, price + R.choice([0, 0, 0, 0, 5000, 10000, -5000, 20000]))
                        mats = None
                        if "фарбув" in svc[sid][3] or "реконструкц" in svc[sid][3]:
                            mats = [{"name": "Фарба / склад (демо)", "qty": 1, "unit_cost": price * 22 // 100}]
                        orders.close_order(s, mm["actor"], aid, amount=amount,
                                           payment_method="cash" if R.random() < 0.35 else "card", materials=mats)
                        stats["orders"] += 1
                        closed = True
                if closed:
                    clock.set_now(at.replace(hour=20, minute=15))
                    with db.session() as s:
                        sh = orders.close_shift(s, mm["actor"], mm["id"])
                        due, shid = sh.payout_due, sh.id
                    stats["shifts"] += 1
                    if due > 0:
                        clock.set_now(at.replace(hour=21))
                        with db.session() as s:
                            orders.confirm_payout(s, da, master_id=mm["id"], amount=due, shift_id=shid, method="card",
                                                  note="Переказ на картку (демо)")
            if day.day == 1:
                log.info("Демо-рік: %s %s", day, stats)
            day += timedelta(days=1)
    finally:
        notifications.enqueue = real_enqueue
        clock.set_now(None)
    log.info("Демо-рік готовий: %s", stats)
    return stats
