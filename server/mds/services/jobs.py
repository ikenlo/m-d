from __future__ import annotations

import logging

from sqlalchemy.orm import Session

log = logging.getLogger("mds.jobs")


def hourly(s: Session) -> dict:
    from . import idempotency

    out = {"idempotency_deleted": idempotency.cleanup(s)}
    if any(out.values()):
        log.info("Щогодинні задачі: %s", out)
    return out
