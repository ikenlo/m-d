"""Гроші зберігаються як цілі копійки (int). Відсотки — у базисних пунктах (5000 = 50%)."""

from __future__ import annotations

BP_100 = 10_000


def percent_of(amount: int, bp: int) -> int:
    """amount * bp / 10000 з округленням half-up (симетрично для від'ємних)."""
    sign = -1 if amount < 0 else 1
    return sign * ((abs(amount) * bp + BP_100 // 2) // BP_100)


def fmt(kop: int) -> str:
    """12345 -> '123.45 ₴' (для повідомлень бота та звітів)."""
    sign = "-" if kop < 0 else ""
    kop = abs(kop)
    whole, frac = divmod(kop, 100)
    whole_s = f"{whole:,}".replace(",", " ")
    return f"{sign}{whole_s}.{frac:02d} ₴" if frac else f"{sign}{whole_s} ₴"
