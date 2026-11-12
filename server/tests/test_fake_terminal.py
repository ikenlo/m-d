"""Емулятор термінала (scripts/fake_terminal.py) сумісний із клієнтом Connect (services/terminal.py)."""
import importlib.util
import threading
import time
from pathlib import Path

import pytest

from mds.services import terminal

SPEC = importlib.util.spec_from_file_location("fake_terminal", Path(__file__).resolve().parents[1] / "scripts" / "fake_terminal.py")
fake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fake)


@pytest.fixture
def emu():
    servers = []

    def start(mode, **kw):
        srv = fake.make_server("127.0.0.1", 0, mode, **{"approve_delay": 0.1, "decline_delay": 0.1, "timeout_delay": 3, **kw})
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv, terminal.PrivatBankTerminalClient("127.0.0.1", srv.server_address[1], timeout=1.0)

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def test_approve(emu):
    srv, c = emu("approve")
    r = c.process_payment(50000, "t")
    assert r["success"] is True and len(r["rrn"]) == 12 and r["rrn"].isdigit() and r["response_code"] == "0000"
    assert srv.state.received == ["Purchase"]


def test_decline(emu):
    _, c = emu("decline")
    r = c.process_payment(50000, "t")
    assert r["success"] is False and r["error"] == "Термінал відхилив оплату (код 05)" and r["uncertain"] is False


def test_timeout_is_unknown(emu):
    _, c = emu("timeout")
    t0 = time.monotonic()
    r = c.process_payment(50000, "t")
    assert r == {"success": False, "error": "Таймаут з'єднання з терміналом", "uncertain": True}
    assert time.monotonic() - t0 < 2.5


def test_by_amount(emu):
    _, c = emu("by-amount")
    assert c.process_payment(10000, "t")["success"] is True
    assert c.process_payment(10005, "t")["response_code"] == "05"
    assert c.process_payment(10090, "t")["uncertain"] is True


def test_ping_and_interrupt(emu):
    srv, c = emu("timeout", timeout_delay=5)
    assert c.ping()["ok"] is True
    out = {}
    th = threading.Thread(target=lambda: out.update(r=terminal.PrivatBankTerminalClient(
        "127.0.0.1", srv.server_address[1], timeout=4).process_payment(50000, "t")))
    th.start()
    time.sleep(0.3)
    assert c.interrupt() is True
    th.join(3)
    assert out["r"]["success"] is False and out["r"]["response_code"] == "1001"
    assert srv.state.received == ["PingDevice", "Purchase", "ServiceMessage"]


def test_void_and_unknown_method(emu):
    srv, c = emu("approve")
    assert c._exchange({"method": "Void", "step": 0}, timeout=1)["params"]["responseCode"] == "0000"
    assert c._exchange({"method": "Refund", "step": 0}, timeout=1)["error"] is True


def test_bad_mode():
    with pytest.raises(ValueError):
        fake.make_server("127.0.0.1", 0, "nope")
