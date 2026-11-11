import random

import pytest

from mds.errors import ValidationError
from mds.money import fmt, percent_of
from mds.services.finance import compute_split

UAH = 100


def test_canon_example_1_no_discount():
    sp = compute_split(1000 * UAH, 1000 * UAH, 200 * UAH, 5000)
    assert (sp.master, sp.salon, sp.discount) == (500 * UAH, 300 * UAH, 0)


def test_canon_example_2_discount_on_salon_master_protected():
    sp = compute_split(1000 * UAH, 800 * UAH, 200 * UAH, 5000)
    assert sp.master == 500 * UAH
    assert sp.salon == 100 * UAH
    assert sp.discount == 200 * UAH


def test_canon_example_3_negative_salon_is_allowed():
    sp = compute_split(1000 * UAH, 600 * UAH, 200 * UAH, 5000)
    assert sp.master == 500 * UAH
    assert sp.salon == -100 * UAH


def test_materials_not_deducted_before_split():
    assert compute_split(1000 * UAH, 1000 * UAH, 999 * UAH, 5000).master == 500 * UAH


def test_odd_kopeck_goes_to_salon():
    sp = compute_split(1, 1, 0, 5000)
    assert (sp.master, sp.salon) == (0, 1)
    sp = compute_split(33333, 33333, 0, 5000)
    assert (sp.master, sp.salon) == (16666, 16667)


def test_default_rate_is_50_percent():
    assert compute_split(10001, 10001, 0).master == 5000


def test_property_invariant_and_ints():
    rnd = random.Random(42)
    for _ in range(20000):
        p = rnd.randint(0, 5_000_000)
        a = rnd.randint(0, p + 1_000_000)
        m = rnd.randint(0, 2_000_000)
        r = rnd.choice([5000, rnd.randint(0, 10000)])
        sp = compute_split(p, a, m, r)
        assert a == sp.master + sp.materials + sp.salon
        assert sp.master == (p * r) // 10000
        assert sp.master >= 0
        assert sp.discount == p - a
        assert all(isinstance(x, int) for x in (sp.master, sp.salon, sp.materials, sp.discount))


def test_master_independent_of_paid_amount_and_materials():
    base = compute_split(70000, 70000, 0, 5000).master
    for a, m in ((0, 0), (1, 5000), (50000, 70000), (90000, 1)):
        assert compute_split(70000, a, m, 5000).master == base


@pytest.mark.parametrize("args", [(-1, 0, 0, 5000), (0, -1, 0, 5000), (0, 0, -1, 5000), (100, 100, 0, 10001),
                                  (100, 100, 0, -1), (100.0, 100, 0, 5000), (100, 100, 0, 0.5)])
def test_invalid_inputs(args):
    with pytest.raises(ValidationError):
        compute_split(*args)


def test_percent_of_and_fmt_helpers():
    assert percent_of(10000, 5000) == 5000
    assert fmt(123456) == "1 234.56 ₴"
    assert fmt(50000) == "500 ₴"
    assert fmt(-150) == "-1.50 ₴"
