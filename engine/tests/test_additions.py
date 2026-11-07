"""昇給、前後の差、版の適用、原資の式。期待値は実装の前に手計算で固定した（INV8）。"""

from datetime import date
from fractions import Fraction

import pytest

from engine.compare import compare
from engine.funding import funding_input, plan_total
from engine.models import CalcResult, EmployeeCalc
from engine.raises import raise_of
from engine.versions import VersionError, VersionRef, applicable

TABLE = {1: 200000, 2: 201000, 3: 202000, 4: 203000, 5: 204000}
INC = {"A": 2, "B": 1, "E": 0}


@pytest.mark.parametrize("rank, step, status, after, pay_after", [
    ("B", 1, "昇給", 2, 201000),       # 1号俸 +1
    ("A", 1, "昇給", 3, 202000),       # 1号俸 +2
    ("E", 3, "据え置き", 3, 202000),   # 増分0
    ("A", 4, "上限", 5, 204000),       # 4+2=6 は表の外。5で止まる
    ("B", 5, "上限", 5, 204000),       # 既に上限
])
def test_定期昇給(rank, step, status, after, pay_after):
    result = raise_of("1", rank, step, TABLE, INC)
    assert (result.status, result.step_after, result.pay_after) == (status, after, pay_after)
    assert result.step_before == step and result.pay_before == TABLE[step]


@pytest.mark.parametrize("rank, step", [(None, 1), ("Z", 1), ("B", None), ("B", 99)])
def test_ランクか号俸が読めない人は止める(rank, step):
    result = raise_of("1", rank, step, TABLE, INC)
    assert result.status == "止まり"
    assert result.step_after == result.step_before == step


def row(emp_id: str, pay: int | None) -> EmployeeCalc:
    return EmployeeCalc(emp_id=emp_id, group="東", status="確定", base_pay=0, points="0", reduction="0",
                        addition="0", addition_exact="0", final_points="0", base_linked=0, amount_part=0,
                        distribution_yen=0, amount=pay, total_pay=pay)


def result(pays: dict[str, int | None]) -> CalcResult:
    return CalcResult(ok=True, employees=[row(k, v) for k, v in pays.items()],
                      total_pay=sum(v or 0 for v in pays.values()))


def test_前後の差は総額だけでなく増減の人数と大きい人を返す():
    before = result({"a": 1000, "b": 2000, "c": 3000, "d": 4000, "held": None})
    after = result({"a": 1500, "b": 1800, "c": 3000, "d": 4100, "held": None})
    got = compare(before, after, top=2)
    assert (got.total_before, got.total_after, got.total_delta) == (10000, 10400, 400)
    assert (got.up, got.down, got.same, got.skipped) == (2, 1, 1, 1)
    assert [(p.emp_id, p.delta) for p in got.largest_up] == [("a", 500), ("d", 100)]
    assert [(p.emp_id, p.delta) for p in got.largest_down] == [("b", -200)]


def test_片方にしかいない人は差に数えない():
    got = compare(result({"a": 1000, "gone": 500}), result({"a": 1000, "new": 700}))
    assert (got.up, got.down, got.same, got.skipped) == (0, 0, 1, 2)


VERSIONS = [VersionRef(id="1", effective_from=date(2025, 4, 1)), VersionRef(id="2", effective_from=date(2026, 10, 1))]


@pytest.mark.parametrize("on, want", [
    (date(2025, 4, 1), "1"),      # 施行日の当日
    (date(2026, 9, 30), "1"),     # 施行の前日
    (date(2026, 10, 1), "2"),     # 施行の当日
    (date(2027, 1, 1), "2"),
])
def test_版は基準日で選ぶ(on, want):
    assert applicable(VERSIONS, on).id == want


def test_施行の前の基準日は止まる():
    with pytest.raises(VersionError, match="適用できる版がありません"):
        applicable(VERSIONS, date(2025, 3, 31))


def test_同じ施行日の版が2つあれば止まる():
    with pytest.raises(VersionError, match="同じ施行日"):
        applicable([*VERSIONS, VersionRef(id="3", effective_from=date(2026, 10, 1))], date(2026, 12, 1))


PLAN = {"previous": 300_000_000, "profit_now": 450_000_000, "profit_before": 360_000_000}


@pytest.mark.parametrize("plan, want", [
    ({"id": "A", "type": "same"}, 300_000_000),
    ({"id": "B", "type": "profit_share", "rate": "1/10"}, 309_000_000),   # 3億 + (4.5億 − 3.6億) ÷ 10
    ({"id": "C", "type": "scale", "rate": "9/10"}, 270_000_000),
])
def test_原資の3案(plan, want):
    assert plan_total(plan, **PLAN) == want


def test_減益なら増益の案は前期並みのまま():
    plan = {"id": "B", "type": "profit_share", "rate": "1/10"}
    assert plan_total(plan, 300_000_000, 100, 200) == 300_000_000


def test_知らない型の案は止まる():
    with pytest.raises(ValueError, match="原資の案の型"):
        plan_total({"id": "X", "type": "謎"}, 1, 1, 1)


def test_総額から総月数を分数で作る():
    got = funding_input(300, 200, "0.6", "1.5")
    assert Fraction(got.total_months) == Fraction(3, 2)
    assert got.base_share == "0.6" and got.position_months == "1.5"


def test_基礎が0なら止まる():
    with pytest.raises(ValueError, match="計算の基礎"):
        funding_input(300, 0, "0.6", "1.5")
