"""POS-термінал ПриватБанку (ECR JSON по TCP): макет термінала на localhost, без реальної мережі."""
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mds import clock
from mds.api.app import create_app
from mds.config import Config
from mds.errors import Conflict, DomainError, Forbidden, ValidationError
from mds.models import AuditLog, CardPayment, Order
from mds.security import create_token
from mds.services import bookings, card_terminal, terminal


class MockTerminal:
    """Макет термінала: приймає JSON із \\x00 у кінці й відповідає за сценарієм."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.mode = "approve"
        self.delay = 0.0
        self.received: list[dict] = []
        self.interrupted = threading.Event()
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            buf = b""
            while b"\x00" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            msg = json.loads(buf.split(b"\x00", 1)[0])
            self.received.append(msg)
            m = msg.get("method")
            if m == "ServiceMessage":
                self.interrupted.set()
                conn.sendall(json.dumps({"method": "ServiceMessage", "step": 0, "error": False}).encode() + b"\x00")
                return
            if m == "PingDevice":
                if self.mode != "down":
                    conn.sendall(json.dumps({"method": "PingDevice", "step": 0, "params": {"responseCode": "0000"},
                                             "error": False}).encode() + b"\x00")
                return
            if self.mode == "hang":
                self.interrupted.wait(10)
                if self.interrupted.is_set():
                    self._reply(conn, "1001", "", error=True)
                return
            time.sleep(self.delay)
            conn.sendall(json.dumps({"method": "ServiceMessage", "step": 0, "params": {"msgType": "insertCard"}}).encode() + b"\x00")
            if self.mode == "approve":
                self._reply(conn, "0000", "014722766522")
            elif self.mode == "decline":
                self._reply(conn, "05", "")
            elif self.mode == "garbage":
                conn.sendall(b"not json\x00")

    @staticmethod
    def _reply(conn, code, rrn, error=False):
        conn.sendall(json.dumps({"method": "Purchase", "step": 0, "error": error, "errorDescription": "",
                                 "params": {"responseCode": code, "rrn": rrn, "approvalCode": "131192" if rrn else "",
                                            "pan": "4149********1451", "paymentSystem": "VISA", "amount": "500.00"}},
                                ensure_ascii=False).encode() + b"\x00")

    def close(self):
        self._stop = True
        self.interrupted.set()
        self.sock.close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def mock_term():
    t = MockTerminal()
    yield t
    t.close()


@pytest.fixture
def setup(db, seed, mock_term, monkeypatch):
    monkeypatch.setenv("MDS_ENV", "test")
    d = seed["actors"]["director"]
    with db.session() as s:
        terminal.update_settings(s, d, enabled=True, ip="127.0.0.1", port=mock_term.port)
    clock.set_now(datetime(2026, 11, 5, 9))
    with db.session() as s:
        aid = bookings.create_appointment(s, d, master_id=seed["m1"], service_id=seed["haircut"],
                                          starts_at=datetime(2026, 11, 5, 10), client_name="Оля", client_phone="0671112233").id
    clock.set_now(datetime(2026, 11, 5, 11))
    yield aid
    clock.set_now(None)


def _start(db, actor, aid, amount=50000):
    with db.session() as s:
        return card_terminal.start_payment(s, actor, aid, amount=amount, client_name="Оля Коваль")


def _orders(db):
    with db.session() as s:
        return s.scalars(select(Order)).all()


# Клієнт і формат

def test_amount_and_purchase_message():
    assert terminal.amount_str(50000) == "500.00" and terminal.amount_str(65050) == "650.50" and terminal.amount_str(7) == "0.07"
    assert terminal.encode(terminal.build_purchase(12345)).endswith(b"\x00")
    assert terminal.build_purchase(12345)["params"]["amount"] == "123.45"


def test_client_success(mock_term):
    r = terminal.PrivatBankTerminalClient("127.0.0.1", mock_term.port).process_payment(50000, "test")
    assert r["success"] is True and r["rrn"] == "014722766522" and r["approval_code"] == "131192"
    assert mock_term.received[0] == {"method": "Purchase", "step": 0,
                                     "params": {"amount": "500.00", "discount": "", "merchantId": "0", "facepay": "false"}}


def test_client_declined(mock_term):
    mock_term.mode = "decline"
    r = terminal.PrivatBankTerminalClient("127.0.0.1", mock_term.port).process_payment(50000, "test")
    assert r == {"success": False, "response_code": "05", "error": "Термінал відхилив оплату (код 05)", "detail": "05",
                 "uncertain": False}


def test_client_timeout_is_uncertain(mock_term):
    mock_term.mode = "hang"
    r = terminal.PrivatBankTerminalClient("127.0.0.1", mock_term.port, timeout=0.5).process_payment(50000, "test")
    assert r == {"success": False, "error": "Таймаут з'єднання з терміналом", "uncertain": True}


def test_client_no_connection_is_not_uncertain():
    r = terminal.PrivatBankTerminalClient("127.0.0.1", _free_port(), connect_timeout=1).process_payment(50000, "test")
    assert r == {"success": False, "error": "Немає з'єднання з терміналом", "uncertain": False}


def test_client_garbage_reply(mock_term):
    mock_term.mode = "garbage"
    r = terminal.PrivatBankTerminalClient("127.0.0.1", mock_term.port).process_payment(50000, "test")
    assert r["success"] is False and r["error"] == "Термінал надіслав незрозумілу відповідь" and r["uncertain"] is True


def test_ping(mock_term):
    assert terminal.check_connection("127.0.0.1", mock_term.port)["ok"] is True
    mock_term.mode = "down"
    assert terminal.check_connection("127.0.0.1", mock_term.port) == {"ok": False, "error": "Немає з'єднання з терміналом"}


# Налаштування

def test_settings_director_only_and_validated(db, seed, monkeypatch):
    monkeypatch.setenv("MDS_ENV", "test")
    d, m1 = seed["actors"]["director"], seed["actors"]["m1"]
    with db.session() as s:
        assert terminal.get_settings(s, d) == {"enabled": False, "ip": "", "port": 2000}
        with pytest.raises(Forbidden):
            terminal.get_settings(s, m1)
        with pytest.raises(Forbidden):
            terminal.update_settings(s, m1, enabled=False)
        for bad in ({"ip": "8.8.8.8"}, {"ip": "abc"}, {"port": 0}, {"port": 70000}, {"enabled": True}):
            with pytest.raises(ValidationError):
                terminal.update_settings(s, d, **bad)
        assert terminal.update_settings(s, d, enabled=True, ip="192.168.1.50", port=2001) == \
            {"enabled": True, "ip": "192.168.1.50", "port": 2001}
        assert s.scalar(select(AuditLog).where(AuditLog.entity == "pos_terminal")) is not None
        assert card_terminal.mode(s) == "ecr"
        from mds.services.common import update_settings
        with pytest.raises(ValidationError):
            update_settings(s, d, {"pos_terminal_ip": "8.8.8.8"})


def test_loopback_rejected_in_production(db, seed, monkeypatch):
    monkeypatch.setenv("MDS_ENV", "production")
    with db.session() as s, pytest.raises(ValidationError):
        terminal.update_settings(s, seed["actors"]["director"], ip="127.0.0.1")


# Оплата і закриття замовлення

def test_pay_success_closes_order(db, seed, setup, mock_term):
    m1 = seed["actors"]["m1"]
    p = _start(db, m1, setup)
    assert p["mode"] == "ecr" and p["status"] == "pending" and _orders(db) == []
    r = card_terminal.ecr_pay(db, m1, p["id"])
    assert r["status"] == "paid" and r["rrn"] == "014722766522" and r["order_id"]
    [o] = _orders(db)
    assert (o.payment_method, o.amount, o.status, o.idempotency_key) == ("card", 50000, "completed", f"card-{p['id']}")
    assert card_terminal.ecr_pay(db, m1, p["id"])["status"] == "paid"
    assert len(_orders(db)) == 1 and len(mock_term.received) == 1


def test_pay_declined_returns_terminal_error(db, seed, setup, mock_term):
    mock_term.mode = "decline"
    m1 = seed["actors"]["m1"]
    p = _start(db, m1, setup)
    r = card_terminal.ecr_pay(db, m1, p["id"])
    assert r["status"] == "failed" and r["error"] == "Термінал відхилив оплату (код 05)" and _orders(db) == []
    mock_term.mode = "approve"
    p2 = _start(db, m1, setup)
    assert p2["id"] != p["id"] and card_terminal.ecr_pay(db, m1, p2["id"])["status"] == "paid"


def test_pay_timeout_is_unknown_and_blocks_new_payment(db, seed, setup, mock_term, monkeypatch):
    monkeypatch.setattr(terminal, "PAY_TIMEOUT", 0.5)
    mock_term.mode = "hang"
    m1 = seed["actors"]["m1"]
    orig = terminal.PrivatBankTerminalClient.__init__
    monkeypatch.setattr(terminal.PrivatBankTerminalClient, "__init__",
                        lambda self, h, p, **kw: orig(self, h, p, **{**kw, "timeout": 0.5}))
    p = _start(db, m1, setup)
    r = card_terminal.ecr_pay(db, m1, p["id"])
    assert r["status"] == "unknown" and r["error"] == "Таймаут з'єднання з терміналом" and _orders(db) == []
    with pytest.raises(Conflict):
        _start(db, m1, setup)
    with db.session() as s:
        assert card_terminal.cancel_payment(s, m1, p["id"])["status"] == "failed"
    assert _start(db, m1, setup)["status"] == "pending"


def test_disabled_returns_400(db, seed, setup):
    m1, d = seed["actors"]["m1"], seed["actors"]["director"]
    p = _start(db, m1, setup)
    with db.session() as s:
        terminal.update_settings(s, d, enabled=False)
    with pytest.raises(DomainError) as e:
        card_terminal.ecr_pay(db, m1, p["id"])
    assert e.value.status == 400 and e.value.code == "terminal_disabled"


def test_concurrent_pay_blocked(db, seed, setup, mock_term):
    mock_term.delay = 0.8
    m1 = seed["actors"]["m1"]
    p = _start(db, m1, setup)

    def go():
        try:
            return card_terminal.ecr_pay(db, m1, p["id"])["status"]
        except Conflict as e:
            return e.code

    with ThreadPoolExecutor(2) as ex:
        results = sorted(ex.map(lambda _: go(), range(2)))
    assert results == ["paid", "payment_in_progress"]
    assert len(_orders(db)) == 1 and len([m for m in mock_term.received if m["method"] == "Purchase"]) == 1


def test_cancel_while_processing_interrupts_terminal(db, seed, setup, mock_term):
    mock_term.mode = "hang"
    m1 = seed["actors"]["m1"]
    p = _start(db, m1, setup)
    with ThreadPoolExecutor(1) as ex:
        fut = ex.submit(card_terminal.ecr_pay, db, m1, p["id"])
        for _ in range(50):
            with db.session() as s:
                if s.get(CardPayment, p["id"]).status == "processing":
                    break
            time.sleep(0.05)
        with db.session() as s:
            r = card_terminal.cancel_payment(s, m1, p["id"])
        assert r["status"] == "processing" and "Скасування надіслано" in r["message"]
        assert mock_term.interrupted.wait(2)
        res = fut.result(5)
    assert res["status"] == "failed" and res["error"] == "Термінал відхилив оплату (код 1001)" and _orders(db) == []


def test_cancel_pending_and_other_master_forbidden(db, seed, setup):
    p = _start(db, seed["actors"]["m1"], setup)
    with pytest.raises(Forbidden):
        card_terminal.ecr_pay(db, seed["actors"]["m2"], p["id"])
    with db.session() as s:
        assert card_terminal.cancel_payment(s, seed["actors"]["m1"], p["id"])["status"] == "failed"


def test_close_failure_keeps_paid_payment(db, seed, setup, mock_term):
    m1 = seed["actors"]["m1"]
    p = _start(db, m1, setup)
    orig = card_terminal.orders.close_order

    def boom(*a, **k):
        raise Conflict("Запис змінено")

    card_terminal.orders.close_order = boom
    try:
        r = card_terminal.ecr_pay(db, m1, p["id"])
    finally:
        card_terminal.orders.close_order = orig
    assert r["status"] == "paid" and r["order_id"] is None and "RRN 014722766522" in r["error"]
    with db.session() as s:
        assert s.get(CardPayment, p["id"]).rrn == "014722766522"


# API

def _client(db, tmp_path):
    cfg = Config(data_dir=tmp_path / "data", database_url=db.url, secret_key="test-secret-key-for-unit-tests-only-0123456789")
    return TestClient(create_app(cfg, db)), cfg


def _h(cfg, db, username):
    from mds.models import User
    with db.session() as s:
        u = s.scalar(select(User).where(User.username == username))
        return {"Authorization": "Bearer " + create_token(cfg.secret_key, u.id, u.token_version, 12)}


def test_api_flow(db, seed, setup, mock_term, tmp_path):
    c, cfg = _client(db, tmp_path)
    with c:
        hd, hm = _h(cfg, db, "director"), _h(cfg, db, "olena")
        assert c.get("/api/settings/terminal", headers=hm).status_code == 403
        assert c.get("/api/settings/terminal", headers=hd).json() == {"enabled": True, "ip": "127.0.0.1", "port": mock_term.port}
        assert c.post("/api/settings/terminal/check", headers=hm).status_code == 403
        ck = c.post("/api/settings/terminal/check", headers=hd).json()
        assert ck["ok"] is True and ck["port"] == mock_term.port
        assert c.get("/api/card-terminal/status", headers=hm).json()["mode"] == "ecr"
        p = c.post("/api/card-payments", json={"appointment_id": setup, "amount": 50000, "client_name": "Оля Коваль"}, headers=hm).json()
        r = c.post(f"/api/card-payments/{p['id']}/pay", headers=hm)
        assert r.status_code == 200 and r.json()["status"] == "paid"
        assert c.post(f"/api/card-payments/{p['id']}/pay").status_code == 401
        assert c.patch("/api/settings/terminal", json={"enabled": False}, headers=hd).json()["enabled"] is False
        assert c.get("/api/card-terminal/status", headers=hm).json()["mode"] == "off"


def test_api_disabled_400(db, seed, setup, tmp_path):
    c, cfg = _client(db, tmp_path)
    with c:
        hd, hm = _h(cfg, db, "director"), _h(cfg, db, "olena")
        p = c.post("/api/card-payments", json={"appointment_id": setup, "amount": 50000}, headers=hm).json()
        c.patch("/api/settings/terminal", json={"enabled": False}, headers=hd)
        r = c.post(f"/api/card-payments/{p['id']}/pay", headers=hm)
        assert r.status_code == 400 and r.json()["error"] == "terminal_disabled"
