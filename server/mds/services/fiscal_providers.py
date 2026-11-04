"""Провайдери ПРРО (програмних реєстраторів розрахункових операцій).

Сервіс фіскалізації (services/fiscal.py) працює лише через цей інтерфейс — провайдера можна замінити.

Помилки:
- ProviderUnavailable — мережа/5xx/таймаут ДО відправки: безпечно повторити;
- ProviderLost — запит міг дійти, а відповідь втрачено: перед повтором ОБОВ'ЯЗКОВО get_receipt(uuid);
- ProviderRejected — провайдер відмовив (4xx, валідація, немає зміни): потрібна увага, без автоповтору.

Реалізації:
- FakePRROProvider — імітація для тестів і демо: зберігає чеки за uuid, вміє імітувати збої.
  НІКОЛИ не створює справжніх фіскальних чеків.
- CheckboxProvider — Checkbox (https://api.checkbox.ua), за офіційною OpenAPI-специфікацією
  (checkbox API 2.110.1): POST /api/v1/cashier/signinPinCode (X-License-Key, pin_code) → Bearer JWT;
  POST /api/v1/shifts (id, X-License-Key) → статуси CREATED/OPENING/OPENED; POST /api/v1/shifts/close → Z-звіт;
  POST /api/v1/reports → X-звіт; POST /api/v1/receipts/sell (id = наш UUID, goods[].good{code,name,price},
  quantity у тисячних, is_return, payments[{type CASH|CASHLESS, value}], related_receipt_id);
  GET /api/v1/receipts/{id} → status CREATED|DONE|ERROR|…, fiscal_code, fiscal_date, tax_url.
  Ключ ліцензії і PIN касира — лише зі змінних оточення (MDS_CHECKBOX_*), не з БД і не в логи.
  Перевірено лише на рівні контракту (тести з імітацією HTTP); потрібна перевірка на тестовій касі Checkbox.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Callable


class ProviderError(Exception):
    pass


class ProviderUnavailable(ProviderError):
    """Запит не дійшов або сервіс недоступний — можна повторити."""


class ProviderLost(ProviderError):
    """Відповідь втрачено: невідомо, чи створено чек. Спершу перевірити get_receipt."""


class ProviderRejected(ProviderError):
    """Провайдер відмовив (дані/стан). Автоматично не повторюється."""


@dataclass
class ReceiptResult:
    status: str  # done | pending | error
    provider_id: str | None = None
    fiscal_code: str | None = None
    fiscal_date: str | None = None
    tax_url: str | None = None
    error: str | None = None


class Provider:
    name = "base"

    def open_shift(self, shift_uuid: str) -> dict:
        raise NotImplementedError

    def shift_status(self) -> dict | None:
        raise NotImplementedError

    def close_shift(self) -> dict:
        raise NotImplementedError

    def x_report(self) -> dict:
        raise NotImplementedError

    def sell(self, receipt_uuid: str, goods: list[dict], payment_method: str, amount: int, *,
             is_return: bool = False, related_provider_id: str | None = None) -> ReceiptResult:
        raise NotImplementedError

    def get_receipt(self, receipt_uuid: str) -> ReceiptResult | None:
        raise NotImplementedError


# ---------------------------------------------------------------------------- імітація
@dataclass
class _FakeState:
    receipts: dict = field(default_factory=dict)
    shift: dict | None = None
    shifts_closed: list = field(default_factory=list)
    serial: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class FakePRROProvider(Provider):
    """Імітація ПРРО. Стан — спільний «сервер» (state), що переживає «перезапуск» нашого сервера.

    fail_next: список режимів для наступних викликів sell/open/close:
      "unavailable" — не дійшло; "lost" — чек СТВОРЕНО, але відповідь втрачено;
      "reject" — відмова; "pending" — чек прийнято, фіскалізація пізніше (status CREATED).
    """

    name = "fake"

    def __init__(self, state: _FakeState | None = None, *, auto_shift: bool = False, delay: float = 0.0):
        self.state = state or _FakeState()
        self.fail_next: list[str] = []
        self.calls: list[tuple] = []
        self.auto_shift = auto_shift
        self.delay = delay
        self.down = False  # повна недоступність

    def _mode(self) -> str | None:
        if self.down:
            return "unavailable"
        with self.state.lock:
            return self.fail_next.pop(0) if self.fail_next else None

    def open_shift(self, shift_uuid):
        self.calls.append(("open_shift", shift_uuid))
        if self._mode() == "unavailable":
            raise ProviderUnavailable("ПРРО недоступний")
        with self.state.lock:
            if self.state.shift and self.state.shift["status"] == "OPENED":
                if self.state.shift["id"] == shift_uuid:
                    return dict(self.state.shift)
                raise ProviderRejected("Зміну вже відкрито")
            self.state.shift = {"id": shift_uuid, "status": "OPENED", "sales": 0, "returns": 0, "cash": 0, "card": 0,
                                "sell_count": 0, "return_count": 0}
            return dict(self.state.shift)

    def shift_status(self):
        return dict(self.state.shift) if self.state.shift else None

    def close_shift(self):
        self.calls.append(("close_shift",))
        if self._mode() == "unavailable":
            raise ProviderUnavailable("ПРРО недоступний")
        with self.state.lock:
            sh = self.state.shift
            if not sh or sh["status"] != "OPENED":
                raise ProviderRejected("Немає відкритої зміни")
            sh["status"] = "CLOSED"
            z = {"is_z_report": True, **{k: sh[k] for k in ("sales", "returns", "cash", "card", "sell_count", "return_count")}}
            self.state.shifts_closed.append(z)
            self.state.shift = None
            return {"status": "CLOSED", "z_report": z}

    def x_report(self):
        self.calls.append(("x_report",))
        if self._mode() == "unavailable":
            raise ProviderUnavailable("ПРРО недоступний")
        sh = self.state.shift
        if not sh:
            raise ProviderRejected("Немає відкритої зміни")
        return {"is_z_report": False, **{k: sh[k] for k in ("sales", "returns", "cash", "card", "sell_count", "return_count")}}

    def sell(self, receipt_uuid, goods, payment_method, amount, *, is_return=False, related_provider_id=None):
        self.calls.append(("sell", receipt_uuid, amount, is_return))
        if self.delay:
            time.sleep(self.delay)
        mode = self._mode()
        if mode == "unavailable":
            raise ProviderUnavailable("ПРРО недоступний")
        if mode == "reject":
            raise ProviderRejected("Невірні дані чека")
        with self.state.lock:
            if receipt_uuid in self.state.receipts:  # той самий id — без нового чека
                r = self.state.receipts[receipt_uuid]
                return ReceiptResult(**r)
            sh = self.state.shift
            if not sh or sh["status"] != "OPENED":
                if self.auto_shift:
                    self.state.shift = sh = {"id": str(uuidlib.uuid4()), "status": "OPENED", "sales": 0, "returns": 0,
                                             "cash": 0, "card": 0, "sell_count": 0, "return_count": 0}
                else:
                    raise ProviderRejected("Зміну не відкрито")
            self.state.serial += 1
            status = "pending" if mode == "pending" else "done"
            r = {"status": status, "provider_id": receipt_uuid,
                 "fiscal_code": f"TEST-{self.state.serial:06d}" if status == "done" else None,
                 "fiscal_date": "2026-01-01T00:00:00" if status == "done" else None,
                 "tax_url": f"https://test.local/check?id=TEST-{self.state.serial:06d}" if status == "done" else None}
            self.state.receipts[receipt_uuid] = {**r, "_amount": amount, "_pm": payment_method, "_ret": is_return,
                                                 "_rel": related_provider_id}
            key = "returns" if is_return else "sales"
            sh[key] += amount
            sh["return_count" if is_return else "sell_count"] += 1
            sh["cash" if payment_method == "cash" else "card"] += -amount if is_return else amount
        if mode == "lost":
            raise ProviderLost("Відповідь не отримано")
        return ReceiptResult(**r)

    def complete_pending(self):
        """Імітація: ДПС підтвердила відкладені чеки."""
        with self.state.lock:
            for r in self.state.receipts.values():
                if r["status"] == "pending":
                    self.state.serial += 1
                    r.update(status="done", fiscal_code=f"TEST-{self.state.serial:06d}", fiscal_date="2026-01-01T00:00:00",
                             tax_url=f"https://test.local/check?id=TEST-{self.state.serial:06d}")

    def get_receipt(self, receipt_uuid):
        self.calls.append(("get", receipt_uuid))
        if self.down:
            raise ProviderUnavailable("ПРРО недоступний")
        r = self.state.receipts.get(receipt_uuid)
        if r is None:
            return None
        return ReceiptResult(**{k: v for k, v in r.items() if not k.startswith("_")})


# ---------------------------------------------------------------------------- Checkbox
@dataclass
class Resp:
    status: int
    body: bytes

    def json(self):
        try:
            return json.loads(self.body or b"{}")
        except ValueError:
            return {}


def _urllib(method: str, url: str, headers: dict, data: bytes | None, timeout: float) -> Resp:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — лише https-адреса провайдера
            return Resp(r.status, r.read())
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.read())


class CheckboxProvider(Provider):
    name = "checkbox"
    STATUS = {"DONE": "done", "CREATED": "pending", "ERROR": "error", "CANCELLATION": "error", "CANCELLED": "error"}

    def __init__(self, *, base_url: str | None = None, license_key: str | None = None, pin_code: str | None = None,
                 access_key: str | None = None, http: Callable | None = None, timeout: float = 30.0):
        self.base = (base_url or os.getenv("MDS_CHECKBOX_BASE_URL") or "https://api.checkbox.ua").rstrip("/")
        self._license = license_key if license_key is not None else os.getenv("MDS_CHECKBOX_LICENSE_KEY", "")
        self._pin = pin_code if pin_code is not None else os.getenv("MDS_CHECKBOX_PIN", "")
        self._access = access_key if access_key is not None else os.getenv("MDS_CHECKBOX_ACCESS_KEY", "")
        self._http = http or _urllib
        self.timeout = timeout
        self._token: str | None = None

    def __repr__(self) -> str:  # без секретів
        return f"CheckboxProvider(base={self.base!r}, configured={bool(self._license and self._pin)})"

    def configured(self) -> bool:
        return bool(self._license and self._pin)

    def _headers(self, *, license: bool = False, auth: bool = True) -> dict:
        h = {"Content-Type": "application/json", "X-Client-Name": "MDS Connect", "X-Client-Version": "0.1"}
        if license:
            h["X-License-Key"] = self._license
        if self._access:
            h["X-Access-Key"] = self._access
        if auth:
            h["Authorization"] = f"Bearer {self._login()}"
        return h

    def _call(self, method: str, path: str, body: dict | None = None, *, license: bool = False, auth: bool = True,
              idempotent_write: bool = False) -> Resp:
        data = json.dumps(body).encode() if body is not None else None
        try:
            r = self._http(method, self.base + path, self._headers(license=license, auth=auth), data, self.timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # для запису (чек/зміна) невідомо, чи дійшов запит — повтор лише після перевірки
            if idempotent_write and isinstance(e, TimeoutError):
                raise ProviderLost("Checkbox: відповідь не отримано (таймаут)") from None
            raise ProviderUnavailable(f"Checkbox недоступний: {type(e).__name__}") from None
        if r.status == 401 and auth:
            self._token = None
            raise ProviderUnavailable("Checkbox: сесія касира завершилась — повторіть")
        if r.status >= 500 or r.status == 429:
            if idempotent_write:
                raise ProviderLost(f"Checkbox: помилка сервера {r.status}")
            raise ProviderUnavailable(f"Checkbox: помилка сервера {r.status}")
        return r

    def _login(self) -> str:
        if self._token:
            return self._token
        if not self.configured():
            raise ProviderRejected("Checkbox не налаштовано: MDS_CHECKBOX_LICENSE_KEY і MDS_CHECKBOX_PIN")
        r = self._call("POST", "/api/v1/cashier/signinPinCode", {"pin_code": self._pin}, license=True, auth=False)
        if r.status >= 400:
            raise ProviderRejected(f"Checkbox: вхід касира не вдався ({r.status})")
        self._token = r.json().get("access_token")
        if not self._token:
            raise ProviderRejected("Checkbox: не отримано токен касира")
        return self._token

    def _result(self, d: dict) -> ReceiptResult:
        return ReceiptResult(status=self.STATUS.get(d.get("status"), "pending"), provider_id=d.get("id"),
                             fiscal_code=d.get("fiscal_code"), fiscal_date=d.get("fiscal_date"), tax_url=d.get("tax_url"))

    def open_shift(self, shift_uuid):
        r = self._call("POST", "/api/v1/shifts", {"id": shift_uuid}, license=True, idempotent_write=True)
        if r.status >= 400:
            raise ProviderRejected(f"Checkbox: зміну не відкрито ({r.status}): {r.json().get('message', '')}")
        return r.json()

    def shift_status(self):
        r = self._call("GET", "/api/v1/cashier/shift")
        if r.status == 404 or not r.body or r.body == b"null":
            return None
        return r.json()

    def close_shift(self):
        r = self._call("POST", "/api/v1/shifts/close", {}, idempotent_write=True)
        if r.status >= 400:
            raise ProviderRejected(f"Checkbox: зміну не закрито ({r.status}): {r.json().get('message', '')}")
        return r.json()

    def x_report(self):
        r = self._call("POST", "/api/v1/reports", None)
        if r.status >= 400:
            raise ProviderRejected(f"Checkbox: X-звіт не створено ({r.status})")
        return r.json()

    def sell(self, receipt_uuid, goods, payment_method, amount, *, is_return=False, related_provider_id=None):
        body = {
            "id": receipt_uuid,
            "goods": [{"good": {"code": str(g["code"]), "name": g["name"][:256], "price": int(g["price"])},
                       "quantity": int(g.get("quantity", 1000)), "is_return": bool(is_return)} for g in goods],
            "payments": [{"type": "CASH" if payment_method == "cash" else "CASHLESS", "value": int(amount)}],
        }
        if related_provider_id:
            body["related_receipt_id"] = related_provider_id
        r = self._call("POST", "/api/v1/receipts/sell", body, idempotent_write=True)
        if r.status >= 400:
            raise ProviderRejected(f"Checkbox: чек відхилено ({r.status}): {json.dumps(r.json(), ensure_ascii=False)[:300]}")
        return self._result(r.json())

    def get_receipt(self, receipt_uuid):
        r = self._call("GET", f"/api/v1/receipts/{receipt_uuid}")
        if r.status == 404:
            return None
        if r.status >= 400:
            raise ProviderUnavailable(f"Checkbox: статус чека недоступний ({r.status})")
        return self._result(r.json())


# ---------------------------------------------------------------------------- реєстр
_FAKE = FakePRROProvider(auto_shift=False)
_override: Provider | None = None


def get_provider(name: str) -> Provider:
    if _override is not None:
        return _override
    if name == "checkbox":
        return CheckboxProvider()
    return _FAKE


def set_provider(p: Provider | None) -> None:
    """Тести: підмінити провайдера (None — повернути стандартний)."""
    global _override
    _override = p
