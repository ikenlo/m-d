from __future__ import annotations

from dataclasses import dataclass

from ..errors import ValidationError
from ..money import BP_100

DEFAULT_MASTER_RATE_BP = 5000


@dataclass(frozen=True)
class Split:
    price: int
    amount: int
    materials: int
    master: int
    salon: int

    @property
    def discount(self) -> int:
        return self.price - self.amount

    @property
    def salon_gross(self) -> int:
        return self.amount - self.master


def compute_split(price_snapshot: int, amount_paid: int, materials_cost: int,
                  master_rate_bp: int = DEFAULT_MASTER_RATE_BP) -> Split:
    """Канон (docs/FINANCE.md §2): майстер = P × ставка // 10000, салон = A − майстер − M."""
    for v in (price_snapshot, amount_paid, materials_cost, master_rate_bp):
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValidationError("Суми і ставка мають бути цілими числами (копійки, bp)")
    if price_snapshot < 0 or amount_paid < 0 or materials_cost < 0:
        raise ValidationError("Ціна, сума та матеріали не можуть бути від'ємними")
    if not (0 <= master_rate_bp <= BP_100):
        raise ValidationError("Ставка майстра має бути від 0 до 100%")
    master = (price_snapshot * master_rate_bp) // BP_100
    salon = amount_paid - master - materials_cost
    assert amount_paid == master + materials_cost + salon
    return Split(price=price_snapshot, amount=amount_paid, materials=materials_cost, master=master, salon=salon)
