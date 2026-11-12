"""POS-термінал ПриватБанку (PAX/Newland) по локальній мережі: ECR JSON через TCP (docs/TERMINAL.md).

Формат повідомлень зібрано за загальнодоступним описом ECR JSON ПриватБанку без документації банку —
перед першою реальною оплатою перевірити на терміналі. Усе, що залежить від формату, — у build_*/parse_*.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
import time

from sqlalchemy.orm import Session

from ..errors import ValidationError
from .common import Actor, Role, audit, get_setting, set_setting

log = logging.getLogger("mds.terminal")

TERMINATOR = b"\x00"
PAY_TIMEOUT = 60.0
CONNECT_TIMEOUT = 5.0
PING_TIMEOUT = 5.0
MAX_MESSAGE = 64 * 1024
APPROVED = {"00", "000", "0000"}

MSG_DECLINED = "Термінал відхилив оплату"
MSG_TIMEOUT = "Таймаут з'єднання з терміналом"
MSG_NO_LINK = "Немає з'єднання з терміналом"
MSG_BAD_REPLY = "Термінал надіслав незрозумілу відповідь"


def amount_str(kop: int) -> str:
    return f"{kop // 100}.{kop % 100:02d}"


def build_purchase(amount_kop: int) -> dict:
    return {"method": "Purchase", "step": 0,
            "params": {"amount": amount_str(amount_kop), "discount": "", "merchantId": "0", "facepay": "false"}}


def build_ping() -> dict:
    return {"method": "PingDevice", "step": 0}


def build_interrupt() -> dict:
    return {"method": "ServiceMessage", "step": 0, "params": {"msgType": "interrupt"}}


def encode(msg: dict) -> bytes:
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + TERMINATOR


def parse_purchase(msg: dict) -> dict:
    p = msg.get("params") or {}
    code = str(p.get("responseCode") or p.get("response_code") or "").strip()
    rrn = str(p.get("rrn") or "").strip() or None
    if not msg.get("error") and code in APPROVED and rrn:
        return {"success": True, "rrn": rrn, "response_code": code,
                "approval_code": (p.get("approvalCode") or None), "masked_pan": (p.get("pan") or None),
                "payment_system": (p.get("paymentSystem") or p.get("issuerName") or None)}
    detail = code or str(msg.get("errorDescription") or "").strip()
    return {"success": False, "response_code": code or None,
            "error": f"{MSG_DECLINED} (код {code})" if code else MSG_DECLINED, "detail": detail[:200] or None}


class TerminalLinkError(Exception):
    def __init__(self, message: str, *, sent: bool):
        super().__init__(message)
        self.sent = sent


class PrivatBankTerminalClient:

    def __init__(self, host: str, port: int, *, timeout: float = PAY_TIMEOUT, connect_timeout: float = CONNECT_TIMEOUT):
        self.host, self.port, self.timeout, self.connect_timeout = host, int(port), timeout, connect_timeout

    @classmethod
    def from_settings(cls, s: Session, **kw) -> "PrivatBankTerminalClient":
        cfg = settings_public(s)
        if not cfg["enabled"] or not cfg["ip"]:
            raise ValidationError("POS-термінал вимкнено або не задано IP-адресу")
        return cls(cfg["ip"], cfg["port"], **kw)

    def _exchange(self, msg: dict, *, timeout: float, until_method: str | None = None) -> dict:
        sent = False
        try:
            with socket.create_connection((self.host, self.port), timeout=self.connect_timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(encode(msg))
                sent = True
                deadline = time.monotonic() + timeout
                buf = b""
                while True:
                    while TERMINATOR not in buf:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise TimeoutError
                        sock.settimeout(left)
                        chunk = sock.recv(4096)
                        if not chunk:
                            raise TerminalLinkError(MSG_NO_LINK, sent=sent)
                        buf += chunk
                        if len(buf) > MAX_MESSAGE:
                            raise TerminalLinkError(MSG_BAD_REPLY, sent=sent)
                    raw, buf = buf.split(TERMINATOR, 1)
                    try:
                        reply = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        raise TerminalLinkError(MSG_BAD_REPLY, sent=sent) from None
                    # Проміжні службові повідомлення термінала (напр. «вставте картку») пропускаємо.
                    if not isinstance(reply, dict) or (until_method and reply.get("method") != until_method):
                        continue
                    return reply
        except TerminalLinkError:
            raise
        except (TimeoutError, socket.timeout):
            raise TerminalLinkError(MSG_TIMEOUT, sent=sent) from None
        except OSError:
            raise TerminalLinkError(MSG_TIMEOUT if sent else MSG_NO_LINK, sent=sent) from None

    def process_payment(self, amount: int, order_id: str) -> dict:
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValidationError("Сума оплати має бути додатною (копійки)")
        log.info("POS: оплата %s коп., %s", amount, order_id)
        try:
            reply = self._exchange(build_purchase(amount), timeout=self.timeout, until_method="Purchase")
        except TerminalLinkError as e:
            # Запит пішов на термінал, а відповіді немає: оплата могла пройти — результат невідомий.
            return {"success": False, "error": str(e), "uncertain": e.sent}
        res = parse_purchase(reply)
        res["uncertain"] = False
        return res

    def ping(self) -> dict:
        t0 = time.monotonic()
        try:
            reply = self._exchange(build_ping(), timeout=PING_TIMEOUT, until_method="PingDevice")
        except TerminalLinkError as e:
            return {"ok": False, "error": str(e)}
        ms = round((time.monotonic() - t0) * 1000)
        if reply.get("error"):
            return {"ok": False, "error": str(reply.get("errorDescription") or "Термінал повідомив про помилку")[:200]}
        return {"ok": True, "ms": ms}

    def interrupt(self) -> bool:
        try:
            self._exchange(build_interrupt(), timeout=PING_TIMEOUT)
            return True
        except TerminalLinkError as e:
            return e.sent


def _validate_host(ip: str) -> str:
    from ..config import is_production

    ip = (ip or "").strip()
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        raise ValidationError("IP-адреса термінала: напр. 192.168.1.50") from None
    if addr.is_loopback and not is_production():
        return ip
    if not addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
        raise ValidationError("IP-адреса термінала має бути з локальної мережі салону (192.168.x.x, 10.x.x.x)")
    return ip


def settings_public(s: Session) -> dict:
    return {"enabled": bool(get_setting(s, "pos_terminal_enabled")), "ip": get_setting(s, "pos_terminal_ip") or "",
            "port": int(get_setting(s, "pos_terminal_port") or 2000)}


def get_settings(s: Session, actor: Actor) -> dict:
    actor.require(Role.DIRECTOR)
    return settings_public(s)


def update_settings(s: Session, actor: Actor, *, enabled: bool | None = None, ip: str | None = None,
                    port: int | None = None) -> dict:
    actor.require(Role.DIRECTOR)
    cur = settings_public(s)
    new = {"enabled": cur["enabled"] if enabled is None else bool(enabled),
           "ip": cur["ip"] if ip is None else _validate_host(ip),
           "port": cur["port"] if port is None else port}
    if isinstance(new["port"], bool) or not isinstance(new["port"], int) or not 1 <= new["port"] <= 65535:
        raise ValidationError("Порт термінала — від 1 до 65535")
    if new["enabled"] and not new["ip"]:
        raise ValidationError("Щоб увімкнути термінал, вкажіть його IP-адресу")
    for key, field in (("pos_terminal_enabled", "enabled"), ("pos_terminal_ip", "ip"), ("pos_terminal_port", "port")):
        if new[field] != cur[field]:
            set_setting(s, actor, key, new[field])
    if new != cur:
        audit(s, actor, "pos_terminal", 0, "settings", cur, new)
    return new


def check_connection(host: str, port: int) -> dict:
    return PrivatBankTerminalClient(host, port).ping()
