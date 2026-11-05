"""Моніторинг швидкодії в пам'яті процесу: час відповіді API (сервер) і вимірювання з браузерів (RUM).

Без зовнішніх сервісів і без персональних даних: зберігаються лише назви маршрутів/сторінок і мілісекунди.
Дані скидаються при перезапуску (кожен деплой) — для «що зараз повільно» цього досить.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque

SAMPLES = 300          # останніх вимірів на одну назву
MAX_NAMES = 300        # захист від розростання (напр. сміттєві шляхи)
SLOW_API_MS = 1000     # повільний запит — пишемо в журнал сервера
SLOW_CLIENT_MS = 3000  # повільне завантаження сторінки в браузері

_lock = threading.Lock()
_started = time.time()
_data: dict[str, dict[str, dict]] = {"server": {}, "client": {}}

# що приймаємо від браузера: тип:назва (назва — лише латиниця/цифри, без даних користувача)
CLIENT_NAME = re.compile(r"^(nav|view):[a-z0-9_-]{1,32}$")


def record(kind: str, name: str, ms: float, slow_ms: float) -> None:
    with _lock:
        bucket = _data[kind]
        item = bucket.get(name)
        if item is None:
            if len(bucket) >= MAX_NAMES:
                return
            item = bucket[name] = {"samples": deque(maxlen=SAMPLES), "count": 0, "slow": 0, "max": 0.0}
        item["samples"].append(ms)
        item["count"] += 1
        item["max"] = max(item["max"], ms)
        if ms >= slow_ms:
            item["slow"] += 1


def _pct(values: list[float], p: float) -> int:
    if not values:
        return 0
    v = sorted(values)
    return round(v[min(len(v) - 1, int(len(v) * p))])


def snapshot(limit: int = 30) -> dict:
    out = {}
    with _lock:
        for kind, bucket in _data.items():
            rows = []
            for name, it in bucket.items():
                vals = list(it["samples"])
                rows.append({"name": name, "count": it["count"], "p50": _pct(vals, 0.5), "p95": _pct(vals, 0.95),
                             "max": round(it["max"]), "slow": it["slow"]})
            rows.sort(key=lambda r: r["p95"], reverse=True)
            out[kind] = rows[:limit]
    out["since"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(_started))
    out["slow_api_ms"], out["slow_client_ms"] = SLOW_API_MS, SLOW_CLIENT_MS
    return out


def reset() -> None:
    with _lock:
        for b in _data.values():
            b.clear()
