from __future__ import annotations

import logging
import random
import secrets
from datetime import date, datetime, timedelta

from sqlalchemy import delete, or_, select

from . import clock
from .db import Database
from .models import (
    ApptStatus, Appointment, AuditLog, CardPayment, Client, Expense, GeneratorRun, LedgerEntry, MasterCharge,
    Material, MeterReading, Order, OrderMaterial, Payout, Role, ScheduleDay, Service, Shift, StockMove, User,
    master_services,
)

log = logging.getLogger("mds.demo_fop1")

DEMO_PREFIX = "demo_"
PHONE_BASE = 679990000
FIRST = ["Анна", "Олена", "Марія", "Ірина", "Тетяна", "Наталія", "Юлія", "Оксана", "Катерина", "Світлана", "Віталій",
         "Андрій", "Олександр", "Дмитро", "Сергій", "Максим", "Іван", "Софія", "Дарина", "Вікторія", "Людмила", "Роман"]
LAST = ["Шевченко", "Коваленко", "Бондаренко", "Ткаченко", "Кравченко", "Олійник", "Шевчук", "Поліщук", "Лисенко",
        "Мороз", "Марченко", "Петренко", "Руденко", "Савченко", "Гончаренко", "Левченко", "Павленко", "Клименко"]
MATERIALS = [
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

FOP_LOGIN = DEMO_PREFIX + "fop"
FOP_NAME = "Маргарита Демченко (демо, ФОП 1 група)"
DEMO_PHONE_LIKE = "+38067999%"
SEASON = {1: 0.85, 2: 0.9, 3: 1.1, 4: 1.0, 5: 1.05, 6: 0.95, 7: 0.85, 8: 0.85, 9: 1.05, 10: 1.1, 11: 1.05, 12: 1.25}
TAXES = {2025: (30280, 80000, 176000), 2026: (33280, 86470, 190234)}


def purge_demo(db: Database) -> dict:
    with db.session() as s:
        du = list(s.scalars(select(User.id).where(User.username.like(DEMO_PREFIX + "%"))))
        appts = list(s.scalars(select(Appointment.id).where(Appointment.master_id.in_(du)))) if du else []
        ords = list(s.scalars(select(Order.id).where(or_(Order.master_id.in_(du), Order.appointment_id.in_(appts))))) if du else []
        shifts = list(s.scalars(select(Shift.id).where(Shift.master_id.in_(du)))) if du else []
        pays = list(s.scalars(select(Payout.id).where(Payout.master_id.in_(du)))) if du else []
        charges = list(s.scalars(select(MasterCharge.id).where(MasterCharge.master_id.in_(du)))) if du else []
        exps = list(s.scalars(select(Expense.id).where(Expense.description.like("%(демо)%"))))
        readings = list(s.scalars(select(MeterReading.id).where(or_(MeterReading.note.like("%(демо)%"),
                                                                    MeterReading.expense_id.in_(exps)))))
        exps = sorted(set(exps) | set(s.scalars(select(MeterReading.expense_id).where(MeterReading.id.in_(readings),
                                                                                       MeterReading.expense_id.isnot(None)))))
        runs = list(s.scalars(select(GeneratorRun.id).where(GeneratorRun.note.like("%(демо)%"))))
        names = {m[0] for m in MATERIALS}
        mats = []
        for m in s.scalars(select(Material).where(Material.name.in_(names))):
            notes = list(s.scalars(select(StockMove.note).where(StockMove.material_id == m.id)))
            if all(n and ("(демо)" in n or n == "Початковий залишок") for n in notes):
                mats.append(m.id)
        cards = list(s.scalars(select(CardPayment.id).where(CardPayment.appointment_id.in_(appts)))) if appts else []

        def audit_for(entity, ids):
            return (AuditLog.entity == entity) & AuditLog.entity_id.in_(ids)

        st = {"users": len(du), "orders": len(ords), "appointments": len(appts), "expenses": len(exps),
              "materials": len(mats)}
        s.execute(delete(CardPayment).where(CardPayment.id.in_(cards)))
        s.execute(delete(StockMove).where(or_(StockMove.order_id.in_(ords), StockMove.material_id.in_(mats))))
        s.execute(delete(OrderMaterial).where(OrderMaterial.order_id.in_(ords)))
        s.execute(delete(LedgerEntry).where(or_(
            LedgerEntry.master_id.in_(du),
            (LedgerEntry.ref_type == "order") & LedgerEntry.ref_id.in_(ords),
            (LedgerEntry.ref_type == "shift") & LedgerEntry.ref_id.in_(shifts),
            (LedgerEntry.ref_type == "payout") & LedgerEntry.ref_id.in_(pays),
            (LedgerEntry.ref_type == "expense") & LedgerEntry.ref_id.in_(exps),
            (LedgerEntry.ref_type == "master_charge") & LedgerEntry.ref_id.in_(charges))))
        s.execute(delete(Order).where(Order.id.in_(ords)))
        s.execute(delete(Payout).where(Payout.id.in_(pays)))
        s.execute(delete(MasterCharge).where(MasterCharge.id.in_(charges)))
        s.execute(delete(Shift).where(Shift.id.in_(shifts)))
        s.execute(delete(Appointment).where(Appointment.id.in_(appts)))
        s.execute(delete(MeterReading).where(MeterReading.id.in_(readings)))
        s.execute(delete(Expense).where(Expense.id.in_(exps)))
        s.execute(delete(GeneratorRun).where(GeneratorRun.id.in_(runs)))
        s.execute(delete(Material).where(Material.id.in_(mats)))
        s.execute(delete(ScheduleDay).where(ScheduleDay.master_id.in_(du)))
        s.execute(delete(master_services).where(master_services.c.master_id.in_(du)))
        used = select(Appointment.client_id)
        cl = list(s.scalars(select(Client.id).where(Client.phone.like(DEMO_PHONE_LIKE), Client.id.not_in(used))))
        s.execute(delete(Client).where(Client.id.in_(cl)))
        s.execute(delete(AuditLog).where(or_(
            AuditLog.user_id.in_(du), audit_for("appointment", appts), audit_for("order", ords),
            audit_for("shift", shifts), audit_for("payout", pays), audit_for("expense", exps),
            audit_for("material", mats), audit_for("client", cl), audit_for("user", du),
            audit_for("meter_reading", readings), audit_for("generator_run", runs), audit_for("card_payment", cards))))
        s.execute(delete(User).where(User.id.in_(du)))
        st["clients"] = len(cl)
    log.info("Демо-дані видалено: %s", st)
    return st


def _price_weight(svc: Service) -> float:
    return max(0.2, 5.0 - svc.price / 100000)


def seed_fop1(db: Database, start: date = date(2025, 1, 1), end: date | None = None, *, year_target: int = 110_000_000,
              future_days: int = 21, seed: int = 2025, clients: int = 380) -> dict:
    from .services import bookings, catalog, expenses, notifications, orders, schedule, stock, users, utilities
    from .services.common import Actor, get_setting, update_settings

    end = end or (clock.now().date() - timedelta(days=1))
    with db.session() as s:
        if s.scalar(select(User.id).where(User.username == FOP_LOGIN)):
            log.info("Демо «ФОП 1 група» вже є — пропуск")
            return {"skipped": True}
        director = s.scalar(select(User).where(User.role == Role.DIRECTOR, User.is_active.is_(True)).order_by(User.id))
        services = {x.id: x for x in s.scalars(select(Service).where(Service.is_active.is_(True)).order_by(Service.id))}
        if director is None or not services:
            raise RuntimeError("Потрібні директор і послуги")
        da = Actor.of(director)
        svc = {i: (x.duration_min, x.price, _price_weight(x), (x.category or "").lower()) for i, x in services.items()}

    R = random.Random(seed)
    real_enqueue = notifications.enqueue
    notifications.enqueue = lambda *a, **k: False
    st = {"orders": 0, "cancelled": 0, "no_show": 0, "expenses": 0, "stock_moves": 0, "readings": 0, "future": 0,
          "income_by_year": {}}
    try:
        clock.set_now(datetime.combine(start, datetime.min.time()).replace(hour=8))
        with db.session() as s:
            u = users.create_user(s, Actor.system(), username=FOP_LOGIN, password=secrets.token_urlsafe(12) + "a1",
                                  full_name=FOP_NAME, role=Role.MASTER, share_bp=0)
            users.update_user(s, da, u.id, {"schedule": {str(d): ["09:00", "19:00"] for d in range(5)},
                                            "service_ids": list(services)})
            fid, fa = u.id, Actor.of(u)
            mats = []
            for name, tone, cat, unit, pack, size, cost, mn, use in MATERIALS:
                use1 = max(1, use // 3)
                m = catalog.create_material(s, da, {"name": name, "tone": tone, "category": cat, "unit": unit,
                                                    "pack_name": pack, "pack_size": size, "unit_cost": cost,
                                                    "min_stock": mn, "stock": size * (use1 + 1)})
                mats.append((m.id, size, use1, mn))
            cur = {k: get_setting(s, k) for k in ("fop_income_limit", "fop_single_tax_month", "fop_min_wage")}
            upd = {k: v for k, v in (("fop_income_limit", 144404900), ("fop_single_tax_month", 33280),
                                     ("fop_min_wage", 864700)) if not cur[k]}
            if upd:
                update_settings(s, da, upd)
        people = [(f"{R.choice(FIRST)} {R.choice(LAST)}", f"0{PHONE_BASE + i}") for i in range(clients)]
        weights = [6 if i < clients // 5 else 1 for i in range(clients)]
        avg_season = sum(SEASON.values()) / 12

        month_budget = {}
        day = start
        while day <= end:
            at = datetime.combine(day, datetime.min.time())
            if day.day == 1:
                ep, vz, esv = TAXES.get(day.year, TAXES[2026])
                clock.set_now(at.replace(hour=9))
                with db.session() as s:
                    for cat, amount, shift, text in (
                        ("rent", 900000, 0, "Оренда кабінету"),
                        ("utilities", R.randint(40000, 70000), 9, "Вода, вивіз сміття"),
                        ("advertising", R.choice([100000, 150000, 200000]), 4, "Реклама в Instagram"),
                        ("materials", R.randint(300000, 550000), 2, "Закупівля фарб і матеріалів"),
                        ("other", R.randint(30000, 80000), 12, "Господарські товари, кава"),
                        ("other", ep + vz, 14, "Податки ФОП: єдиний податок + військовий збір"),
                    ):
                        expenses.create_expense(s, da, category=cat, amount=amount, spent_on=day + timedelta(days=shift),
                                                description=f"{text} (демо)")
                        st["expenses"] += 1
                    if day.month in (1, 4, 7, 10):
                        expenses.create_expense(s, da, category="other", amount=esv * 3, spent_on=day + timedelta(days=17),
                                                description="ЄСВ ФОП за квартал (демо)")
                        st["expenses"] += 1
                workdays = sum(1 for k in range(31) if (day + timedelta(days=k)).month == day.month
                               and (day + timedelta(days=k)).weekday() < 5)
                month_budget = {"left": year_target / 12 * SEASON[day.month] / avg_season, "days": workdays}
            if day.weekday() >= 5 or R.random() < 0.04:
                day += timedelta(days=1)
                continue
            daily = month_budget["left"] / max(1, month_budget["days"]) * R.uniform(0.75, 1.25)
            month_budget["days"] -= 1
            t, plan, planned = at.replace(hour=9), [], 0
            clock.set_now(at.replace(hour=8))
            with db.session() as s:
                while planned < daily:
                    sid = R.choices(list(svc), weights=[v[2] for v in svc.values()])[0]
                    dur = svc[sid][0]
                    if t + timedelta(minutes=dur) > at.replace(hour=19):
                        break
                    name, phone = people[R.choices(range(clients), weights=weights)[0]]
                    a = bookings.create_appointment(s, da, master_id=fid, service_id=sid, starts_at=t,
                                                    client_name=name, client_phone=phone)
                    plan.append((a.id, sid, t, dur))
                    planned += svc[sid][1]
                    t += timedelta(minutes=dur + R.choice([0, 15, 15, 30]))
            closed = False
            for aid, sid, stt, dur in plan:
                r = R.random()
                clock.set_now(stt + timedelta(minutes=dur))
                with db.session() as s:
                    if r < 0.04:
                        bookings.set_status(s, da, aid, ApptStatus.CANCELLED, "Клієнт скасував", notify_client=False)
                        st["cancelled"] += 1
                        continue
                    if r < 0.06:
                        bookings.set_status(s, da, aid, ApptStatus.NO_SHOW, "Не прийшов", notify_client=False)
                        st["no_show"] += 1
                        continue
                    price = svc[sid][1]
                    amount = max(10000, price + R.choice([0, 0, 0, 5000, -5000, 10000]))
                    mats_line = None
                    if "фарбув" in svc[sid][3] or "реконструкц" in svc[sid][3]:
                        mats_line = [{"name": "Фарба / склад (демо)", "qty": 1, "unit_cost": price * 20 // 100}]
                    orders.close_order(s, fa, aid, amount=amount, payment_method="cash" if R.random() < 0.45 else "card",
                                       materials=mats_line)
                    month_budget["left"] -= amount
                    y = str(day.year)
                    st["income_by_year"][y] = st["income_by_year"].get(y, 0) + amount
                    st["orders"] += 1
                    closed = True
            if closed:
                clock.set_now(at.replace(hour=19, minute=15))
                with db.session() as s:
                    orders.close_shift(s, fa, fid)
            day += timedelta(days=1)

        d = start
        while d <= end:
            if d.day == 3 or d.weekday() == 4:
                clock.set_now(datetime.combine(d, datetime.min.time()).replace(hour=18))
                with db.session() as s:
                    for mid, size, use1, _mn in mats:
                        if d.day == 3:
                            stock.receive(s, da, mid, size * max(1, use1 + R.choice([-1, 0, 0, 1])),
                                          note="Закупівля у дистриб'ютора (демо)")
                            st["stock_moves"] += 1
                        if d.weekday() == 4:
                            q = max(1, round(size * use1 / 4 * R.uniform(0.7, 1.2)))
                            if s.get(Material, mid).stock_milli // 1000 > q:
                                stock.writeoff(s, da, mid, q, note="Використано в роботі за тиждень (демо)")
                                st["stock_moves"] += 1
            d += timedelta(days=1)

        meter, m0 = 5200.0, date(start.year, start.month, 1)
        while m0 <= end:
            nxt = date(m0.year + (m0.month == 12), m0.month % 12 + 1, 1)
            if nxt > end + timedelta(days=1):
                break
            used = R.randint(180, 320) + (80 if m0.month in (12, 1, 2) else 0)
            clock.set_now(datetime.combine(nxt, datetime.min.time()).replace(hour=10))
            with db.session() as s:
                rd = utilities.create_reading(s, da, month=m0, read_on=nxt - timedelta(days=1),
                                              prev=f"{meter:.1f}" if st["readings"] == 0 else None,
                                              curr=f"{meter + used:.1f}", tariff=432, note="Показники (демо)")
                utilities.confirm_reading(s, da, rd.id)
            meter += used
            st["readings"] += 1
            m0 = nxt

        with db.session() as s:
            demo_clients = list(s.scalars(select(Client).where(Client.phone.like(DEMO_PHONE_LIKE)).order_by(Client.id)))
            for c in R.sample(demo_clients, min(90, len(demo_clients))):
                catalog.update_client(s, da, c.id, {"notes": R.choice(NOTES) + " (демо)"})
                c.reminders = R.random() < 0.7
            phones = [(c.phone, c.name) for c in demo_clients]

        clock.set_now(None)
        today = clock.now().date()
        with db.session() as s:
            schedule.set_day(s, da, fid, today + timedelta(days=9), off=True)
        for k in range(1, future_days + 1):
            dday = today + timedelta(days=k)
            if dday.weekday() >= 5 or k == 9:
                continue
            t = datetime.combine(dday, datetime.min.time()).replace(hour=9)
            with db.session() as s:
                while True:
                    sid = R.choices(list(svc), weights=[v[2] for v in svc.values()])[0]
                    dur = svc[sid][0]
                    if t + timedelta(minutes=dur) > t.replace(hour=19):
                        break
                    if R.random() < (0.7 if k <= 7 else 0.35):
                        ph, nm = R.choice(phones)
                        bookings.create_appointment(s, da, master_id=fid, service_id=sid, starts_at=t, client_name=nm,
                                                    client_phone=ph, status="pending" if R.random() < 0.15 else "confirmed",
                                                    confirm_off_hours=True)
                        st["future"] += 1
                    t += timedelta(minutes=dur + 30)
    finally:
        notifications.enqueue = real_enqueue
        clock.set_now(None)
    st["income_by_year"] = {y: v // 100 for y, v in st["income_by_year"].items()}
    log.info("Демо «ФОП 1 група» готово: %s", st)
    return st
