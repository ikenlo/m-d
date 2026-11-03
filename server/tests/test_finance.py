import pytest

from mds.errors import ValidationError
from mds.money import fmt, percent_of
from mds.services.finance import compute_split


def test_default_50_50_materials_on_salon():
    s = compute_split(100000, 6000, 5000, "salon")
    assert s.master == 50000
    assert s.salon == 44000
    assert s.salon_gross == 50000
    assert s.master + s.salon + s.materials == s.amount


def test_materials_on_master():
    s = compute_split(100000, 6000, 5000, "master")
    assert s.master == 44000
    assert s.salon == 50000


def test_materials_split_proportionally():
    s = compute_split(100000, 6000, 5000, "split")
    assert s.master == 47000
    assert s.salon == 47000


@pytest.mark.parametrize("amount,bp", [(33333, 5000), (1, 5000), (99999, 3333), (12345, 6667), (0, 5000)])
@pytest.mark.parametrize("mode", ["salon", "master", "split"])
def test_split_always_balances(amount, bp, mode):
    s = compute_split(amount, 7, bp, mode)
    assert s.master + s.salon + s.materials == amount


def test_rounding_half_up():
    assert percent_of(1, 5000) == 1  # 0.5 коп -> 1
    assert percent_of(-1, 5000) == -1
    assert percent_of(3, 3333) == 1


def test_invalid_inputs():
    with pytest.raises(ValidationError):
        compute_split(-1, 0, 5000, "salon")
    with pytest.raises(ValidationError):
        compute_split(100, 0, 10001, "salon")
    with pytest.raises(ValidationError):
        compute_split(100, 0, 5000, "weird")


def test_fmt():
    assert fmt(123456) == "1 234.56 ₴"
    assert fmt(50000) == "500 ₴"
    assert fmt(-150) == "-1.50 ₴"
