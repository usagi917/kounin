"""丸めの試験。Excel の ROUND と同じく、0.5 は0から遠い側へ寄せる。"""

from fractions import Fraction

import pytest

from engine import rounding as r


@pytest.mark.parametrize("value, want", [
    ("0.25", "0.3"), ("0.24", "0.2"), ("-0.25", "-0.3"), ("-0.24", "-0.2"),
    ("15.95", "16.0"), ("8.25", "8.3"), ("0", "0"), ("1/3", "0.3"), ("-1/3", "-0.3"),
])
def test_points_half_away_from_zero(value, want):
    assert r.points(value) == Fraction(want)


@pytest.mark.parametrize("value, want", [("1.3195", "1.320"), ("1.3194", "1.319"), ("-1.3195", "-1.320")])
def test_months(value, want):
    assert r.months(value) == Fraction(want)


@pytest.mark.parametrize("value, want", [
    ("0.5", 1), ("0.49", 0), ("-0.5", -1), ("-0.49", 0), ("1596.7", 1597), ("-1596.5", -1597), (0, 0),
])
def test_yen(value, want):
    assert r.yen(value) == want
    assert isinstance(r.yen(value), int)


@pytest.mark.parametrize("value, want", [
    (0, 0), (499, 0), ("499.9", 0), (500, 1000), (1499, 1000), (1500, 2000),
    (729656, 730000), (999, 1000), (1000, 1000), (-1, 0), (-500, 0), (-1500, 0), ("-0.4", 0),
])
def test_thousand(value, want):
    assert r.thousand(value) == want


def test_thousand_boundary_fraction():
    assert r.thousand(Fraction(999_999, 2000)) == 0       # 499.9995 円
    assert r.thousand(Fraction(1_000_001, 2000)) == 1000   # 500.0005 円


def test_round_to_negative_digits():
    assert r.round_to(Fraction(1250), -2) == 1300
    assert r.round_to(Fraction(-1250), -2) == -1300


@pytest.mark.parametrize("bad", [0.5, 1.0, None, True, [1]])
def test_to_fraction_rejects_non_exact(bad):
    with pytest.raises(TypeError):
        r.to_fraction(bad)


@pytest.mark.parametrize("func", [r.points, r.months, r.yen, r.thousand])
def test_rounders_reject_float(func):
    with pytest.raises(TypeError):
        func(0.25)


def test_to_fraction_accepts_exact_forms():
    assert r.to_fraction("1.1") == Fraction(11, 10)
    assert r.to_fraction("1234/567") == Fraction(1234, 567)
    assert r.to_fraction(3) == 3
    assert r.to_fraction(Fraction(1, 3)) == Fraction(1, 3)
