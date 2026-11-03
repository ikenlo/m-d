"""Єдине джерело «поточного часу».

Салон один і працює в одному часовому поясі, тому в БД зберігаються
наївні (без tzinfo) дати-час у локальному часі салону.
Тести підміняють час через `set_now`.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_tz = ZoneInfo("Europe/Kyiv")
_fixed: datetime | None = None


def configure(tz_name: str) -> None:
    global _tz
    _tz = ZoneInfo(tz_name)


def now() -> datetime:
    if _fixed is not None:
        return _fixed
    return datetime.now(_tz).replace(tzinfo=None, microsecond=0)


def set_now(value: datetime | None) -> None:
    """Лише для тестів: зафіксувати поточний час."""
    global _fixed
    _fixed = value
