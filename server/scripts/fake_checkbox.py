#!/usr/bin/env python3
"""Емулятор API Checkbox (ПРРО) для перевірки без реальної каси. НЕ створює фіскальних чеків.

Лише стандартна бібліотека. Підтримує те, що використовує Connect (docs/CHECKBOX.md):
signin, cashier/shift, shifts, shifts/close, receipts/sell, receipts/{id}, receipts/{id}/text.
Текст чека — наближений до чеків Checkbox (реальний макет формує Checkbox), з позначкою «ЕМУЛЯЦІЯ».

    python scripts/fake_checkbox.py --port 8779
    # сервер Connect: MDS_CHECKBOX_API_URL=http://127.0.0.1:8779 MDS_CHECKBOX_LOGIN=x MDS_CHECKBOX_PASSWORD=x MDS_CHECKBOX_LICENSE_KEY=x
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ORG = ["ФОП ДЕМЧЕНКО МАРГАРИТА", "САЛОН КРАСИ M&D", "м. Київ, вул. Тестова, 1", "ІД 3456789012"]
PAY_LABEL = {"CASH": "ГОТІВКА", "CASHLESS": "БЕЗГОТІВКОВА"}


def money(kop: int) -> str:
    return f"{kop // 100}.{kop % 100:02d}"


def line(left: str, right: str, w: int) -> str:
    left = left[: max(1, w - len(right) - 1)]
    return left + " " * (w - len(left) - len(right)) + right


def wrap(text: str, w: int) -> list[str]:
    out, cur = [], ""
    for word in text.split():
        if len(cur) + len(word) + (1 if cur else 0) > w:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    return out + ([cur] if cur else [])


def render(r: dict, w: int) -> str:
    b, rows = r["body"], []
    rows += [x.center(w).rstrip() for x in ORG]
    rows.append("-" * w)
    if any(g.get("is_return") for g in b["goods"]):
        rows.append("ВИДАТКОВИЙ ЧЕК (ПОВЕРНЕННЯ)".center(w).rstrip())
    for g in b["goods"]:
        qty = g["quantity"] / 1000
        rows += wrap(g["good"]["name"], w)
        rows.append(line(f"{qty:.3f} x {money(g['good']['price'])}", money(round(g["good"]["price"] * qty)), w))
    rows.append("-" * w)
    total = sum(p["value"] for p in b["payments"])
    rows.append(line("СУМА", money(total), w))
    for p in b["payments"]:
        rows.append(line(PAY_LABEL.get(p["type"], p["type"]), money(p["value"]), w))
        if p.get("card_mask"):
            rows.append(line("Картка", p["card_mask"], w))
        if p.get("rrn"):
            rows.append(line("RRN", p["rrn"], w))
        if p.get("auth_code"):
            rows.append(line("Код авт.", p["auth_code"], w))
    rows.append("-" * w)
    rows.append(line("ЧЕК №", str(r["serial"]), w))
    rows.append(line("ФН ПРРО", "4000000001", w))
    rows.append(line("ФН чека", r["fiscal_code"], w))
    rows.append(line("Замовлення", f"#{b.get('order_id', '')}", w))
    rows.append(datetime.fromisoformat(r["fiscal_date"]).strftime("%d.%m.%Y %H:%M:%S").center(w).rstrip())
    rows.append("ФІСКАЛЬНИЙ ЧЕК".center(w).rstrip())
    rows.append("*** ЕМУЛЯЦІЯ — НЕ ФІСКАЛЬНИЙ ***"[:w].center(w).rstrip())
    return "\n".join(rows) + "\n"


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.shift: dict | None = None
        self.receipts: dict[str, dict] = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("checkbox:", fmt % args, flush=True)

    def _send(self, code: int, body, ctype: str = "application/json"):
        data = (json.dumps(body, ensure_ascii=False) if ctype == "application/json" else body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        st: State = self.server.state  # type: ignore[attr-defined]
        u = urlsplit(self.path)
        if u.path == "/api/v1/cashier/shift":
            return self._send(200, st.shift)
        m = re.fullmatch(r"/api/v1/receipts/([^/]+)/text", u.path)
        if m and m.group(1) in st.receipts:
            w = int((parse_qs(u.query).get("width") or ["32"])[0])
            return self._send(200, render(st.receipts[m.group(1)], w), "text/plain")
        m = re.fullmatch(r"/api/v1/receipts/([^/]+)", u.path)
        if m and m.group(1) in st.receipts:
            r = st.receipts[m.group(1)]
            return self._send(200, {k: r[k] for k in ("id", "status", "fiscal_code", "fiscal_date", "serial")})
        self._send(404, {"message": "Not found"})

    def do_POST(self):
        st: State = self.server.state  # type: ignore[attr-defined]
        u = urlsplit(self.path)
        body = self._body()
        if u.path == "/api/v1/cashier/signin":
            return self._send(200, {"access_token": "EMULATOR-TOKEN", "token_type": "bearer"})
        if u.path == "/api/v1/shifts":
            st.shift = {"id": body.get("id"), "status": "OPENED", "auto_close_at": body.get("auto_close_at")}
            return self._send(202, st.shift)
        if u.path == "/api/v1/shifts/close":
            st.shift = None
            return self._send(202, {"status": "CLOSING"})
        if u.path == "/api/v1/receipts/sell":
            if st.shift is None:
                return self._send(400, {"message": "Зміну не відкрито"})
            with st.lock:
                if body["id"] in st.receipts:
                    return self._send(400, {"message": "Чек з таким ID вже існує"})
                serial = len(st.receipts) + 1
                r = {"id": body["id"], "status": "DONE", "serial": serial, "fiscal_code": f"EMU{serial:07d}",
                     "fiscal_date": datetime.now().replace(microsecond=0).isoformat(), "body": body}
                st.receipts[body["id"]] = r
            print(f"checkbox: чек {r['fiscal_code']} на {money(sum(p['value'] for p in body['payments']))} грн", flush=True)
            return self._send(201, {k: r[k] for k in ("id", "status", "fiscal_code", "fiscal_date", "serial")})
        self._send(404, {"message": "Unknown"})


def make_server(host: str = "127.0.0.1", port: int = 8779) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.state = State()  # type: ignore[attr-defined]
    return srv


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Емулятор API Checkbox (без фіскальних чеків)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8779)
    a = ap.parse_args(argv)
    srv = make_server(a.host, a.port)
    print(f"Емулятор Checkbox: http://{a.host}:{srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
