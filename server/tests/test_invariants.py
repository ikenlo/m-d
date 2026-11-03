"""Фінансові інваріанти на випадкових даних (фіксоване зерно — відтворювано).

Перевіряє: amount == master + materials + salon для кожного замовлення; журнал (ledger)
після сторно/виправлень дає ті самі суми, що й замовлення; дашборд і баланс майстрів
узгоджені з замовленнями та виплатами.
"""

import random
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from mds import clock
from mds.models import LedgerEntry, Order, Payout
from mds.services import analytics, bookings, orders, users
from mds.services.finance import compute_split


def test_compute_split_invariant_random():
    rnd = random.Random(1)
    for _ in range(5000):
        amount = rnd.randint(0, 5_000_000)
        materials = rnd.randint(0, amount)
        share = rnd.randint(0, 10000)
        for mode in ("salon", "master", "split"):
            sp = compute_split(amount, materials, share, mode)
            assert sp.amount == sp.master + sp.materials + sp.salon
            assert all(isinstance(x, int) for x in (sp.master, sp.salon, sp.materials))


def test_ledger_orders_dashboard_consistent(db, seed):
    rnd = random.Random(7)
    d = seed["actors"]["director"]
    with db.session() as s:
        users.update_user(s, d, seed["m2"], {"materials_mode": "split"})
    order_ids = []
    day = datetime(2026, 11, 2, 9, 0)
    for i in range(40):
        master = "m1" if i % 2 else "m2"
        with db.session() as s:
            start = day + timedelta(days=i // 8, hours=(i % 8))
            clock.set_now(start - timedelta(minutes=5))
            a = bookings.create_appointment(s, d, master_id=seed[master], service_id=seed["haircut"], starts_at=start,
                                            client_name=f"Клієнт {i}", client_phone=f"067{1000000 + i}")
            mats = [{"material_id": seed["gel"], "qty": rnd.randint(1, 3)}] if rnd.random() < 0.5 else []
            o = orders.close_order(s, seed["actors"][master], a.id, amount=rnd.randint(1, 3000) * 100,
                                   payment_method=rnd.choice(["cash", "card"]), materials=mats)
            order_ids.append(o.id)
        if i % 8 == 7:  # кінець дня — закриваємо зміни
            with db.session() as s:
                for m in ("m1", "m2"):
                    if orders.current_shift(s, seed[m]):
                        orders.close_shift(s, seed["actors"][m], seed[m])
    # виправлення частини замовлень директором
    with db.session() as s:
        for oid in rnd.sample(order_ids, 10):
            orders.correct_order(s, d, oid, {"amount": rnd.randint(1, 4000) * 100,
                                             "materials": [{"material_id": seed["gel"], "qty": q} for q in [rnd.randint(0, 2)] if q]},
                                 "перевірка")
        orders.confirm_payout(s, d, master_id=seed["m1"], amount=12345, method="card")

    with db.session() as s:
        for o in s.scalars(select(Order)):
            assert o.amount == o.master_earning + o.materials_cost + o.salon_earning
        kinds = ("order_closed", "order_reversal", "order_corrected")
        lg = s.execute(select(func.sum(LedgerEntry.amount), func.sum(LedgerEntry.master_amount),
                              func.sum(LedgerEntry.salon_amount), func.sum(LedgerEntry.materials_amount))
                       .where(LedgerEntry.kind.in_(kinds))).one()
        od = s.execute(select(func.sum(Order.amount), func.sum(Order.master_earning),
                              func.sum(Order.salon_earning), func.sum(Order.materials_cost))).one()
        assert tuple(lg) == tuple(od)

        dash = analytics.dashboard(s, d, date(2026, 11, 1), date(2026, 11, 30))
        assert dash["revenue"] == od[0] and dash["masters_share"] == od[1] and dash["materials"] == od[3]
        assert dash["salon_net_from_orders"] == od[2]
        assert dash["gross_profit"] == od[0] - od[1] - od[3]
        paid = s.scalar(select(func.sum(Payout.amount)))
        assert dash["salary_debt_total"] == od[1] - paid
        for m in ("m1", "m2"):
            bal = orders.master_balance(s, seed[m])
            acc = s.scalar(select(func.sum(Order.master_earning)).where(Order.master_id == seed[m]))
            assert bal["accrued"] == acc and bal["balance"] == acc - bal["paid"]
