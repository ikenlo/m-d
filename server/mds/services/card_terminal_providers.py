"""«Термінал» ПриватБанку (NFC на планшеті) — серверна частина App-to-App інтеграції.

Документація банку: https://app.privatbank.ua/nfcpos (handbook: privatbank.ua/handbook/sumishennya_zastosunok_terminal).
Потік: сервер → token.php (JWT операції) → планшет відкриває nfcterminal://executor?token=…&callback=… →
клієнт платить → сервер → check.php (фінальний результат, успіх лише при response_code "00").

Ключі (лише змінні сервера, не БД і не журнали):
  MDS_PRIVAT_TERMINAL_CLID, MDS_PRIVAT_TERMINAL_SECRET — видає банк інтегратору;
  MDS_PRIVAT_TERMINAL_CLIENT_TOKEN — ключ підприємства з кабінету terminal.pb.ua («Інтеграція»).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

BASE_URL = "https://dio.privatbank.ua/api/nfcpos/integrators"
DEEPLINK = "nfcterminal://executor"
CALLBACK = "mdsconnect://terminal-result"
TERMINAL_PACKAGE = "ua.privatbank.pterminal"
ENV = ("MDS_PRIVAT_TERMINAL_CLID", "MDS_PRIVAT_TERMINAL_SECRET", "MDS_PRIVAT_TERMINAL_CLIENT_TOKEN")
MODES = ("off", "mock", "live")


class TerminalError(Exception):
    """Помилка банку або зв'язку. Повідомлення без ключів."""


class TerminalUnavailable(TerminalError):
    """Мережа / 5xx — можна повторити перевірку пізніше."""


def sign(secret: str, signed: str, data: str) -> str:
    """signature = sha1(signed + secret + data + secret) — за документацією банку."""
    return hashlib.sha1((signed + secret + data + secret).encode("utf-8")).hexdigest()


def body_json(data: dict, amount_kop: int | None = None) -> str:
    """Тіло запиту рівно тими байтами, що підписуються. Сума — число з 2 знаками (грн), без float."""
    d = dict(data)
    if amount_kop is not None:
        d["amount"] = "__AMOUNT__"
    s = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
    if amount_kop is not None:
        s = s.replace('"__AMOUNT__"', f"{amount_kop // 100}.{amount_kop % 100:02d}")
    return s


def deeplink(jwt: str) -> str:
    return f"{DEEPLINK}?token={urllib.parse.quote(jwt, safe='')}&callback={urllib.parse.quote(CALLBACK, safe='')}"


def configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in ENV)


HttpFn = Callable[[str, bytes, dict, float], tuple[int, bytes]]


def _urllib(url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — лише https BASE_URL
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""


class PrivatTerminalProvider:
    name = "live"

    def __init__(self, clid: str, secret: str, client_token: str, *, http: HttpFn | None = None,
                 base_url: str = BASE_URL, now: Callable[[], float] = time.time, timeout: float = 20.0):
        if not (clid and secret and client_token):
            raise TerminalError("Не задано ключі «Термінала» на сервері (MDS_PRIVAT_TERMINAL_*)")
        self._clid, self._secret, self._client_token = clid, secret, client_token
        self._http, self._base, self._now, self._timeout = http or _urllib, base_url.rstrip("/"), now, timeout

    def __repr__(self) -> str:
        return "PrivatTerminalProvider(clid=***, secret=***)"

    def _post(self, endpoint: str, data: str) -> dict:
        signed = str(int(self._now()))
        q = urllib.parse.urlencode({"clid": self._clid, "signed": signed, "signature": sign(self._secret, signed, data)})
        try:
            status, raw = self._http(f"{self._base}/{endpoint}?{q}", data.encode("utf-8"),
                                     {"Content-Type": "application/json"}, self._timeout)
        except (TimeoutError, OSError) as e:
            raise TerminalUnavailable(f"Банк недоступний ({type(e).__name__})") from None
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            payload = {}
        if status >= 500 or status == 418:  # 418 — підпис застарів (годинник), можна повторити
            raise TerminalUnavailable(f"Банк: HTTP {status}")
        if status >= 400 or payload.get("success") is False:
            msg = payload.get("message") or f"HTTP {status}"
            raise TerminalError(f"Банк відхилив запит: {msg}" + (f" ({payload['error']})" if payload.get("error") else ""))
        return payload

    def create_pay(self, amount_kop: int, purpose: str) -> dict:
        data = body_json({"operation": "pay", "amount": None, "purpose": purpose, "client_token": self._client_token},
                         amount_kop)
        r = self._post("token.php", data)
        if not r.get("jwt"):
            raise TerminalError("Банк не повернув токен операції")
        return {"jwt": r["jwt"], "rid": r.get("rid")}

    def check(self, jwt: str) -> dict:
        return self._post("check.php", body_json({"jwt": jwt}))


class FakeTerminalProvider:
    """Імітація для тестів і режиму mock: «оплата» відбувається, коли викликано complete()."""

    name = "mock"

    def __init__(self):
        self.ops: dict[str, dict] = {}
        self.fail_next: str | None = None  # "decline" | "unavailable" | "reject"
        self.n = 0

    def create_pay(self, amount_kop: int, purpose: str) -> dict:
        if self.fail_next == "reject":
            self.fail_next = None
            raise TerminalError("Банк відхилив запит: Невалідні дані запиту. (IE_01)")
        self.n += 1
        jwt = f"MOCK-JWT-{self.n}"
        self.ops[jwt] = {"amount": amount_kop, "state": "new"}
        return {"jwt": jwt, "rid": f"MOCK-RID-{self.n}"}

    def complete(self, jwt: str, ok: bool = True) -> None:
        self.ops[jwt]["state"] = "paid" if ok else "declined"

    def check(self, jwt: str) -> dict:
        if self.fail_next == "unavailable":
            self.fail_next = None
            raise TerminalUnavailable("Банк: HTTP 503")
        op = self.ops.get(jwt)
        if op is None:
            raise TerminalError("Банк відхилив запит: операцію не знайдено")
        if op["state"] == "new":
            return {"success": True}  # клієнт ще не оплатив
        a = op["amount"]
        ok = op["state"] == "paid"
        return {"success": True, "pay": {
            "transaction_id": f"PAX-TEST-{jwt[-4:]}", "code": "iso00_Approved" if ok else "iso05_Declined",
            "user_message": "Успішно" if ok else "Відмова банку-емітента", "approval_code": "131192" if ok else "",
            "response_code": "00" if ok else "05", "rrn": "014722766522" if ok else "", "amount_full": a / 100,
            "payment_system": "Visa", "masked_pan": "4149********1451", "receipt": "<p>Чек (імітація)</p>"}}


_override = None
_mock = FakeTerminalProvider()


def set_provider(p) -> None:
    """Лише для тестів."""
    global _override
    _override = p


def get_provider(mode: str):
    if _override is not None:
        return _override
    if mode == "mock":
        return _mock
    if mode == "live":
        return PrivatTerminalProvider(*(os.environ.get(k, "").strip() for k in ENV))
    raise TerminalError("Оплату через «Термінал» вимкнено в налаштуваннях")
