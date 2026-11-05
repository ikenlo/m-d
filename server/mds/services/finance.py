from __future__ import annotations

from dataclasses import dataclass

from ..errors import ValidationError
from ..models import MaterialsMode
from ..money import BP_100, percent_of


@dataclass(frozen=True)
class Split:
    amount: int
    materials: int
    master: int
    salon: int

    @property
    def salon_gross(self) -> int:
        return self.amount - self.master


def compute_split(amount: int, materials: int, share_bp: int, mode: str) -> Split:
    if amount < 0 or materials < 0:
        raise ValidationError("Сума та матеріали не можуть бути від'ємними")
    if not (0 <= share_bp <= BP_100):
        raise ValidationError("Відсоток майстра має бути від 0 до 100%")
    if mode == MaterialsMode.SALON:
        master = percent_of(amount, share_bp)
    elif mode == MaterialsMode.MASTER:
        master = percent_of(amount, share_bp) - materials
    elif mode == MaterialsMode.SPLIT:
        master = percent_of(amount - materials, share_bp)
    else:
        raise ValidationError("Невідомий режим обліку матеріалів")
    salon = amount - materials - master
    return Split(amount=amount, materials=materials, master=master, salon=salon)
