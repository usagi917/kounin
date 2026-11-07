"""手計算の固定データとの一致（INV8）。期待値は engine を使わず別の式で出した。実装側で合わせない。"""

from fractions import Fraction
from pathlib import Path

import pytest
import yaml

calc = pytest.importorskip("engine.calc")

from engine.models import Coefficients, Employee, FundingInput, Placement  # noqa: E402

GOLDEN = Path(__file__).parent / "golden"
INPUT = yaml.safe_load((GOLDEN / "fixed8-input.yaml").read_text(encoding="utf-8"))
EXPECTED = yaml.safe_load((GOLDEN / "fixed8-expected.yaml").read_text(encoding="utf-8"))
POINT_KEYS = ("points", "reduction", "addition", "final_points")
YEN_KEYS = ("base_pay", "base_linked", "amount_part", "distribution_yen", "amount")


def run(name: str):
    staff = [Employee.model_validate(item) for item in INPUT["employees"]]
    placements = [Placement.model_validate(item) for item in INPUT["scenarios"][name]["placements"]]
    return calc.calculate(staff, placements, Coefficients.model_validate(INPUT["coefficients"]),
                          FundingInput.model_validate(INPUT["funding"]))


@pytest.mark.parametrize("name", ["held", "resolved"])
def test_totals(name):
    result, expected = run(name), EXPECTED[name]
    assert result.ok
    assert result.unit_price == expected["unit_price"]
    assert result.amount_fund == expected["amount_fund"]
    assert result.total_base_pay == expected["total_base_pay"]
    assert result.total_amount == expected["total_amount"]
    assert result.rounding_residual == expected["rounding_residual"]
    assert Fraction(result.total_points) == Fraction(expected["total_points"])
    assert Fraction(result.unallocated_points) == Fraction(expected["unallocated_points"])
    assert {k: Fraction(v) for k, v in result.group_shares.items()} == {
        k: Fraction(v) for k, v in expected["group_shares"].items()}


@pytest.mark.parametrize("name", ["held", "resolved"])
def test_each_employee(name):
    result, expected = run(name), EXPECTED[name]["employees"]
    assert {e.emp_id for e in result.employees} == set(expected)
    for employee in result.employees:
        want = expected[employee.emp_id]
        assert employee.group == want["group"]
        assert employee.status == want["status"]
        for key in YEN_KEYS:
            assert getattr(employee, key) == want[key], (employee.emp_id, key)
        for key in POINT_KEYS:
            assert Fraction(getattr(employee, key)) == Fraction(want[key]), (employee.emp_id, key)
