"""5区分の判定。承認後の形を手で書いたルールで、デモの人の結果を固定する。"""

from datetime import date

import pytest

from engine.judge import judge
from engine.models import Category, Employee, NormalScope, Period, Rule, Segment

PERIOD = Period(id="2026-winter", start=date(2026, 5, 1), end=date(2026, 10, 31))
SCOPE = NormalScope(employment_types=["正職員"], has_transfer=False)
MONTH_STARTS = [date(2026, m, 1) for m in range(5, 11)]
MONTH_ENDS = [date(2026, 5, 31), date(2026, 6, 30), date(2026, 7, 31),
              date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 31)]


def segments(*parts: tuple[str, int, str]) -> list[Segment]:
    result, cursor = [], 0
    for group, months, kind in parts:
        result.append(Segment(group=group, kind=kind, start=MONTH_STARTS[cursor],
                              end=MONTH_ENDS[cursor + months - 1]))
        cursor += months
    return result


def employee(emp_id: str, *parts: tuple[str, int, str], employment: str | None = "正職員") -> Employee:
    return Employee(emp_id=emp_id, display_name=f"合成職員{emp_id}", employment_type=employment,
                    job_type="看護", segments=segments(*parts))


def rule(rule_id: str = "R010", basis: str = "longest_tenure") -> Rule:
    return Rule.model_validate({
        "id": rule_id, "org_id": "hospital-a", "version": 1, "status": "approved",
        "effective": {"from": "2026-summer", "to": None},
        "when": {"has_transfer": True},
        "exclude_segments": [{"kind": "新卒ローテ"}, {"kind": "応援", "max_months": 4}],
        "then": {"group_basis": basis},
        "undecided": [{"id": "U1", "kind": "応援", "min_months": 5, "judgment": "J4"}],
        "sources": [{"type": "judgment", "id": "J1", "role": "担当者"}],
        "text": "合成の例",
    })


def run(emp: Employee, rules: list[Rule] | None = None):
    return judge(emp, [rule()] if rules is None else rules, SCOPE, PERIOD)


def test_異動なしは通常():
    result = run(employee("N", ("X", 6, "通常")))
    assert (result.category, result.group) == (Category.NORMAL, "X")


def test_長くいた職場で数える():
    result = run(employee("A", ("X", 4, "通常"), ("Y", 2, "通常")))
    assert (result.category, result.group, result.rule_ids) == (Category.EXCEPTION, "X", ["R010"])
    assert result.eval_end_group == "Y"


def test_ローテ先を数えない():
    result = run(employee("R", ("Z", 4, "新卒ローテ"), ("Y", 2, "通常")))
    assert (result.category, result.group) == (Category.EXCEPTION, "Y")


def test_T_応援4か月は期末も応援先でも元の職場():
    result = run(employee("T", ("X", 2, "通常"), ("Y", 4, "応援")))
    assert (result.category, result.group, result.eval_end_group) == (Category.EXCEPTION, "X", "Y")


def test_B_応援5か月は埋めずに止まる():
    result = run(employee("B", ("X", 1, "通常"), ("Y", 5, "応援")))
    assert result.category == Category.UNREGISTERED
    assert result.group is None
    assert (result.reason_code, result.undecided_id, result.judgment_id) == ("担当者が決めなかった", "U1", "J4")


def test_C_異動日の空欄は情報不足():
    emp = employee("C", ("X", 3, "通常"), ("Y", 3, "通常"))
    emp.segments[0].end = None
    emp.segments[1].start = None
    result = run(emp)
    assert result.category == Category.MISSING
    assert result.missing == ["segments"]


def test_雇用区分の欠けは情報不足():
    assert run(employee("D", ("X", 6, "通常"), employment=None)).category == Category.MISSING


def test_除外後の同点は判定なしで止まる():
    result = run(employee("E", ("X", 3, "通常"), ("Y", 3, "通常")))
    assert (result.category, result.reason_code) == (Category.UNREGISTERED, "判定なし")


def test_ルールが無い異動者は外れた条件つきで止まる():
    result = run(employee("P", ("X", 4, "通常"), ("Y", 2, "通常")), rules=[])
    assert result.category == Category.UNREGISTERED
    assert result.failed_conditions
    assert result.normal_scope_failed == ["has_transfer"]


def test_食い違うルールは競合():
    result = run(employee("A", ("X", 4, "通常"), ("Y", 2, "通常")),
                 rules=[rule(), rule("R011", basis="eval_end")])
    assert result.category == Category.CONFLICT
    assert result.rule_ids == ["R010", "R011"]


def test_同じ結論のルールは競合にしない():
    result = run(employee("A", ("X", 4, "通常"), ("Y", 2, "通常")), rules=[rule(), rule("R011")])
    assert result.category == Category.EXCEPTION


def test_通常の範囲外の雇用区分は判断未登録():
    result = run(employee("F", ("X", 6, "通常"), employment="嘱託"))
    assert result.category == Category.UNREGISTERED
    assert result.normal_scope_failed == ["employment_type"]


@pytest.mark.parametrize("months,expected", [(1, Category.EXCEPTION), (4, Category.EXCEPTION),
                                             (5, Category.UNREGISTERED)])
def test_応援の月数の境界(months, expected):
    assert run(employee("V", ("X", 6 - months, "通常"), ("Y", months, "応援"))).category == expected


def test_単一所属で日付の欠けは適用ルールがあれば情報不足():
    emp = employee("D", ("X", 6, "通常"))
    emp.segments[0].start = None
    everyone = rule().model_copy(update={"when": rule().when.model_copy(update={"has_transfer": None})})
    result = run(emp, rules=[everyone])
    assert (result.category, result.missing) == (Category.MISSING, ["segments"])


def test_評価期間の外の在籍は数えない():
    emp = employee("G", ("X", 2, "通常"), ("Y", 4, "通常"))
    emp.segments[0].start = date(2025, 1, 1)
    result = run(emp)
    assert (result.category, result.group) == (Category.EXCEPTION, "Y")


def test_評価期間の前に異動した人は異動者ではない():
    emp = employee("H", ("Y", 6, "通常"))
    emp.segments.insert(0, Segment(group="X", kind="通常", start=date(2025, 1, 1), end=date(2026, 4, 30)))
    assert run(emp).category == Category.NORMAL
