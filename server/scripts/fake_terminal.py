#!/usr/bin/env python3
"""Емулятор POS-термінала ПриватБанку (ECR JSON по TCP) для перевірки без справжнього термінала.

Лише стандартна бібліотека, від коду Connect не залежить. Протокол — як у mds/services/terminal.py
і docs/TERMINAL.md: JSON у UTF-8 з байтом \\x00 у кінці.

    python scripts/fake_terminal.py                     # 0.0.0.0:2000, режим approve
    python scripts/fake_terminal.py --port 2001 --mode decline
    python scripts/fake_terminal.py --mode random       # approve / decline / timeout навмання
    python scripts/fake_terminal.py --mode by-amount    # копійки .05 → відмова, .90 → таймаут, інше → схвалено

Режими для Purchase:
    approve  — через --approve-delay (3 с) код 0000 і випадковий RRN;
    decline  — через --decline-delay (2 с) код 05 (недостатньо коштів);
    timeout  — мовчить --timeout-delay (90 с), щоб спрацював 60-секундний таймаут Connect.
PingDevice, Void і ServiceMessage/interrupt — одразу успіх; interrupt перериває Purchase, що очікує (код 1001).
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import socketserver
import threading

log = logging.getLogger("fake_terminal")
MODES = ("approve", "decline", "timeout", "random", "by-amount")


class State:
    def __init__(self, mode: str, approve_delay: float, decline_delay: float, timeout_delay: float):
        self.mode, self.approve_delay, self.decline_delay, self.timeout_delay = mode, approve_delay, decline_delay, timeout_delay
        self.interrupt = threading.Event()
        self.lock = threading.Lock()
        self.received: list[str] = []

    def outcome(self, amount: str) -> str:
        if self.mode == "random":
            return random.choice(("approve", "decline", "timeout"))
        if self.mode == "by-amount":
            cents = amount.rsplit(".", 1)[-1] if "." in amount else "00"
            return {"05": "decline", "90": "timeout"}.get(cents, "approve")
        return self.mode


def reply(method: str, *, error: bool = False, description: str = "", **params) -> bytes:
    msg = {"method": method, "step": 0, "error": error, "errorDescription": description, "params": params}
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\x00"


def purchase_result(outcome: str, amount: str) -> bytes:
    if outcome == "approve":
        rrn = "".join(random.choice("0123456789") for _ in range(12))
        return reply("Purchase", responseCode="0000", rrn=rrn, approvalCode=f"{random.randint(0, 999999):06d}",
                     pan="4149********1451", paymentSystem="VISA", issuerName="VISA", amount=amount,
                     terminalId="FAKE0001", merchant="M&D TEST")
    return reply("Purchase", responseCode="05", rrn="", amount=amount, description="Недостатньо коштів")


class Handler(socketserver.BaseRequestHandler):
    def _read(self) -> dict | None:
        buf = b""
        while b"\x00" not in buf:
            chunk = self.request.recv(4096)
            if not chunk:
                return None
            buf += chunk
            if len(buf) > 64 * 1024:
                return None
        try:
            return json.loads(buf.split(b"\x00", 1)[0].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _send(self, data: bytes) -> None:
        try:
            self.request.sendall(data)
        except OSError:
            pass

    def handle(self) -> None:
        st: State = self.server.state  # type: ignore[attr-defined]
        msg = self._read()
        if not isinstance(msg, dict):
            return
        method = str(msg.get("method") or "")
        params = msg.get("params") or {}
        with st.lock:
            st.received.append(method)
        if method == "PingDevice":
            log.info("PingDevice → OK")
            return self._send(reply("PingDevice", responseCode="0000", code="00"))
        if method == "Void" or (method == "ServiceMessage" and params.get("msgType") == "interrupt"):
            log.info("%s → OK (переривання)", method)
            st.interrupt.set()
            return self._send(reply(method, responseCode="0000"))
        if method != "Purchase":
            log.info("%s → невідомий метод", method)
            return self._send(reply(method, error=True, description="Unknown method"))

        amount = str(params.get("amount") or "0.00")
        outcome = st.outcome(amount)
        delay = {"approve": st.approve_delay, "decline": st.decline_delay, "timeout": st.timeout_delay}[outcome]
        log.info("Purchase %s ₴ → %s через %.0f с", amount, outcome, delay)
        st.interrupt.clear()
        self._send(reply("ServiceMessage", msgType="insertCard"))
        if st.interrupt.wait(delay):
            log.info("Purchase %s ₴ → перервано", amount)
            return self._send(reply("Purchase", error=True, description="Interrupted", responseCode="1001", rrn=""))
        if outcome == "timeout":
            log.info("Purchase %s ₴ → тиша (таймаут)", amount)
            return
        self._send(purchase_result(outcome, amount))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, state: State):
        super().__init__((host, port), Handler)
        self.state = state


def make_server(host: str = "127.0.0.1", port: int = 2000, mode: str = "approve", *, approve_delay: float = 3.0,
                decline_delay: float = 2.0, timeout_delay: float = 90.0) -> Server:
    if mode not in MODES:
        raise ValueError(f"mode: {', '.join(MODES)}")
    return Server(host, port, State(mode, approve_delay, decline_delay, timeout_delay))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Емулятор POS-термінала ПриватБанку (ECR JSON по TCP)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--mode", choices=MODES, default="approve")
    ap.add_argument("--approve-delay", type=float, default=3.0)
    ap.add_argument("--decline-delay", type=float, default=2.0)
    ap.add_argument("--timeout-delay", type=float, default=90.0)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    srv = make_server(a.host, a.port, a.mode, approve_delay=a.approve_delay, decline_delay=a.decline_delay,
                      timeout_delay=a.timeout_delay)
    print(f"Емулятор термінала: {a.host}:{srv.server_address[1]}, режим {a.mode}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
