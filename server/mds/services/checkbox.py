"""Checkbox (ПРРО): фіскальні чеки для замовлень і друк на чековому принтері через планшет (docs/CHECKBOX.md).

Контракт — офіційна OpenAPI-специфікація Checkbox 2.112.0 (https://api.checkbox.ua/api/openapi.json).
Ключі касира — лише змінні MDS_CHECKBOX_* (не БД, не логи). Від чека нічого не залежить у грошах:
закриття й повернення замовлень працюють і без Checkbox, чеки стають у чергу й створюються пізніше.
"""
from __future__ import annotations

import base64
import logging
import uuid as uuidlib
from datetime import datetime, time

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import __version__, clock
from ..errors import Conflict, DomainError, Forbidden, NotFound, ValidationError
from ..models import FiscalReceipt, Order, OrderStatus, PaymentMethod
from .common import Actor, Role, audit, get_setting, set_setting

log = logging.getLogger("mds.checkbox")

CLIENT_NAME = "MDS Connect"
TIMEOUT = 15.0
MAX_ATTEMPTS = 12
SHIFT_AUTO_CLOSE = time(23, 50)
PAYMENT_TYPES = {PaymentMethod.CASH: "CASH", PaymentMethod.CARD: "CASHLESS", PaymentMethod.TRANSFER: "CASHLESS"}
PRINTERS = ("none", "wifi", "bluetooth")
WIDTHS = (32, 42, 48)
CODEPAGES = {"cp1251": 46, "cp866": 17}
# Символи, яких немає в кодовій сторінці принтера (CP866 не має І, і, Ґ, ґ), — найближча заміна замість «?».
FALLBACK = {"І": "I", "і": "i", "Ґ": "Г", "ґ": "г", "Є": "Е", "є": "е", "Ї": "I", "ї": "i", "—": "-", "–": "-", "₴": "грн",
            "’": "'", "ʼ": "'", "«": '"', "»": '"', "…": "...", "№": "N", " ": " "}


def _encode(text: str, codepage: str) -> bytes:
    out = bytearray()
    for ch in text:
        try:
            out += ch.encode(codepage)
        except UnicodeEncodeError:
            out += FALLBACK.get(ch, "?").encode(codepage, errors="replace")
    return bytes(out)


class CheckboxError(Exception):
    """Checkbox відмовив (дані, стан, ключі) — без автоповтору."""


class CheckboxUnavailable(CheckboxError):
    """Мережа, таймаут або 5xx — повторити пізніше (чек з тим самим id не задублюється)."""


class CheckboxClient:

    def __init__(self, base_url: str, login: str, password: str, license_key: str, *, timeout: float = TIMEOUT,
                 transport: httpx.BaseTransport | None = None):
        if not (login and password and license_key):
            raise CheckboxError("Не задано ключі Checkbox на сервері (MDS_CHECKBOX_LOGIN, _PASSWORD, _LICENSE_KEY)")
        self._login, self._password, self._license = login, password, license_key
        self._token: str | None = None
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                  headers={"X-Client-Name": CLIENT_NAME, "X-Client-Version": __version__})

    def __repr__(self) -> str:
        return "CheckboxClient(login=***, license=***)"

    def close(self) -> None:
        self._http.close()

    def _send(self, method: str, path: str, *, auth: bool = True, license_key: bool = False, **kw) -> httpx.Response:
        headers = kw.pop("headers", {})
        if auth:
            headers["Authorization"] = f"Bearer {self._token or self.signin()}"
        if license_key:
            headers["X-License-Key"] = self._license
        try:
            return self._http.request(method, path, headers=headers, **kw)
        except httpx.TimeoutException:
            raise CheckboxUnavailable("Checkbox не відповів вчасно") from None
        except httpx.HTTPError as e:
            raise CheckboxUnavailable(f"Checkbox недоступний ({type(e).__name__})") from None

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        r = self._send(method, path, **kw)
        if r.status_code == 401 and kw.get("auth", True):
            self._token = None
            r = self._send(method, path, **kw)
        if r.status_code >= 500 or r.status_code == 429:
            raise CheckboxUnavailable(f"Checkbox: HTTP {r.status_code}")
        if r.status_code >= 400:
            raise CheckboxError(f"Checkbox відмовив: {_message(r)}")
        return r

    def signin(self) -> str:
        r = self._call("POST", "/api/v1/cashier/signin", auth=False,
                       json={"login": self._login, "password": self._password})
        token = (r.json() or {}).get("access_token")
        if not token:
            raise CheckboxError("Checkbox не повернув токен касира")
        self._token = token
        return token

    def current_shift(self) -> dict | None:
        r = self._call("GET", "/api/v1/cashier/shift")
        return r.json() if r.content and r.json() else None

    def open_shift(self, auto_close_at: datetime | None = None) -> dict:
        body: dict = {"id": str(uuidlib.uuid4())}
        if auto_close_at is not None:
            body["auto_close_at"] = auto_close_at.isoformat()
        return self._call("POST", "/api/v1/shifts", license_key=True, json=body).json()

    def close_shift(self) -> dict:
        return self._call("POST", "/api/v1/shifts/close", json={}).json()

    def create_receipt(self, receipt_id: str, order_id: int, amount: int, payment_method: str, items: list[dict], *,
                       card: dict | None = None) -> dict:
        body = receipt_payload(receipt_id, order_id, amount, payment_method, items, card=card)
        return self._create(receipt_id, body)

    def create_return_receipt(self, receipt_id: str, original_receipt_id: str, amount: int, payment_method: str,
                              items: list[dict], order_id: int) -> dict:
        body = receipt_payload(receipt_id, order_id, amount, payment_method, items, is_return=True)
        body["related_receipt_id"] = original_receipt_id
        return self._create(receipt_id, body)

    def _create(self, receipt_id: str, body: dict) -> dict:
        try:
            return self._call("POST", "/api/v1/receipts/sell", json=body).json()
        except CheckboxError as e:
            # Повтор після обриву: чек з цим id уже міг створитися — беремо наявний замість помилки.
            existing = self.get_receipt(receipt_id, missing_ok=True)
            if existing is not None:
                return existing
            raise e

    def get_receipt(self, receipt_id: str, *, missing_ok: bool = False) -> dict | None:
        try:
            return self._call("GET", f"/api/v1/receipts/{receipt_id}").json()
        except CheckboxError:
            if missing_ok:
                return None
            raise

    def get_receipt_text(self, receipt_id: str, width: int = 32) -> str:
        return self._call("GET", f"/api/v1/receipts/{receipt_id}/text", params={"width": width}).text


def _message(r: httpx.Response) -> str:
    try:
        d = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    msg = d.get("message") or d.get("detail") or f"HTTP {r.status_code}"
    if isinstance(msg, list):
        msg = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in msg)
    return str(msg)[:200]


def receipt_payload(receipt_id: str, order_id: int, amount: int, payment_method: str, items: list[dict], *,
                    is_return: bool = False, card: dict | None = None) -> dict:
    ptype = PAYMENT_TYPES.get(payment_method)
    if ptype is None:
        raise ValidationError(f"Невідомий спосіб оплати для чека: {payment_method}")
    goods = [{"good": {"code": it["code"], "name": it["name"][:256], "price": it["price"]},
              "quantity": it["qty"] * 1000, **({"is_return": True} if is_return else {})} for it in items]
    if sum(it["price"] * it["qty"] for it in items) != amount:
        raise ValidationError("Сума рядків чека не збігається з сумою оплати")
    payment: dict = {"type": ptype, "value": amount}
    if ptype == "CASHLESS":
        payment["label"] = "Картка" if payment_method == PaymentMethod.CARD else "Переказ"
        for k in ("rrn", "auth_code", "card_mask", "payment_system"):
            if card and card.get(k):
                payment[k] = card[k]
    return {"id": receipt_id, "order_id": str(order_id), "goods": goods, "payments": [payment]}


# ESC/POS: Checkbox віддає чек звичайним текстом; команди принтера додаємо самі.

def escpos(text: str, *, codepage: str = "cp1251") -> bytes:
    if codepage not in CODEPAGES:
        raise ValidationError("Кодова сторінка принтера: cp1251 або cp866")
    body = _encode(text.replace("\r\n", "\n"), codepage)
    return b"\x1b@" + bytes([0x1B, 0x74, CODEPAGES[codepage]]) + body + b"\n\n\n\n" + b"\x1dVB\x00"


def escpos_b64(text: str, *, codepage: str = "cp1251") -> str:
    return base64.b64encode(escpos(text, codepage=codepage)).decode("ascii")


# Налаштування: увімкнення й принтер — у БД (директор); ключі — лише змінні середовища.

def keys_configured(cfg) -> bool:
    return bool(cfg.checkbox_login and cfg.checkbox_password and cfg.checkbox_license_key)


def printer_public(s: Session) -> dict:
    return {"enabled": bool(get_setting(s, "checkbox_enabled")), "printer_type": get_setting(s, "checkbox_printer_type") or "none",
            "printer_ip": get_setting(s, "checkbox_printer_ip") or "", "printer_port": int(get_setting(s, "checkbox_printer_port") or 9100),
            "printer_bt_name": get_setting(s, "checkbox_printer_bt_name") or "",
            "printer_width": int(get_setting(s, "checkbox_printer_width") or 32),
            "printer_codepage": get_setting(s, "checkbox_printer_codepage") or "cp1251"}


def settings_public(s: Session, cfg) -> dict:
    out = printer_public(s)
    out["keys_configured"] = keys_configured(cfg)
    out["queue"] = {st: n for st, n in (
        (st, s.query(FiscalReceipt).filter(FiscalReceipt.status == st).count()) for st in ("pending", "error"))}
    return out


def get_settings(s: Session, actor: Actor, cfg) -> dict:
    actor.require(Role.DIRECTOR)
    return settings_public(s, cfg)


def update_settings(s: Session, actor: Actor, cfg, data: dict) -> dict:
    from .terminal import _validate_host

    actor.require(Role.DIRECTOR)
    cur = printer_public(s)
    new = {**cur, **{k: v for k, v in data.items() if v is not None}}
    if not isinstance(new["enabled"], bool):
        raise ValidationError("Checkbox: увімкнено так або ні")
    if new["enabled"] and not keys_configured(cfg):
        raise ValidationError("Спершу задайте ключі Checkbox на сервері: MDS_CHECKBOX_LOGIN, _PASSWORD, _LICENSE_KEY")
    if new["printer_type"] not in PRINTERS:
        raise ValidationError("Принтер: немає, Wi-Fi або Bluetooth")
    new["printer_ip"] = _validate_host(str(new["printer_ip"] or ""))
    if new["printer_type"] == "wifi" and not new["printer_ip"]:
        raise ValidationError("Для Wi-Fi принтера вкажіть його IP-адресу")
    port = new["printer_port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValidationError("Порт принтера — від 1 до 65535")
    if new["printer_width"] not in WIDTHS:
        raise ValidationError("Ширина чека: 32 (58 мм), 42 або 48 (80 мм) символів")
    if new["printer_codepage"] not in CODEPAGES:
        raise ValidationError("Кодова сторінка принтера: cp1251 або cp866")
    new["printer_bt_name"] = str(new["printer_bt_name"] or "").strip()[:64]
    for key, val in new.items():
        if val != cur[key]:
            set_setting(s, actor, "checkbox_" + key, val)
    if new != cur:
        audit(s, actor, "checkbox", 0, "settings", cur, new)
    return settings_public(s, cfg)


def client_config(s: Session) -> dict:
    p = printer_public(s)
    return {k: p[k] for k in ("enabled", "printer_type", "printer_ip", "printer_port", "printer_bt_name")}


def make_client(cfg) -> CheckboxClient:
    return CheckboxClient(cfg.checkbox_api_url, cfg.checkbox_login, cfg.checkbox_password, cfg.checkbox_license_key)


_client_factory = make_client


def set_client_factory(fn) -> None:
    global _client_factory
    _client_factory = fn or make_client


# Черга чеків. Мережа — поза транзакцією БД: 1) запис чека «pending», 2) Checkbox, 3) результат.

def _items(o: Order) -> list[dict]:
    items = []
    if o.amount > 0:
        name = (o.service.name if o.service else None) or "Послуга салону"
        items.append({"code": f"S{o.service_id or 0}", "name": name, "price": o.amount, "qty": 1})
    for p in o.products:
        if not p.refunded:
            items.append({"code": f"P{p.product_id}", "name": p.name, "price": p.sale_price_snapshot_cents, "qty": p.qty})
    return items


def _check_access(actor: Actor, o: Order) -> None:
    if not (actor.is_director or actor.role == "system" or actor.user_id == o.master_id):
        raise Forbidden("Чек можна створити лише для власного замовлення")


def _card_details(s: Session, o: Order) -> dict | None:
    from ..models import CardPayment

    p = s.scalar(select(CardPayment).where(CardPayment.order_id == o.id, CardPayment.status == "paid"))
    if p is None:
        return None
    return {"rrn": p.rrn, "auth_code": p.approval_code, "card_mask": p.masked_pan, "payment_system": p.payment_system}


def enqueue_sell(s: Session, actor: Actor, order_id: int) -> FiscalReceipt:
    if not get_setting(s, "checkbox_enabled"):
        raise DomainError("Фіскалізацію Checkbox вимкнено в налаштуваннях", code="checkbox_disabled")
    o = s.get(Order, order_id)
    if o is None:
        raise NotFound(f"Замовлення #{order_id} не знайдено")
    _check_access(actor, o)
    existing = s.scalar(select(FiscalReceipt).where(FiscalReceipt.order_id == o.id, FiscalReceipt.kind == "sell"))
    if existing is not None:
        return existing
    if o.status != OrderStatus.COMPLETED:
        raise Conflict("Чек створюється лише для оплаченого замовлення")
    items = _items(o)
    total = sum(it["price"] * it["qty"] for it in items)
    if total <= 0:
        raise Conflict("Сума замовлення 0 ₴ — чек не потрібен")
    fr = FiscalReceipt(uuid=str(uuidlib.uuid4()), order_id=o.id, kind="sell", payment_type=o.payment_method, amount=total,
                       status="pending", attempts=0, created_by=actor.user_id, created_at=clock.now(), updated_at=clock.now())
    s.add(fr)
    s.flush()
    o.fiscal_receipt_id = fr.uuid
    audit(s, actor, "fiscal_receipt", fr.id, "enqueue", None, {"order_id": o.id, "kind": "sell", "amount": total})
    return fr


def enqueue_return(s: Session, actor: Actor, order_id: int) -> FiscalReceipt | None:
    sell = s.scalar(select(FiscalReceipt).where(FiscalReceipt.order_id == order_id, FiscalReceipt.kind == "sell"))
    if sell is None:
        return None
    existing = s.scalar(select(FiscalReceipt).where(FiscalReceipt.order_id == order_id, FiscalReceipt.kind == "return"))
    if existing is not None:
        return existing
    fr = FiscalReceipt(uuid=str(uuidlib.uuid4()), order_id=order_id, kind="return", payment_type=sell.payment_type,
                       amount=sell.amount, related_uuid=sell.uuid, status="pending", attempts=0, created_by=actor.user_id,
                       created_at=clock.now(), updated_at=clock.now())
    s.add(fr)
    s.flush()
    audit(s, actor, "fiscal_receipt", fr.id, "enqueue", None, {"order_id": order_id, "kind": "return", "amount": fr.amount})
    return fr


def _ensure_shift(client: CheckboxClient) -> None:
    sh = client.current_shift()
    if sh and sh.get("status") in ("OPENED", "OPENING", "CREATED"):
        return
    close_at = datetime.combine(clock.now().date(), SHIFT_AUTO_CLOSE)
    client.open_shift(auto_close_at=close_at)
    log.info("Checkbox: відкрито зміну ПРРО (автозакриття %s)", close_at.strftime("%H:%M"))


def process(db, cfg, receipt_pk: int) -> dict:
    with db.session() as s:
        fr = s.get(FiscalReceipt, receipt_pk)
        if fr is None:
            raise NotFound("Чек не знайдено")
        if fr.status == "done":
            return public(fr)
        o = s.get(Order, fr.order_id)
        items = _items(o)
        if fr.kind == "return":
            sell_items = items
            if sum(it["price"] * it["qty"] for it in sell_items) != fr.amount:
                sell_items = [{"code": "R", "name": "Повернення за чеком", "price": fr.amount, "qty": 1}]
            items = sell_items
            related = fr.related_uuid
            related_done = s.scalar(select(FiscalReceipt.status).where(FiscalReceipt.uuid == related)) == "done"
        card = _card_details(s, o) if fr.payment_type == PaymentMethod.CARD and fr.kind == "sell" else None
        res = s.execute(update(FiscalReceipt).where(FiscalReceipt.id == fr.id, FiscalReceipt.status.in_(("pending", "error")))
                        .values(attempts=FiscalReceipt.attempts + 1, updated_at=clock.now()))
        if res.rowcount != 1:
            return public(fr)
        snap = {"uuid": fr.uuid, "order_id": fr.order_id, "amount": fr.amount, "payment_type": fr.payment_type, "kind": fr.kind}

    result, error, retry = None, None, False
    if snap["kind"] == "return" and not related_done:
        error, retry = "Чек продажу ще не створено — повернення чекає на нього", True
    else:
        client = None
        try:
            client = _client_factory(cfg)
            _ensure_shift(client)
            if snap["kind"] == "sell":
                result = client.create_receipt(snap["uuid"], snap["order_id"], snap["amount"], snap["payment_type"], items, card=card)
            else:
                result = client.create_return_receipt(snap["uuid"], related, snap["amount"], snap["payment_type"], items,
                                                      snap["order_id"])
        except CheckboxUnavailable as e:
            error, retry = str(e), True
        except (CheckboxError, ValidationError) as e:
            error = str(e) if isinstance(e, CheckboxError) else e.message
        finally:
            if client is not None:
                client.close()

    with db.session() as s:
        fr = s.get(FiscalReceipt, receipt_pk)
        fr.updated_at = clock.now()
        if result is not None:
            fr.status, fr.last_error, fr.done_at = "done", None, clock.now()
            fr.fiscal_code = (str(result.get("fiscal_code")) if result.get("fiscal_code") else None)
            fr.fiscal_date = (str(result.get("fiscal_date")) if result.get("fiscal_date") else None)
            fr.tax_url = (result.get("tax_url") or None)
            audit(s, Actor.system(), "fiscal_receipt", fr.id, "done", None, {"fiscal_code": fr.fiscal_code})
        else:
            fr.last_error = (error or "Помилка Checkbox")[:255]
            fr.status = "pending" if retry and fr.attempts < MAX_ATTEMPTS else "error"
            log.warning("Checkbox: чек %s (замовлення %s) — %s", fr.uuid, fr.order_id, fr.last_error)
        return public(fr)


def receipt_print(db, cfg, receipt_pk: int) -> str | None:
    with db.session() as s:
        fr = s.get(FiscalReceipt, receipt_pk)
        if fr is None or fr.status != "done":
            return None
        p = printer_public(s)
        rid = fr.uuid
    client = _client_factory(cfg)
    try:
        return escpos_b64(client.get_receipt_text(rid, width=p["printer_width"]), codepage=p["printer_codepage"])
    except CheckboxError as e:
        log.warning("Checkbox: текст чека %s недоступний: %s", rid, e)
        return None
    finally:
        client.close()


def fiscalize(db, cfg, actor: Actor, order_id: int) -> tuple[int, dict]:
    with db.session() as s:
        if not keys_configured(cfg) and get_setting(s, "checkbox_enabled"):
            raise DomainError("Не задано ключі Checkbox на сервері", code="checkbox_keys")
        fr = enqueue_sell(s, actor, order_id)
        pk = fr.id
    out = process(db, cfg, pk)
    if out["status"] == "done":
        out["print_base64"] = receipt_print(db, cfg, pk)
        with db.session() as s:
            out["printer"] = client_config(s)
        return 200, out
    if out["status"] == "pending":
        out["message"] = "Чек буде створено пізніше"
        return 202, out
    return 200, out | {"message": f"Чек не створено: {out['last_error']}"}


def retry_pending(db, cfg, limit: int = 20) -> int:
    with db.session() as s:
        if not get_setting(s, "checkbox_enabled") or not keys_configured(cfg):
            return 0
        ids = s.scalars(select(FiscalReceipt.id).where(FiscalReceipt.status == "pending")
                        .order_by(FiscalReceipt.kind.desc(), FiscalReceipt.id).limit(limit)).all()
    done = 0
    for pk in ids:
        if process(db, cfg, pk)["status"] == "done":
            done += 1
    return done


def public(fr: FiscalReceipt) -> dict:
    return {"id": fr.id, "receipt_id": fr.uuid, "order_id": fr.order_id, "kind": fr.kind, "amount": fr.amount,
            "payment_type": PAYMENT_TYPES.get(fr.payment_type, fr.payment_type), "status": fr.status, "attempts": fr.attempts,
            "last_error": fr.last_error, "fiscal_code": fr.fiscal_code, "fiscal_date": fr.fiscal_date, "tax_url": fr.tax_url}


def check_connection(cfg) -> dict:
    if not keys_configured(cfg):
        return {"ok": False, "error": "Не задано ключі Checkbox на сервері (MDS_CHECKBOX_*)"}
    client = _client_factory(cfg)
    try:
        client.signin()
        sh = client.current_shift()
        return {"ok": True, "shift": (sh or {}).get("status") or "CLOSED"}
    except CheckboxError as e:
        return {"ok": False, "error": str(e)}
    finally:
        client.close()


def shift_action(cfg, actor: Actor, action: str) -> dict:
    actor.require(Role.DIRECTOR)
    if action not in ("open", "close"):
        raise ValidationError("Дія зі зміною: open або close")
    if not keys_configured(cfg):
        raise ValidationError("Не задано ключі Checkbox на сервері (MDS_CHECKBOX_*)")
    client = _client_factory(cfg)
    try:
        if action == "open":
            sh = client.current_shift()
            if sh and sh.get("status") in ("OPENED", "OPENING", "CREATED"):
                return {"ok": True, "shift": sh.get("status"), "message": "Зміна ПРРО вже відкрита"}
            sh = client.open_shift(auto_close_at=datetime.combine(clock.now().date(), SHIFT_AUTO_CLOSE))
            return {"ok": True, "shift": sh.get("status") or "OPENING", "message": "Зміну ПРРО відкрито"}
        sh = client.close_shift()
        return {"ok": True, "shift": sh.get("status") or "CLOSING", "message": "Зміну ПРРО закрито (Z-звіт)"}
    except CheckboxUnavailable as e:
        raise Conflict(f"{e}. Спробуйте пізніше") from None
    except CheckboxError as e:
        raise Conflict(str(e)) from None
    finally:
        client.close()


def test_print(s: Session, actor: Actor) -> dict:
    actor.require(Role.DIRECTOR)
    p = printer_public(s)
    w = p["printer_width"]
    text = "\n".join(["M&D".center(w), "Тестовий друк MDS Connect".center(w), "-" * w,
                      "АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ", "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя", "0123456789 грн",
                      "-" * w, f"Кодова сторінка: {p['printer_codepage']}", f"Ширина: {w} символів"])
    return {"print_base64": escpos_b64(text, codepage=p["printer_codepage"]), "printer": client_config(s)}


def on_refund(s: Session, actor: Actor, order_id: int) -> FiscalReceipt | None:
    """Повернення замовлення: якщо був фіскальний чек продажу — ставимо в чергу чек повернення."""
    return enqueue_return(s, actor, order_id)
