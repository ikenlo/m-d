"""Повний робочий день салону з фіскалізацією (3 і 5 майстрів) + контроль цілісності фінансів.

FakePRROProvider; збої ПРРО випадкові (фіксоване зерно), закриття замовлень паралельне.
Перевіряємо: немає подвійних чеків, втрачених замовлень і подвійного нарахування; дохід, готівка, картки,
чеки, повернення, частки майстра/салону, виплати, борги, склад — збігаються; історичні записи не змінились.
"""

import random
import threading
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from mds import clock
from mds.errors import Conflict
from mds.models import FiscalReceipt, LedgerEntry, Material, Order, Payout, Role
from mds.services import analytics, bookings, fiscal, orders, users
from mds.services.common import Actor
from mds.services.fiscal_providers import FakePRROProvider, set_provider

DAY = date(2026, 11, 3)


def snapshot(db):
    """Контрольні показники фінансів (для порівняння до/після)."""
    with db.session() as s:
        od = s.execute(select(func.count(Order.id), func.coalesce(func.sum(Order.amount), 0),
                              func.coalesce(func.sum(Order.master_earning), 0), func.coalesce(func.sum(Order.salon_earning), 0),
                              func.coalesce(func.sum(Order.materials_cost), 0))).one()
        by_pm = dict(s.execute(select(Order.payment_method, func.sum(Order.amount)).group_by(Order.payment_method)).all())
        ledger_rows = [(e.id, e.kind, e.amount, e.master_amount, e.salon_amount) for e in s.scalars(select(LedgerEntry).order_by(LedgerEntry.id))]
        paid = int(s.scalar(select(func.coalesce(func.sum(Payout.amount), 0))) or 0)
        stock = {m.id: m.stock_milli for m in s.scalars(select(Material))}
    return {"orders": tuple(od), "by_pm": by_pm, "ledger": ledger_rows, "paid": paid, "stock": stock}



@pytest.mark.parametrize("n_masters", [3, 5])
def test_full_workday_with_fiscalization(db, seed, n_masters):
    rnd = random.Random(n_masters)
    d = seed["actors"]["director"]
    from mds.models import User

    # --- історичні дані до запуску ПРРО (не повинні змінитись)
    clock.set_now(datetime(2026, 11, 2, 9))
    with db.session() as s:
        a = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"], starts_at=datetime(2026, 11, 2, 10),
                                        client_name="Історичний", client_phone="0500000001")
        hist_id = a.id
    clock.set_now(datetime(2026, 11, 2, 11))
    with db.session() as s:
        orders.close_order(s, seed["actors"]["m1"], hist_id, amount=50000, payment_method="cash",
                           materials=[{"material_id": seed["gel"], "qty": 1}])
    before = snapshot(db)

    fp = FakePRROProvider(auto_shift=False)
    set_provider(fp)
    try:
        with db.session() as s:
            fiscal.update_config(s, d, {"enabled": True})
            ids = [seed["m1"], seed["m2"]]
            for i in range(n_masters - 2):
                ids.append(users.create_user(s, Actor.system(), username=f"mx{i}", password="Master123",
                                             full_name=f"Майстер {i + 3}", role=Role.MASTER).id)
            actors = {mid: Actor.of(s.get(User, mid)) for mid in ids}
        clock.set_now(datetime(2026, 11, 3, 8, 30))
        fiscal.open_shift(db, d, provider=fp)

        # --- записи на день: по 4 клієнти на майстра, 1 скасування на майстра
        plan = []
        with db.session() as s:
            for k, mid in enumerate(ids):
                for j in range(5):
                    st = datetime(2026, 11, 3, 9 + 2 * j, 0)
                    a = bookings.create_appointment(s, d, master_id=mid, service_id=seed["haircut"], starts_at=st,
                                                    client_name=f"К{k}-{j}", client_phone=f"06{k}{j:02d}{mid:05d}")
                    plan.append((mid, a.id, j))
            for _mid, aid, j in plan:
                if j == 4:
                    bookings.set_status(s, d, aid, "cancelled", "клієнт передумав")
        to_close = [(mid, aid) for mid, aid, j in plan if j < 4]
        amounts = {aid: rnd.choice([30000, 45000, 60000, 75000]) for _, aid in to_close}
        pms = {aid: rnd.choice(["cash", "card"]) for _, aid in to_close}

        # --- нестабільний ПРРО: випадкові збої під час дня
        fp.fail_next = [rnd.choice([None, None, None, "unavailable", "lost", "pending"]) for _ in range(len(to_close) * 2)]
        fp.fail_next = [x for x in fp.fail_next if x]

        # --- паралельне закриття замовлень усіма майстрами (+ подвійні натискання)
        clock.set_now(datetime(2026, 11, 3, 18, 0))
        errors = []

        def master_work(mid):
            for m2, aid in to_close:
                if m2 != mid:
                    continue
                for _attempt in range(2):  # подвійне натискання: той самий ключ
                    try:
                        with db.session() as s:
                            orders.close_order(s, actors[mid], aid, amount=amounts[aid], payment_method=pms[aid],
                                               idempotency_key=f"tap-{aid}")
                    except Conflict:
                        pass
                    except Exception as e:  # noqa: BLE001
                        errors.append(repr(e))
                fiscal.process_pending(db, fp)  # паралельна обробка черги з кількох потоків

        ts = [threading.Thread(target=master_work, args=(mid,)) for mid in ids]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errors

        # --- «перезапуск» і дообробка черги, поки все не фіскалізовано
        for _ in range(40):
            fp.complete_pending()
            clock.set_now(clock.now() + timedelta(minutes=16))
            fiscal.process_pending(db, fp)
            with db.session() as s:
                if not s.scalar(select(func.count()).select_from(FiscalReceipt).where(FiscalReceipt.status != "done")):
                    break

        # --- повернення одному клієнту (часткове)
        refund_aid = to_close[0][1]
        with db.session() as s:
            refund_order = s.scalar(select(Order).where(Order.appointment_id == refund_aid))
            refund_order_id, refund_pm = refund_order.id, refund_order.payment_method
        fiscal.refund(db, d, refund_order_id, 10000, "частковий перероблений колір", provider=fp)

        # --- закриття змін майстрів (каса, виплати) і фіскальної зміни (Z)
        with db.session() as s:
            for mid in ids:
                orders.close_shift(s, actors[mid], mid)
        with db.session() as s:
            for sh in orders.list_shifts(s, d, status="closed"):
                if sh.payout_due:
                    orders.confirm_payout(s, d, master_id=sh.master_id, amount=sh.payout_due, shift_id=sh.id, method="card")
        z = fiscal.close_shift(db, d, provider=fp)["z_report"]

        # =========================== КОНТРОЛЬ ЦІЛІСНОСТІ
        expected_total = sum(amounts.values()) - 10000
        with db.session() as s:
            day_orders = list(s.scalars(select(Order).where(Order.closed_at >= datetime(2026, 11, 3))))
            assert len(day_orders) == len(to_close)  # жодного втраченого чи подвійного замовлення
            assert sum(o.amount for o in day_orders) == expected_total
            for o in day_orders:
                assert o.amount == o.master_earning + o.materials_cost + o.salon_earning
            receipts = list(s.scalars(select(FiscalReceipt)))
            sells = [r for r in receipts if r.kind == "sell"]
            rets = [r for r in receipts if r.kind == "return"]
            assert len(sells) == len(to_close) and len({r.order_id for r in sells}) == len(sells)  # 1 чек на замовлення
            assert all(r.status == "done" and r.fiscal_code for r in receipts)
            assert len(rets) == 1 and rets[0].amount == 10000
            # у ПРРО рівно стільки чеків, скільки в нас — без дублікатів
            assert len(fp.state.receipts) == len(receipts)
            # ПРРО (Z-звіт) = внутрішній облік
            cash_int = sum(amounts[a] for a in amounts if pms[a] == "cash") - (10000 if refund_pm == "cash" else 0)
            card_int = sum(amounts[a] for a in amounts if pms[a] == "card") - (10000 if refund_pm != "cash" else 0)
            assert z["sales"] == sum(amounts.values()) and z["returns"] == 10000
            assert (z["cash"], z["card"]) == (cash_int, card_int)
            assert z["sell_count"] == len(to_close) and z["return_count"] == 1
            rec = fiscal.reconcile(s, d, DAY, DAY, provider=fp)
            assert rec["ok"], rec["issues"][:3]
            assert rec["totals"]["fiscal_net"] == expected_total
            # фінансовий журнал = замовлення (з урахуванням сторно повернення)
            lg = s.execute(select(func.sum(LedgerEntry.amount), func.sum(LedgerEntry.master_amount), func.sum(LedgerEntry.salon_amount))
                           .where(LedgerEntry.kind.in_(("order_closed", "order_reversal", "order_corrected")))).one()
            od = s.execute(select(func.sum(Order.amount), func.sum(Order.master_earning), func.sum(Order.salon_earning))).one()
            assert tuple(lg) == tuple(od)
            # звіт директора за день
            dash = analytics.dashboard(s, d, DAY, DAY)
            assert dash["revenue"] == expected_total and dash["orders_count"] == len(to_close)
            assert dash["cancellations"] == len(ids)
            # виплати: кожен майстер отримав рівно нараховане за день (каса + картка), боргу немає
            # (історичне замовлення 2.11 було в тій самій відкритій зміні m1 — виплачене разом із нею)
            for mid in ids:
                bal = orders.master_balance(s, mid)
                assert bal["balance"] == 0 and bal["accrued"] == bal["paid"], (mid, bal)
        after = snapshot(db)
        # історичні записи не змінились (перші рядки журналу ті самі)
        assert after["ledger"][:len(before["ledger"])] == before["ledger"]
        assert after["stock"] == before["stock"]  # стрижки без матеріалів — склад не змінився
    finally:
        set_provider(None)


def test_parallel_close_same_order_one_receipt(db, seed):
    fp = FakePRROProvider(auto_shift=True)
    set_provider(fp)
    try:
        d = seed["actors"]["director"]
        with db.session() as s:
            fiscal.update_config(s, d, {"enabled": True})
        clock.set_now(datetime(2026, 11, 2, 9))
        with db.session() as s:
            aid = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"],
                                              starts_at=datetime(2026, 11, 2, 10), client_name="К", client_phone="0501112233").id
        clock.set_now(datetime(2026, 11, 2, 11))
        barrier = threading.Barrier(5)
        res = []

        def tap(i):
            barrier.wait()
            try:
                with db.session() as s:
                    orders.close_order(s, seed["actors"]["m1"], aid, amount=60000, payment_method="cash", idempotency_key=f"k{i}")
                res.append("ok")
            except Conflict:
                res.append("conflict")

        ts = [threading.Thread(target=tap, args=(i,)) for i in range(5)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert res.count("ok") == 1
        for _ in range(3):
            fiscal.process_pending(db, fp)
        with db.session() as s:
            assert s.scalar(select(func.count()).select_from(Order)) == 1
            assert s.scalar(select(func.count()).select_from(FiscalReceipt)) == 1
        assert len(fp.state.receipts) == 1
    finally:
        set_provider(None)


def test_db_error_during_close_does_not_leave_receipt(db, seed, monkeypatch):
    """Помилка БД під час закриття замовлення: транзакція відкочується — немає ні замовлення, ні чека."""
    d = seed["actors"]["director"]
    with db.session() as s:
        fiscal.update_config(s, d, {"enabled": True})
    clock.set_now(datetime(2026, 11, 2, 9))
    with db.session() as s:
        aid = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"],
                                          starts_at=datetime(2026, 11, 2, 10), client_name="К", client_phone="0501112233").id
    clock.set_now(datetime(2026, 11, 2, 11))

    def boom(*a, **k):
        raise RuntimeError("диск переповнено")

    monkeypatch.setattr(fiscal, "audit", boom)
    with pytest.raises(RuntimeError):
        with db.session() as s:
            orders.close_order(s, seed["actors"]["m1"], aid, amount=60000, payment_method="cash")
    monkeypatch.undo()
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Order)) == 0
        assert s.scalar(select(func.count()).select_from(FiscalReceipt)) == 0
        assert s.get(__import__("mds.models", fromlist=["Appointment"]).Appointment, aid).status != "completed"
        # повтор після усунення збою — успішно
        orders.close_order(s, seed["actors"]["m1"], aid, amount=60000, payment_method="cash")
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(FiscalReceipt)) == 1
