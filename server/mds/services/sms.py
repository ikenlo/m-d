from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger("mds.sms")
BASE = "https://api.turbosms.ua"
TIMEOUT = 10.0
OK_CODES = (0, 800, 801, 802, 803)


SENDING_ENABLED = False
DISABLED_REASON = "SMS із MDS Connect вимкнено — їх надсилає MDs Manager (D-6a)"


def configured() -> bool:
    return bool(os.environ.get("MDS_TURBOSMS_TOKEN") and os.environ.get("MDS_TURBOSMS_SENDER"))


def _call(path: str, body: dict) -> dict:
    token = os.environ.get("MDS_TURBOSMS_TOKEN", "")
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310
        return json.loads(r.read().decode() or "{}")


def send_one(phone: str, text: str, channel: str = "viber_sms") -> dict:
    if not SENDING_ENABLED:
        return {"ok": False, "message_id": None, "error": "disabled"}
    if not configured():
        return {"ok": False, "message_id": None, "error": "not_configured"}
    sender = os.environ.get("MDS_TURBOSMS_SENDER", "")
    body: dict = {"recipients": [phone.lstrip("+")]}
    if channel in ("sms", "viber_sms"):
        body["sms"] = {"sender": sender, "text": text}
    if channel in ("viber", "viber_sms"):
        body["viber"] = {"sender": sender, "text": text}
    try:
        data = _call("/message/send.json", body)
    except Exception as e:  # noqa: BLE001
        log.warning("TurboSMS: помилка надсилання: %s", type(e).__name__)
        return {"ok": False, "message_id": None, "error": "Сервіс SMS недоступний"}
    res = (data.get("response_result") or [{}])[0] or {}
    ok = data.get("response_code") in OK_CODES and res.get("response_code", 0) in OK_CODES
    return {"ok": ok, "message_id": res.get("message_id"), "error": None if ok else str(res.get("response_status") or data.get("response_status") or "помилка")[:200]}


def balance() -> float | None:
    if not configured():
        return None
    try:
        data = _call("/user/balance.json", {})
        return float((data.get("response_result") or {}).get("balance"))
    except Exception:  # noqa: BLE001
        return None


def statuses(message_ids: list[str]) -> dict[str, str]:
    if not configured() or not message_ids:
        return {}
    try:
        data = _call("/message/status.json", {"messages": message_ids[:200]})
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for r in data.get("response_result") or []:
        st = str(r.get("status") or "").lower()
        out[str(r.get("message_id"))] = "delivered" if "deliver" in st or "read" in st else (
            "failed" if any(x in st for x in ("fail", "reject", "expired", "undeliv", "error")) else "sent")
    return out


def send(phones: list[str], text: str, channel: str = "viber_sms", timeout: float = TIMEOUT) -> dict:
    sent = sum(1 for p in phones if send_one(p, text, channel)["ok"])
    return {"ok": bool(sent), "sent": sent, "error": None if sent else "не надіслано"}
