from __future__ import annotations

import logging
import os
import time
from datetime import date

from sqlalchemy.orm import Session

from ...models import Role
from ..common import Actor
from .local_llama import LocalLlamaProvider
from .provider import AIError, AIProvider
from .rules import RulesProvider

log = logging.getLogger("mds.ai")
CACHE_TTL = 3600
_cache: dict[tuple, tuple[float, dict]] = {}


def ai_config() -> dict:
    return {"provider": (os.getenv("MDS_AI_PROVIDER") or "rules").strip().lower(),
            "llama_url": os.getenv("MDS_AI_LLAMA_URL") or "http://localhost:8080/v1"}


def get_provider() -> AIProvider:
    cfg = ai_config()
    if cfg["provider"] == "llama":
        return LocalLlamaProvider(cfg["llama_url"])
    return RulesProvider()


def facts(s: Session, actor: Actor, d_from: date, d_to: date) -> dict:
    from sqlalchemy import func, select

    from ...models import Order, OrderStatus
    from ..analytics import _bounds, by_service, dashboard

    d = dashboard(s, actor, d_from, d_to)
    top = by_service(s, actor, d_from, d_to)
    start, end = _bounds(d_from, d_to)
    neg = s.scalar(select(func.count(Order.id)).where(Order.status == OrderStatus.COMPLETED, Order.salon_earning < 0,
                                                      Order.closed_at >= start, Order.closed_at < end)) or 0
    return {"period": [d_from.isoformat(), d_to.isoformat()], "revenue": d["revenue"], "real_revenue": d["real_revenue"],
            "discounts": d["discounts"], "masters_share": d["masters_share"], "materials": d["materials"],
            "gross_profit": d["gross_profit"], "net_profit": d["net_profit"], "profitability_bp": d["profitability_bp"],
            "expenses": {k: v for k, v in d["expenses"].items() if isinstance(v, int)},
            "orders_count": d["orders_count"], "cancellations": d["cancellations"],
            "unpaid_overdue": d["unpaid_orders"]["overdue_count"], "negative_salon_orders": int(neg),
            "top_service": top[0]["service_name"] if top else None}


def recommend(s: Session, actor: Actor, d_from: date, d_to: date, refresh: bool = False) -> dict:
    actor.require(Role.DIRECTOR)
    provider = get_provider()
    key = (provider.name, d_from, d_to)
    hit = _cache.get(key)
    if hit and not refresh and time.time() - hit[0] < CACHE_TTL:
        return hit[1] | {"cached": True}
    f = facts(s, actor, d_from, d_to)
    used = provider.name
    try:
        items = provider.recommend(f)
    except AIError as e:
        log.warning("AI-провайдер %s недоступний, використано правила: %s", provider.name, e)
        items, used = RulesProvider().recommend(f), "rules"
    out = {"provider": used, "items": items, "from": d_from.isoformat(), "to": d_to.isoformat(), "generated_at": time.time()}
    _cache[key] = (time.time(), out)
    return out | {"cached": False}


__all__ = ["AIError", "AIProvider", "LocalLlamaProvider", "RulesProvider", "ai_config", "get_provider", "recommend"]
