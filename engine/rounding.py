"""丸め。Excel の ROUND と同じ、0から遠い側への四捨五入を整数演算で行う。金額の丸めはここだけ。"""

import math
from fractions import Fraction

Number = str | int | Fraction


def to_fraction(value: Number) -> Fraction:
    """文字列と整数と Fraction だけを受け付ける。float は誤差が入るので TypeError。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, Fraction)):
        raise TypeError(f"小数は文字列か Fraction で渡す: {type(value).__name__}")
    return Fraction(value)


def round_to(value: Fraction, digits: int) -> Fraction:
    """小数 digits 位へ丸める。digits が負なら 10 の累乗の位へ丸める。0.5 は0から遠い側へ。"""
    value = to_fraction(value)
    scale = Fraction(10) ** -digits   # 丸めの刻み幅
    scaled = abs(value / scale)
    whole = (2 * scaled.numerator + scaled.denominator) // (2 * scaled.denominator)
    return (whole if value >= 0 else -whole) * scale


def points(value: Number) -> Fraction:
    """小数1位。"""
    return round_to(to_fraction(value), 1)


def months(value: Number) -> Fraction:
    """小数3位。"""
    return round_to(to_fraction(value), 3)


def yen(value: Number) -> int:
    """円。"""
    return int(round_to(to_fraction(value), 0))


def thousand(value: Number) -> int:
    """千円。0以下は0。"""
    return max(thousand_signed(value), 0)


def thousand_signed(value: Number) -> int:
    """千円。0でクリップしない（精勤分配金の控除後）。"""
    return int(round_to(to_fraction(value), -3))


def floor_yen(value: Number) -> int:
    """円。切り捨て（欠勤等の控除）。"""
    return math.floor(to_fraction(value))
