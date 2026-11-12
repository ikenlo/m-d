#!/usr/bin/env python3
"""Емулятор чекового принтера ESC/POS (TCP, як мережевий принтер на порту 9100).

Приймає байти так само, як справжній принтер, розбирає команди, які надсилає Connect
(ESC @ — скидання, ESC t n — кодова сторінка, GS V — обрізання), декодує текст і зберігає
кожен чек як .txt і .html (вигляд термострічки). Лише стандартна бібліотека.

    python scripts/fake_printer.py --port 9100 --out /tmp/receipts
"""
from __future__ import annotations

import argparse
import html
import socketserver
from pathlib import Path

CODEPAGES = {0: "cp437", 17: "cp866", 46: "cp1251"}


def parse(data: bytes) -> dict:
    """ESC/POS → {text, codepage, cut, reset, unknown}. Невідомі команди не ламають розбір."""
    out, i, cp = [], 0, "cp437"
    info = {"reset": False, "cut": False, "codepages": [], "unknown": []}
    buf = bytearray()

    def flush():
        if buf:
            out.append(bytes(buf).decode(cp, errors="replace"))
            buf.clear()

    while i < len(data):
        b = data[i]
        if b == 0x1B and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd == 0x40:
                flush()
                info["reset"], cp, i = True, "cp437", i + 2
                continue
            if cmd == 0x74 and i + 2 < len(data):
                flush()
                n = data[i + 2]
                cp = CODEPAGES.get(n, cp)
                info["codepages"].append(n)
                i += 3
                continue
            info["unknown"].append(f"ESC {cmd:#04x}")
            i += 2
            continue
        if b == 0x1D and i + 1 < len(data) and data[i + 1] == 0x56:
            flush()
            info["cut"] = True
            i += 4 if i + 2 < len(data) and data[i + 2] in (0x41, 0x42, 65, 66) else 3
            continue
        buf.append(b)
        i += 1
    flush()
    info["text"] = "".join(out).rstrip("\n") + "\n"
    info["codepage"] = CODEPAGES.get(info["codepages"][-1], "?") if info["codepages"] else "cp437"
    return info


def to_html(p: dict, title: str) -> str:
    lines = p["text"].rstrip("\n").split("\n")
    width = max(32, max((len(x) for x in lines), default=32))
    cut = '<div class="cut">✂ - - - - обрізання - - - -</div>' if p["cut"] else ""
    return f"""<!doctype html><html lang="uk"><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body{{margin:0;padding:24px;background:#d9dde2;font-family:sans-serif}}
.paper{{width:{width}ch;margin:0 auto;background:#fffefa;padding:18px 16px 10px;box-shadow:0 8px 24px rgba(0,0,0,.18);
  font:15px/1.35 "DejaVu Sans Mono","Courier New",monospace;color:#1d1d1d;white-space:pre;
  -webkit-mask:linear-gradient(#000 0 0) top/100% calc(100% - 8px) no-repeat,
  conic-gradient(from -45deg at bottom,#0000,#000 1deg 89deg,#0000 90deg) bottom/12px 8px repeat-x}}
.cut{{text-align:center;font:11px monospace;color:#888;margin:8px auto 0;width:{width}ch;white-space:nowrap;overflow:hidden}}
.meta{{text-align:center;font-size:12px;color:#555;margin-bottom:10px}}
</style>
<div class="meta">{html.escape(title)} · кодова сторінка {p["codepage"]} · ESC @ {"✓" if p["reset"] else "✗"} · обрізання {"✓" if p["cut"] else "✗"}</div>
<div class="paper">{html.escape(p["text"].rstrip(chr(10)))}</div>{cut}
</html>"""


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        chunks = []
        self.request.settimeout(5)
        try:
            while True:
                c = self.request.recv(4096)
                if not c:
                    break
                chunks.append(c)
        except OSError:
            pass
        data = b"".join(chunks)
        if not data:
            return
        srv = self.server
        srv.count += 1  # type: ignore[attr-defined]
        n = srv.count  # type: ignore[attr-defined]
        p = parse(data)
        out: Path = srv.out  # type: ignore[attr-defined]
        (out / f"receipt-{n}.bin").write_bytes(data)
        (out / f"receipt-{n}.txt").write_text(p["text"], encoding="utf-8")
        (out / f"receipt-{n}.html").write_text(to_html(p, f"Чек {n}"), encoding="utf-8")
        print(f"printer: чек {n}, {len(data)} байт, {p['codepage']}, обрізання={p['cut']}", flush=True)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_server(host: str, port: int, out: Path) -> Server:
    out.mkdir(parents=True, exist_ok=True)
    srv = Server((host, port), Handler)
    srv.out, srv.count = out, 0  # type: ignore[attr-defined]
    return srv


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Емулятор чекового принтера ESC/POS")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--out", default="printed")
    a = ap.parse_args(argv)
    srv = make_server(a.host, a.port, Path(a.out))
    print(f"Емулятор принтера: {a.host}:{srv.server_address[1]} → {Path(a.out).resolve()}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
