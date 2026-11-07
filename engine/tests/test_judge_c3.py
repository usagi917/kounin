"""C3 の拡張：個別判断、考課の上書き、勤続月数（PLAN §3.15）。"""

from datetime import date

import pytest

from engine import predicates as p
from engine.judge import effective_rank, judge
from engine.models import Category, Decision, Employee, Rule
from engine.tests.test_judge import PERIOD, SCOPE, employee, rule, run

RANKS = {"S", "A", "B", "C", "D"}


def grade_rule(expr: str = "< 4", grade: str = "B") -> Rule:
    return Rule.model_validate({
        "id": "R002", "org_id": "hospital-a", "version": 1, "status": "approved",
        "effective": {"from": "2026-summer", "to": None},
        "when": {"tenure_months_at_eval_end": expr}, "then": {"grade": grade}})


def hired(emp_id: str, hire_date: date | None, rank: str | None = "A", *parts: tuple[str, int, str]) -> Employee:
    emp = employee(emp_id, *(parts or (("X", 6, "通常"),)))
    return emp.model_copy(update={"hire_date": hire_date, "rank": rank})


def test_個別判断は最優先で例外適用():
    emp = employee("B", ("X", 1, "通常"), ("Y", 5, "応援"))
    result = judge(emp, [rule()], SCOPE, PERIOD, decision=Decision(id="D1", emp_id="B", group="X"))
    assert (result.category, result.group, result.rule_ids, result.reason_code) == (
        Category.EXCEPTION, "X", ["D1"], "個別判断")
    assert result.eval_end_group == "Y"


def test_個別判断は情報不足にも勝つ():
    emp = employee("C", ("X", 3, "通常"), ("Y", 3, "通常"))
    emp.segments[0].end = None
    result = judge(emp, [rule()], SCOPE, PERIOD, decision=Decision(id="D2", emp_id="C", group="Y"))
    assert (result.category, result.group) == (Category.EXCEPTION, "Y")


def test_別の職員の個別判断は拒否する():
    with pytest.raises(ValueError, match="別の職員"):
        judge(employee("N", ("X", 6, "通常")), [], SCOPE, PERIOD, decision=Decision(id="D1", emp_id="B", group="X"))


def test_所属を決めるルールがあれば考課のルールは並んでいても区分に効かない():
    emp = hired("A", date(2026, 8, 1), "A", ("X", 4, "通常"), ("Y", 2, "通常"))
    result = run(emp, rules=[rule(), grade_rule()])
    assert (result.category, result.group, result.rule_ids) == (Category.EXCEPTION, "X", ["R010"])


def test_考課のルールだけでは異動者を例外適用にしない():
    result = run(hired("A", date(2026, 8, 1), "A", ("X", 4, "通常"), ("Y", 2, "通常")), rules=[grade_rule()])
    assert (result.category, result.rule_ids) == (Category.UNREGISTERED, [])


def test_考課のルールがあっても異動なしは通常():
    result = run(hired("N", date(2026, 8, 1), "A"), rules=[grade_rule()])
    assert (result.category, result.rule_ids) == (Category.NORMAL, [])


def test_eval_end_の基準は期末の所属():
    result = run(employee("A", ("X", 4, "通常"), ("Y", 2, "通常")), rules=[rule(basis="eval_end")])
    assert (result.category, result.group) == (Category.EXCEPTION, "Y")


@pytest.mark.parametrize("hire,expected", [
    (date(2026, 8, 1), "B"), (date(2026, 7, 1), "B"), (date(2026, 6, 30), "A"), (date(2026, 6, 1), "A")])
def test_入職4か月未満はBに上書き(hire, expected):
    assert effective_rank(hired("H", hire, "A"), [grade_rule()], PERIOD) == expected


def test_入職日がなければ上書きせず元のランク():
    assert effective_rank(hired("H", None, "A"), [grade_rule()], PERIOD) == "A"
    assert effective_rank(hired("H", None, None), [grade_rule()], PERIOD) is None


def test_ルールがなければ職員のランク():
    assert effective_rank(hired("H", date(2026, 8, 1), "S"), [], PERIOD) == "S"


@pytest.mark.parametrize("hired_on,on,months", [
    (date(2025, 12, 30), date(2026, 4, 30), 4),
    (date(2025, 12, 31), date(2026, 4, 30), 4),   # 末日は日付が大きくても満月とみなす
    (date(2025, 12, 15), date(2026, 4, 14), 3),
    (date(2026, 3, 1), date(2026, 4, 30), 1),
])
def test_満月数(hired_on, on, months):
    assert p.tenure_months(hired_on, on) == months


@pytest.mark.parametrize("expr,expected", [
    ("< 3", False), ("< 4", True), ("<= 3", True), ("> 3", False), (">= 3", True), ("== 3", True), ("== 4", False)])
def test_勤続月数の比較(expr, expected):
    emp = hired("H", date(2026, 7, 31))   # 期末まで満3か月
    assert p.tenure_months_at_eval_end(emp, PERIOD) == 3
    assert p.matches(emp, grade_rule(expr), PERIOD) is expected


def test_期間を渡さなければ勤続月数の条件は満たさない():
    assert not p.matches(hired("H", date(2026, 9, 1)), grade_rule())


def test_月数の比較の書き方を検査する():
    for bad in ("4", "<< 4", "< x", "約4"):
        with pytest.raises(ValueError, match="月数の比較"):
            p.parse_comparison(bad)


def test_語彙の検査():
    no_basis = rule().model_copy(update={"then": rule().then.model_copy(update={"group_basis": None})})
    with pytest.raises(ValueError, match="どちらかが必要"):
        p.validate_vocabulary(no_basis)
    with pytest.raises(ValueError, match="考課ランク"):
        p.validate_vocabulary(grade_rule(grade="Z"), RANKS)
    p.validate_vocabulary(grade_rule(), RANKS)
