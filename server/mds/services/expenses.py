"""Витрати салону: оренда, комунальні, реклама, матеріали, обладнання, господарські, інше.

Витрати — для управлінського обліку і прибутковості; дохід ФОП на єдиному податку вони НЕ зменшують.
До витрати можна прикріпити фото/файл документа (зберігається в базі — потрапляє в резервні копії)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ValidationError
from ..models import Expense, ExpenseCategory, ExpenseFile, Role
from .common import Actor, audit, get_or_404, ledger


def expense_public(e: Expense) -> dict:
    return {
        "id": e.id,
        "category": e.category,
        "amount": e.amount,
        "spent_on": e.spent_on.isoformat(),
        "description": e.description,
        "payment_method": e.payment_method,
        "is_deleted": e.is_deleted,
    }


PAY_METHODS = ("cash", "card", "account")  # готівка | картка | з рахунку ФОП
FILE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}
MAX_FILE = 5 * 1024 * 1024
MAX_FILES = 10


def _validate(category: str | None, amount: int | None, payment_method: str | None = None) -> None:
    if category is not None and category not in ExpenseCategory.ALL:
        raise ValidationError("Невідома категорія витрат")
    if payment_method is not None and payment_method not in PAY_METHODS:
        raise ValidationError("Спосіб оплати: готівка, картка або рахунок")
    if amount is not None and amount <= 0:
        raise ValidationError("Сума витрати має бути більшою за 0")


def create_expense(s: Session, actor: Actor, *, category: str, amount: int, spent_on: date, description: str | None = None,
                   payment_method: str | None = None) -> Expense:
    actor.require(Role.DIRECTOR)
    _validate(category, amount, payment_method)
    e = Expense(category=category, amount=amount, spent_on=spent_on, description=description,
                payment_method=payment_method, created_by=actor.user_id or 0)
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
    _validate(data.get("category"), data.get("amount"), data.get("payment_method"))
    before = expense_public(e)
    ledger(s, actor, "expense_reversal", "expense", e.id, amount=e.amount, salon_amount=e.amount)
    for k in ("category", "amount", "spent_on", "description", "payment_method"):
        if k in data and data[k] is not None:
            setattr(e, k, data[k])
    ledger(s, actor, "expense", "expense", e.id, amount=-e.amount, salon_amount=-e.amount,
           details={"category": e.category, "spent_on": e.spent_on, "reason": reason})
    audit(s, actor, "expense", e.id, "update", before, expense_public(e), reason)
    return e


def delete_expense(s: Session, actor: Actor, expense_id: int, reason: str | None = None) -> None:
    """М'яке видалення — запис залишається в історії."""
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


# ------------------------------------------------------------------ документи до витрат
def file_public(f: ExpenseFile) -> dict:
    return {"id": f.id, "expense_id": f.expense_id, "filename": f.filename, "content_type": f.content_type,
            "size": f.size, "created_at": f.created_at.isoformat()}


def files_for(s: Session, expense_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    if expense_ids:
        for f in s.scalars(select(ExpenseFile).where(ExpenseFile.expense_id.in_(expense_ids)).order_by(ExpenseFile.id)):
            out.setdefault(f.expense_id, []).append(file_public(f))
    return out


def add_file(s: Session, actor: Actor, expense_id: int, *, filename: str, content_type: str, data: bytes) -> ExpenseFile:
    """Фото/PDF документа до витрати: jpg, png, webp, pdf; до 5 МБ; до 10 файлів на витрату."""
    actor.require(Role.DIRECTOR)
    e: Expense = get_or_404(s, Expense, expense_id, "Витрату")
    if e.is_deleted:
        raise ValidationError("Витрату видалено")
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct not in FILE_TYPES:
        raise ValidationError("Файл: фото (jpg, png, webp) або PDF")
    if not data:
        raise ValidationError("Порожній файл")
    if len(data) > MAX_FILE:
        raise ValidationError("Файл завеликий — до 5 МБ")
    sig = {"image/jpeg": b"\xff\xd8", "image/png": b"\x89PNG", "application/pdf": b"%PDF"}.get(ct)
    if (sig and not data.startswith(sig)) or (ct == "image/webp" and data[8:12] != b"WEBP"):
        raise ValidationError("Вміст файлу не відповідає його типу")
    if (s.scalar(select(func.count(ExpenseFile.id)).where(ExpenseFile.expense_id == expense_id)) or 0) >= MAX_FILES:
        raise ValidationError("До однієї витрати — не більше 10 файлів")
    name = " ".join((filename or "").replace("/", " ").replace("\\", " ").split())[:150] or f"document{FILE_TYPES[ct]}"
    f = ExpenseFile(expense_id=expense_id, filename=name, content_type=ct, size=len(data), data=data, created_by=actor.user_id)
    s.add(f)
    s.flush()
    audit(s, actor, "expense", expense_id, "file_add", None, file_public(f))
    return f


def get_file(s: Session, actor: Actor, file_id: int) -> ExpenseFile:
    actor.require(Role.DIRECTOR)
    return get_or_404(s, ExpenseFile, file_id, "Файл")


def delete_file(s: Session, actor: Actor, file_id: int) -> None:
    actor.require(Role.DIRECTOR)
    f: ExpenseFile = get_or_404(s, ExpenseFile, file_id, "Файл")
    audit(s, actor, "expense", f.expense_id, "file_delete", file_public(f), None)
    s.delete(f)
