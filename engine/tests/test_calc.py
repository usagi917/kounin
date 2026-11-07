"""calculate の単体試験。期待値は小さい例を手で計算して固定した。"""

from fractions import Fraction

import pytest

from engine.calc import CalcError, calculate
from engine.models import Coefficients, Employee, FundingInput, Placement

COEF = Coefficients(
    base_pay={1: {1: {1: 100000, 2: 120000}, 2: {1: 150000}}},
    grade_points={1: {"B": "100", "A": "120"}, 2: {"B": "150", "A": "170"}},
    job_coefficients={"J": "1", "K": "0.9"},
    rank_coefficients={"B": "1", "A": "1.1"},
)
FUND = FundingInput(total_months="2", base_share="0.5")


def emp(emp_id, att="1", grade=1, step=1, rank="B", job="J", adj=0, group="X"):
    return Employee(emp_id=emp_id, display_name="合成", employment_type="正職員", job_type=job, table_no=1,
                    grade=grade, step=step, adjustment_pay=adj, rank=rank, attendance_coefficient=att,
                    segments=[{"group": group}])


def place(emp_id, group="X", **kw):
    return Placement(emp_id=emp_id, group=group, **kw)


def held(emp_id, *candidates):
    return Placement(emp_id=emp_id, held=True, candidates=list(candidates))


def by_id(result):
    return {e.emp_id: e for e in result.employees}


def test_two_people_hand_calculation():
    # 基本給 10万円ずつ、月数 1.000 + 1.000。点数 100 ずつ、単価 200000/200 = 1000。
    # B は減点 50.0 を A が受ける。A: 100000 + 150×1000、B: 100000 + 50×1000。
    result = calculate([emp("A"), emp("B", att="0.5")], [place("A"), place("B")], COEF, FUND)
    a, b = by_id(result)["A"], by_id(result)["B"]
    assert (result.base_months, result.amount_months) == ("1.000", "1.000")
    assert (result.amount_fund, result.unit_price) == (200000, 1000)
    assert (a.addition, a.final_points, a.amount, a.distribution_yen) == ("50.0", "150.0", 250000, 50000)
    assert (b.reduction, b.final_points, b.amount) == ("50.0", "50.0", 150000)
    assert (result.total_amount, result.rounding_residual) == (400000, 0)
    assert result.group_shares == {"X": "50.0"} and Fraction(result.unallocated_points) == 0


def test_no_receivers_leaves_points_unallocated():
    result = calculate([emp("A", att="0.5"), emp("B", att="0.5")], [place("A"), place("B")], COEF, FUND)
    assert Fraction(result.unallocated_points) == 100
    assert result.group_shares == {"X": "0.0"}
    assert all(e.addition == "0.0" and e.amount == 150000 for e in result.employees)
    assert result.rounding_residual == 100000


def test_held_person_stays_in_denominator_and_is_not_a_receiver():
    people = [emp("A"), emp("B", att="0.5"), emp("H", att="0.5")]
    result = calculate(people, [place("A"), place("B"), held("H", "X")], COEF, FUND)
    rows = by_id(result)
    # 分母は 3 人分の 300、単価は 300000/300 = 1000。H の減点は配分されず、B の減点だけが A へ行く。
    assert Fraction(result.total_points) == 300 and result.unit_price == 1000
    assert rows["A"].addition == "50.0" and rows["A"].status == "暫定"
    assert rows["H"].status == "保留" and rows["H"].amount is None and rows["H"].group is None
    assert rows["H"].addition == "0.0" and Fraction(result.unallocated_points) == 50
    assert result.total_amount == rows["A"].amount + rows["B"].amount
    assert result.rounding_residual is None


def test_held_without_candidates_leaves_others_confirmed():
    people = [emp("A"), emp("H")]
    rows = by_id(calculate(people, [place("A"), held("H")], COEF, FUND))
    assert rows["A"].status == "確定" and rows["H"].status == "保留"


def test_unit_price_unchanged_when_held_is_resolved():
    people = [emp("A"), emp("B", att="0.5"), emp("H", att="0.5")]
    before = calculate(people, [place("A"), place("B"), held("H", "X")], COEF, FUND)
    after = calculate(people, [place("A"), place("B"), place("H")], COEF, FUND)
    assert before.unit_price == after.unit_price and before.total_points == after.total_points
    assert after.rounding_residual is not None and all(e.status == "確定" for e in after.employees)


def test_total_points_zero_is_failure_not_exception():
    coef = COEF.model_copy(update={"grade_points": {1: {"B": "0"}}})
    result = calculate([emp("A")], [place("A")], coef, FUND)
    assert result.ok is False and result.error and result.employees == []


def test_adjustment_pay_negative_and_rank_override():
    result = calculate([emp("A", adj=-5000, rank="B")], [place("A", rank="A")], COEF, FUND)
    row = by_id(result)["A"]
    assert row.base_pay == 95000
    assert row.points == "120.0"              # 上書きの考課 A のポイント
    assert row.base_linked == 104500          # 95000 × 1.000 × 考課係数 1.1


def test_job_coefficient_rounds_to_one_decimal():
    row = by_id(calculate([emp("A", job="K", rank="A")], [place("A")], COEF, FUND))["A"]
    assert row.points == "108.0"
    row = by_id(calculate([emp("A", job="K", grade=2)], [place("A")], COEF, FUND))["A"]
    assert row.points == "135.0"


def test_amount_is_never_negative_and_in_thousands():
    result = calculate([emp("A", adj=-1_000_000)], [place("A")], COEF, FUND)
    assert by_id(result)["A"].amount == 0


def test_trace_has_each_step_and_rule_ids():
    result = calculate([emp("A")], [place("A", rule_ids=["R010"])], COEF, FUND)
    row = by_id(result)["A"]
    assert row.rule_ids == ["R010"]
    assert [t.step for t in row.trace] == [1, 2, 3, 3, 4, 5, 6, 7, 8, 9]   # 手順6、7、9を足した
    assert any(t.rule_ids == ["R010"] for t in row.trace)


def test_addition_exact_keeps_value_before_rounding():
    people = [emp("A"), emp("B"), emp("C"), emp("D", att="0.5")]   # D の減点 50.0 を 3 人で
    row = by_id(calculate(people, [place(p.emp_id) for p in people], COEF, FUND))["A"]
    assert row.addition == "16.7" and Fraction(row.addition_exact) == Fraction(50, 3)


@pytest.mark.parametrize("change", [
    {"grade": 9}, {"step": 9}, {"job": "Z"}, {"rank": "Z"}, {"grade": 3},
])
def test_table_miss_is_calc_error(change):
    with pytest.raises(CalcError):
        calculate([emp("A", **change)], [place("A")], COEF, FUND)


def test_unknown_table_no_is_calc_error():
    bad = emp("A").model_copy(update={"table_no": 9})
    with pytest.raises(CalcError):
        calculate([bad], [place("A")], COEF, FUND)


def test_missing_input_is_calc_error():
    bad = emp("A").model_copy(update={"grade": None})
    with pytest.raises(CalcError):
        calculate([bad], [place("A")], COEF, FUND)


def test_placement_errors():
    with pytest.raises(CalcError):
        calculate([emp("A"), emp("B")], [place("A")], COEF, FUND)             # 配置がない
    with pytest.raises(CalcError):
        calculate([emp("A")], [place("A"), place("Z")], COEF, FUND)           # 余計な配置
    with pytest.raises(CalcError):
        calculate([emp("A")], [place("A"), place("A")], COEF, FUND)           # 重複
    with pytest.raises(CalcError):
        calculate([emp("A")], [Placement(emp_id="A")], COEF, FUND)            # 保留でないのに職場なし
    with pytest.raises(CalcError):
        calculate([emp("A")], [], COEF, FUND)                                 # 全員に配置なし


def test_calculation_is_pure():
    people, placements = [emp("A"), emp("B", att="0.5")], [place("A"), place("B")]
    first = calculate(people, placements, COEF, FUND)
    assert calculate(people, placements, COEF, FUND) == first
    assert placements[0] == place("A")
