"""SMS / Viber клієнтам через TurboSMS (український сервіс; Viber з запасним SMS).

Ключ — лише змінна сервера MDS_TURBOSMS_TOKEN (кабінет TurboSMS → API), відправник — MDS_TURBOSMS_SENDER
(зареєстроване в TurboSMS ім'я, напр. «MD Salon»). Без ключа нічого не надсилається: панель показує,
кому подзвонити. Вмикається налаштуванням `sms_enabled`. Ключ не логується.
Документація API: https://turbosms.ua/api.html (POST /message/send.json, Authorization: Bearer).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger("mds.sms")
API = "https://api.turbosms.ua/message/send.json"


def configured() -> bool:
    return bool(os.environ.get("MDS_TURBOSMS_TOKEN") and os.environ.get("MDS_TURBOSMS_SENDER"))


def send(phones: list[str], text: str, channel: str = "viber_sms", timeout: float = 10.0) -> dict:
    """Надіслати один текст кільком номерам. channel: sms | viber | viber_sms (Viber, а хто не має — SMS).
    Повертає {"ok": bool, "sent": n, "error": str|None}. Не кидає винятків — запис уже зсунуто."""
    token, sender = os.environ.get("MDS_TURBOSMS_TOKEN"), os.environ.get("MDS_TURBOSMS_SENDER")
    recipients = sorted({p.lstrip("+") for p in phones if p})
    if not (token and sender) or not recipients:
        return {"ok": False, "sent": 0, "error": "SMS не налаштовано" if recipients else None}
    body: dict = {"recipients": recipients}
    if channel in ("sms", "viber_sms"):
        body["sms"] = {"sender": sender, "text": text}
    if channel in ("viber", "viber_sms"):
        body["viber"] = {"sender": sender, "text": text}
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — фіксована https-адреса
            data = json.loads(r.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001
        log.warning("TurboSMS: помилка надсилання (%s номерів): %s", len(recipients), type(e).__name__)
        return {"ok": False, "sent": 0, "error": "Сервіс SMS недоступний"}
    ok = data.get("response_code") in (0, 800, 801, 802, 803)  # 0/80x — прийнято до надсилання
    if not ok:
        log.warning("TurboSMS: %s %s", data.get("response_code"), data.get("response_status"))
    return {"ok": ok, "sent": len(recipients) if ok else 0, "error": None if ok else str(data.get("response_status") or "помилка")}
