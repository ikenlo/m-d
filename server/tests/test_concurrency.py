import threading
from datetime import datetime

from mds.errors import Conflict
from mds.models import Appointment, Order
from mds.services import bookings, orders


def _race(n, fn):
    barrier = threading.Barrier(n)
    results = []

    def worker(i):
        barrier.wait()
        try:
            fn(i)
            results.append("ok")
        except Conflict:
            results.append("conflict")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_parallel_booking_same_slot(db, seed):
    adm = seed["actors"]["admin"]

    def book(i):
        with db.session() as s:
            bookings.create_appointment(
                s, adm, master_id=seed["m1"], service_id=seed["haircut"], starts_at=datetime(2026, 11, 2, 10),
                client_name=f"К{i}", client_phone=f"050000000{i}",
            )

    results = _race(5, book)
    assert results.count("ok") == 1 and results.count("conflict") == 4
    with db.session() as s:
        assert s.query(Appointment).count() == 1


def test_parallel_close_order(db, seed):
    m1 = seed["actors"]["m1"]
    with db.session() as s:
        a = bookings.create_appointment(
            s, seed["actors"]["admin"], master_id=seed["m1"], service_id=seed["haircut"],
            starts_at=datetime(2026, 11, 2, 10), client_name="К", client_phone="0500000000",
        )

    def close(i):
        with db.session() as s:
            orders.close_order(s, m1, a.id, amount=50000, payment_method="cash", idempotency_key=f"key-{i}")

    results = _race(5, close)
    assert results.count("ok") == 1
    with db.session() as s:
        assert s.query(Order).count() == 1
