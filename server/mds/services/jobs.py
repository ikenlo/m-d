from __future__ import annotations

import logging

from sqlalchemy.orm import Session

log = logging.getLogger("mds.jobs")


def hourly(s: Session) -> dict:
    from .. import clock
    from . import alerts, idempotency, vacation
    from .common import Actor, get_setting, set_setting

    out: dict = {"idempotency_deleted": idempotency.cleanup(s), "vacation_off": vacation.auto_off(s)}
    out["alerts"] = alerts.hourly_checks(s)
    now = clock.now()
    today = now.date().isoformat()
    if now.hour >= int(get_setting(s, "alerts_digest_hour") or 9) and get_setting(s, "jobs_daily_last") != today:
        out["daily"] = alerts.daily_checks(s, now.date())
        set_setting(s, Actor.system(), "jobs_daily_last", today)
    if any(v for v in out.values() if not isinstance(v, dict)) or out.get("daily"):
        log.info("Щогодинні задачі: %s", out)
    return out
