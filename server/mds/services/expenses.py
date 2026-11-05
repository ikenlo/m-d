from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ValidationError
from ..models import Expense, ExpenseCategory, Role
from .common import Actor, audit, get_or_404, ledger


def expense_public(e: Expense) -> dict:
    return {
        "id": e.id,
        "category": e.category,
        "amount": e.amount,
        "spent_on": e.spent_on.isoformat(),
        "description": e.description,
        "is_deleted": e.is_deleted,
    }


def _validate(category: str | None, amount: int | None) -> None:
    if category is not None and category not in ExpenseCategory.ALL:
        raise ValidationError("Невідома категорія витрат")
    if amount is not None and amount <= 0:
        raise ValidationError("Сума витрати має бути більшою за 0")


def create_expense(s: Session, actor: Actor, *, category: str, amount: int, spent_on: date, description: str | None = None) -> Expense:
    actor.require(Role.DIRECTOR)
    _validate(category, amount)
    e = Expense(category=category, amount=amount, spent_on=spent_on, description=description, created_by=actor.user_id or 0)
    s.add(e)
    s.flush()
    ledger(s, actor, "expense", "expense", e.id, amount=-amount, salon_amount=-amount,
           details={"category": category, "spent_on": spent_on})
    audit(s, actor, "expense", e.id, "create", None, expense_public(e))
    return e


def update_expense(s: Session, actor: Actor, expense_id: int, data: dict, reason: str | None = None) -> Expense:
    actor.require(Role.DIRECTOR)
    e: Expense = get_or_404(s, Expense, expense_id, "Витрату")
    if e.is_deleted:
        raise ValidationError("Витрату видалено")
    _validate(data.get("category"), data.get("amount"))
    before = expense_public(e)
    ledger(s, actor, "expense_reversal", "expense", e.id, amount=e.amount, salon_amount=e.amount)
    for k in ("category", "amount", "spent_on", "description"):
        if k in data and data[k] is not None:
            setattr(e, k, data[k])
    ledger(s, actor, "expense", "expense", e.id, amount=-e.amount, salon_amount=-e.amount,
           details={"category": e.category, "spent_on": e.spent_on, "reason": reason})
    audit(s, actor, "expense", e.id, "update", before, expense_public(e), reason)
    return e


def delete_expense(s: Session, actor: Actor, expense_id: int, reason: str | None = None) -> None:
    actor.require(Role.DIRECTOR)
    e: Expense = get_or_404(s, Expense, expense_id, "Витрату")
    if e.is_deleted:
        return
    e.is_deleted = True
    ledger(s, actor, "expense_reversal", "expense", e.id, amount=e.amount, salon_amount=e.amount, details={"reason": reason})
    audit(s, actor, "expense", e.id, "delete", expense_public(e), None, reason)


def list_expenses(s: Session, actor: Actor, date_from: date, date_to: date) -> list[Expense]:
    actor.require(Role.DIRECTOR)
    return list(
        s.scalars(
            select(Expense)
            .where(Expense.spent_on >= date_from, Expense.spent_on <= date_to, Expense.is_deleted.is_(False))
            .order_by(Expense.spent_on.desc(), Expense.id.desc())
        )
    )
