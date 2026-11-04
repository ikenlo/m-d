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


# ------------------------------------------------------------------ етап 2: склад, клієнти, графік, майбутні записи
MATERIALS = [  # назва, тон, категорія, од., упаковка, розмір, ціна уп. коп, мін. залишок (од.), використання уп./міс
    ("Igora Royal", "6-0", "Фарби", "г", "туба", 60, 32000, 120, 3),
    ("Igora Royal", "7-1", "Фарби", "г", "туба", 60, 32000, 120, 4),
    ("Igora Royal", "8-0", "Фарби", "г", "туба", 60, 32000, 120, 4),
    ("Igora Royal", "9-1", "Фарби", "г", "туба", 60, 32000, 120, 3),
    ("Igora Royal", "5-0", "Фарби", "г", "туба", 60, 32000, 120, 2),
    ("Igora Vibrance", "9-12", "Фарби", "г", "туба", 60, 34000, 60, 2),
    ("Blondme Lift", None, "Освітлення", "г", "банка", 450, 98000, 450, 1),
    ("Igora Oil Developer 6%", None, "Окисники", "мл", "флакон", 1000, 42000, 1000, 2),
    ("Igora Oil Developer 9%", None, "Окисники", "мл", "флакон", 1000, 42000, 1000, 1),
    ("Шампунь BC Color Freeze", None, "Догляд", "мл", "флакон", 1000, 85000, 1000, 2),
    ("Маска BC Repair", None, "Догляд", "мл", "банка", 750, 120000, 750, 1),
    ("Склад для холодної реконструкції", None, "Догляд", "мл", "набір", 500, 260000, 250, 1),
    ("Фарба для брів RefectoCil", "графіт", "Брови та вії", "мл", "туба", 15, 38000, 15, 1),
    ("Склад для ламінування вій", None, "Брови та вії", "мл", "набір", 30, 95000, 10, 1),
    ("Віск для депіляції", None, "Брови та вії", "г", "банка", 400, 46000, 200, 1),
    ("Лак для волосся", None, "Стайлінг", "мл", "флакон", 500, 36000, 500, 2),
    ("Рукавички нітрилові", None, "Витратні", "шт", "уп.", 100, 28000, 100, 4),
    ("Пеньюар одноразовий", None, "Витратні", "шт", "уп.", 50, 35000, 50, 3),
    ("Фольга перукарська", None, "Витратні", "шт", "рулон", 1, 42000, 2, 1),
]
NOTES = ["Алергія на аміак — лише безаміачні фарби", "Любить холодні відтінки блонду", "Чутлива шкіра голови",
         "Завжди приходить з дитиною", "Постійна клієнтка, знижка 5%", "Не любить коротко на скронях",
         "Віддає перевагу записам зранку", "Фарбування 7-1 + 8-0 (1:1), окисник 6%", "Платить карткою",
         "Просила нагадувати за день", "Довге густе волосся — закладати більше часу", "Тонке волосся, без філірування"]


def seed_extra(db: Database, start: date = date(2025, 11, 1), end: date = date(2026, 10, 31), *,
               future_days: int = 25, seed: int = 2027) -> dict:
    """Етап 2: склад з рухами за рік, нотатки клієнтів, графік і послуги майстринь, майбутні записи, комунальні,
    генератор, кілька виправлень замовлень. Лише додає; повторний запуск — пропуск."""
    from .models import Client, Expense, Material, Order
    from .services import bookings, catalog, expenses, notifications, orders, schedule, stock, users, utilities
    from .services.common import Actor, update_settings

    with db.session() as s:
        if s.scalar(select(User).where(User.username == DEMO_PREFIX + "admin")):
            log.info("Демо-етап 2 вже виконано — пропуск")
            return {"skipped": True}
        masters = list(s.scalars(select(User).where(User.username.like(DEMO_PREFIX + "%"), User.role == Role.MASTER)
                                 .order_by(User.id)))
        if not masters:
            raise RuntimeError("Спершу демо-рік (seed_year)")
        director = s.scalar(select(User).where(User.role == Role.DIRECTOR, User.is_active.is_(True)).order_by(User.id))
        da = Actor.of(director)
        services = {x.id: x for x in s.scalars(select(Service).where(Service.is_active.is_(True)))}
        offs = {m.username: off for m in masters for login, _, _, _, off in MASTERS if m.username == DEMO_PREFIX + login}
        cats = {DEMO_PREFIX + login: c for login, _, _, c, _ in MASTERS}
        mlist = [(m.id, m.username) for m in masters]

    R = random.Random(seed)
    real_enqueue = notifications.enqueue
    notifications.enqueue = lambda *a, **k: False
    st = {"materials": 0, "stock_moves": 0, "client_notes": 0, "future": 0, "readings": 0, "generator_runs": 0,
          "corrections": 0, "day_offs": 0}
    try:
        clock.set_now(datetime.combine(start, datetime.min.time()).replace(hour=8))
        with db.session() as s:
            users.create_user(s, Actor.system(), username=DEMO_PREFIX + "admin", password=secrets.token_urlsafe(12),
                              full_name="Христина (демо, адміністратор)", role=Role.ADMIN)
            # графік (6 днів, свій вихідний) і послуги кожної майстрині
            for mid, login in mlist:
                sch = {str(d): ["09:00", "20:00"] for d in range(6) if d != offs[login]}
                ids = [i for i, x in services.items() if any(c in (x.category or "").lower() for c in cats[login])]
                users.update_user(s, da, mid, {"schedule": sch, "service_ids": ids or list(services)})
            # склад: початкові залишки
            mats = []
            for name, tone, cat, unit, pack, size, cost, mn, use in MATERIALS:
                m = catalog.create_material(s, da, {"name": name, "tone": tone, "category": cat, "unit": unit,
                                                    "pack_name": pack, "pack_size": size, "unit_cost": cost,
                                                    "min_stock": mn, "stock": size * (use + 2)})
                mats.append((m.id, size, use, mn))
                st["materials"] += 1
            # генератор: параметри (потрібні для розрахунку вартості запусків)
            update_settings(s, da, {"generator_fuel_lph_milli": 1500, "generator_fuel_price": 5800,
                                    "generator_amort_per_hour": 2000})

        # рухи складу щомісяця: прихід 3-го числа, списання щотижня, інвентаризація щокварталу
        d = start
        while d <= end:
            if d.day == 3 or d.weekday() == 5 or (d.day == 28 and d.month % 3 == 0):
                clock.set_now(datetime.combine(d, datetime.min.time()).replace(hour=19))
                with db.session() as s:
                    for mid, size, use, _mn in mats:
                        if d.day == 3:
                            packs = max(1, use + R.choice([-1, 0, 0, 1]))
                            stock.receive(s, da, mid, size * packs, note="Закупівля у дистриб'ютора (демо)")
                            st["stock_moves"] += 1
                        if d.weekday() == 5:
                            q = max(1, round(size * use / 4 * R.uniform(0.7, 1.2)))
                            cur = s.get(Material, mid).stock_milli // 1000
                            if cur > q:
                                stock.writeoff(s, da, mid, q, note="Використано в роботі за тиждень (демо)")
                                st["stock_moves"] += 1
                        if d.day == 28 and d.month % 3 == 0:
                            cur = s.get(Material, mid).stock_milli // 1000
                            if cur > 0 and R.random() < 0.4:
                                stock.adjust(s, da, mid, max(0, cur - R.randint(1, max(1, size // 10))),
                                             note="Квартальна інвентаризація (демо)")
                                st["stock_moves"] += 1
            d += timedelta(days=1)
        # кілька позицій — нижче мінімуму (щоб було видно попередження)
        clock.set_now(datetime.combine(end, datetime.min.time()).replace(hour=20))
        with db.session() as s:
            for mid, _size, _use, mn in mats[:3]:
                stock.adjust(s, da, mid, max(0, mn // 2), note="Інвентаризація: закінчується (демо)")

        # комунальні: показники лічильника щомісяця (підтверджені → витрата «Комунальні»)
        meter, m0 = 12000.0, start
        with db.session() as s:
            for e in s.scalars(select(Expense).where(Expense.category == "utilities", Expense.description.like("%(демо)%"))):
                expenses.update_expense(s, da, e.id, {"amount": R.randint(50000, 90000), "description": "Вода, вивіз сміття (демо)"},
                                        "Електроенергія тепер окремо — за лічильником")
        while m0 <= end:
            nxt = date(m0.year + (m0.month == 12), m0.month % 12 + 1, 1)
            used = R.randint(650, 1150) + (300 if m0.month in (12, 1, 2) else 0)
            clock.set_now(datetime.combine(nxt, datetime.min.time()).replace(hour=10))
            with db.session() as s:
                r = utilities.create_reading(s, da, month=m0, read_on=nxt - timedelta(days=1),
                                             prev=f"{meter:.1f}" if m0 == start else None,
                                             curr=f"{meter + used:.1f}", tariff=432,
                                             note="Показники зняла адміністратор (демо)")
                utilities.confirm_reading(s, da, r.id)
            meter += used
            st["readings"] += 1
            m0 = nxt
        # генератор: відключення світла взимку
        for mon in ((2025, 11), (2025, 12), (2026, 1), (2026, 2)):
            for _ in range(R.randint(6, 11)):
                sd = datetime(mon[0], mon[1], R.randint(1, 28), R.choice([9, 11, 13, 15, 17]))
                clock.set_now(sd + timedelta(hours=6))
                with db.session() as s:
                    utilities.add_run(s, da, started_at=sd, hours=R.choice([2, 2.5, 3, 4, 4.5]),
                                      note="Відключення за графіком (демо)")
                st["generator_runs"] += 1

        # нотатки клієнтів
        with db.session() as s:
            demo_clients = list(s.scalars(select(Client).where(Client.phone.like("+38067999%") | Client.phone.like("067999%"))
                                          .order_by(Client.id)))
            for c in R.sample(demo_clients, min(140, len(demo_clients))):
                catalog.update_client(s, da, c.id, {"notes": R.choice(NOTES) + " (демо)"})
                c.reminders = R.random() < 0.7
                st["client_notes"] += 1
            phones = [c.phone for c in demo_clients]
            names = {c.phone: c.name for c in demo_clients}

        # кілька виправлень замовлень (знижки) — видно в журналі
        with db.session() as s:
            ords = list(s.scalars(select(Order).where(Order.master_id.in_([m for m, _ in mlist])).order_by(Order.id)))
            picks = R.sample(ords, min(10, len(ords)))
        for o in picks:
            clock.set_now(o.closed_at + timedelta(hours=2))
            with db.session() as s:
                orders.correct_order(s, da, o.id, {"amount": max(0, o.amount - 5000)}, "Знижка постійній клієнтці 50 грн (демо)")
            st["corrections"] += 1

        # графік: відпустки й вихідні наперед
        clock.set_now(None)
        today = clock.now().date()
        with db.session() as s:
            for i, (mid, _login) in enumerate(mlist):
                for k in (3 + i * 5, 4 + i * 5):
                    schedule.set_day(s, da, mid, today + timedelta(days=k), off=True)
                    st["day_offs"] += 1
        # майбутні записи (реальний поточний час)
        for k in range(1, future_days + 1):
            day = today + timedelta(days=k)
            if day.weekday() == 6:
                continue
            for mid, login in mlist:
                if day.weekday() == offs[login] or k in (3 + mlist.index((mid, login)) * 5, 4 + mlist.index((mid, login)) * 5):
                    continue
                ids = [i for i, x in services.items() if any(c in (x.category or "").lower() for c in cats[login])] or list(services)
                t = datetime.combine(day, datetime.min.time()).replace(hour=9)
                fill = 0.8 if k <= 7 else 0.45
                with db.session() as s:
                    while t < t.replace(hour=19):
                        sid = R.choice(ids)
                        dur = services[sid].duration_min
                        if t + timedelta(minutes=dur) > t.replace(hour=20):
                            break
                        if R.random() < fill:
                            ph = R.choice(phones)
                            bookings.create_appointment(s, da, master_id=mid, service_id=sid, starts_at=t,
                                                        client_name=names[ph], client_phone=ph,
                                                        status="pending" if R.random() < 0.15 else "confirmed")
                            st["future"] += 1
                        t += timedelta(minutes=dur + 30)
    finally:
        notifications.enqueue = real_enqueue
        clock.set_now(None)
    log.info("Демо-етап 2 готовий: %s", st)
    return st
