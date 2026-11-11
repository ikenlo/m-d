from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Order, User
from ..money import fmt
from . import notifications

EARNINGS_CHANGE_THRESHOLD = 5000


def raise_alert(s: Session, kind: str, text: str, *, entity: str | None = None, entity_id: int | None = None,
                master_id: int | None = None, to_directors: bool = True, exclude_user_id: int | None = None) -> int:
    n = 0
    if to_directors:
        n += notifications.notify_directors(s, text, exclude_user_id=exclude_user_id)
    if master_id:
        m = s.get(User, master_id)
        if m is not None and m.id != exclude_user_id:
            n += notifications.enqueue(s, m.telegram_chat_id, text)
    return n


def earnings_changed(s: Session, o: Order, before: int, reason: str) -> None:
    text = (f"✏️ Виправлено замовлення #{o.id} ({o.service.name if o.service else 'товари'}).\n"
            f"Заробіток майстра: {fmt(before)} → {fmt(o.master_earning)}\nПричина: {reason}")
    big = abs(o.master_earning - before) > EARNINGS_CHANGE_THRESHOLD
    raise_alert(s, "earnings_change", text, entity="order", entity_id=o.id, master_id=o.master_id, to_directors=big)


def order_voided(s: Session, o: Order, reason: str) -> None:
    title = "↩️ Повернення" if o.status == "refunded" else "🚫 Скасовано замовлення"
    raise_alert(s, "cancellation", f"{title} #{o.id}: {fmt(o.amount)}\nПричина: {reason}",
                entity="order", entity_id=o.id, master_id=o.master_id)


def check_master_balance(s: Session, master_id: int) -> None:
    from .orders import master_balance

    b = master_balance(s, master_id)
    if b["balance"] < 0:
        m = s.get(User, master_id)
        raise_alert(s, "master_negative_balance",
                    f"🔴 Від'ємний баланс майстра {m.full_name if m else master_id}: {fmt(b['balance'])} (переплата). "
                    "Буде утримано з наступної виплати.", entity="user", entity_id=master_id)
