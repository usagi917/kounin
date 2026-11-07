"""原資の式。案の型はルールパックに書き、円の計算はここだけで行う。"""

from collections.abc import Mapping
from fractions import Fraction

from engine import rounding as r
from engine.models import FundingInput


def plan_total(plan: Mapping, previous: int, profit_now: int, profit_before: int) -> int:
    """案の原資の総額。same は前期並み、profit_share は増益の一部を足す、scale は前期に率を掛ける。"""
    kind = plan["type"]
    if kind == "same":
        return previous
    if kind == "profit_share":
        return previous + r.yen(max(profit_now - profit_before, 0) * r.to_fraction(plan["rate"]))
    if kind == "scale":
        return r.yen(previous * r.to_fraction(plan["rate"]))
    raise ValueError(f"原資の案の型が分かりません: {kind}")


def funding_input(total_yen: int, basis_total: int, base_share: str, position_months: str) -> FundingInput:
    """原資の総額を、計算の基礎の合計で割って総月数にする。"""
    if basis_total <= 0:
        raise ValueError("計算の基礎の合計が0のため、総月数を決められない")
    return FundingInput(total_months=str(Fraction(total_yen, basis_total)), base_share=base_share,
                        position_months=position_months)
