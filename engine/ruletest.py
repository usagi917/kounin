"""ルールのテスト（対象例と非対象例）を判定にかける。

テストの形は `{name, input, expect}`。input は職員の項目に加え、評価期間の末日 `eval_end`、
考課の入力 `grade_input`、職場 `group` を書ける。expect は次の項目から必要なものを書く。
  matched  ルールの when に当たるか
  grade    考課の上書きを当てた後のランク
  category 判定の区分、group 判定の職場
`choice` を持つ判定のテストは、ルール案の再現検査（drafts）が流すのでここでは扱わない。
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml

from engine import predicates as p
from engine.judge import effective_rank, judge
from engine.models import Employee, NormalScope, Period, Rule, Segment

DEFAULT_SCOPE = NormalScope(employment_types=["正職員"], has_transfer=False)
EMPLOYEE_FIELDS = set(Employee.model_fields) - {"emp_id", "display_name", "segments"}
EXTRA_INPUT = {"eval_end", "grade_input", "group", "segments", "emp_id"}
EXPECT_KEYS = {"matched", "grade", "category", "group"}


@dataclass(frozen=True)
class TestResult:
    __test__ = False   # pytest に収集させない
    name: str
    passed: bool
    detail: str = ""


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _period(end: date) -> Period:
    """評価期間は6か月。末日から5か月前の月初が始まり。"""
    index = end.year * 12 + end.month - 1 - 5
    return Period(id="test", start=date(index // 12, index % 12 + 1, 1), end=end)


def _employee(data: dict, period: Period) -> Employee:
    unknown = set(data) - EMPLOYEE_FIELDS - EXTRA_INPUT
    if unknown:
        raise ValueError(f"input にない項目です: {sorted(unknown)}")
    fields = {key: data[key] for key in EMPLOYEE_FIELDS if key in data}
    if "grade_input" in data:
        fields["rank"] = data["grade_input"]
    if "segments" in data:
        segments = [Segment.model_validate(item) for item in data["segments"]]
    else:   # 職場の指定がなければ、評価期間ぜんぶを1つの職場にいたことにする
        segments = [Segment(group=data.get("group", "X"), start=period.start, end=period.end)]
    fields.setdefault("employment_type", "正職員")
    return Employee(emp_id=data.get("emp_id", "TEST"), display_name="架空のテスト", segments=segments, **fields)


def _observe(rule: Rule, emp: Employee, period: Period, scope: NormalScope) -> dict:
    clipped = p.clip(emp, period) if p.segments_complete(emp) else emp
    result = judge(emp, [rule], scope, period)
    return {"matched": p.matches(clipped, rule, period), "grade": effective_rank(emp, [rule], period),
            "category": result.category.value, "group": result.group}


def run_rule_test(rule: Rule, test: dict, scope: NormalScope = DEFAULT_SCOPE,
                  period: Period | None = None) -> TestResult:
    name = str(test.get("name", test.get("id", "")))
    if "input" not in test or "expect" not in test:
        raise ValueError(f"テストには input と expect が必要です: {name!r}")
    unknown = set(test["expect"]) - EXPECT_KEYS
    if unknown:
        raise ValueError(f"expect にない項目です: {sorted(unknown)}")
    data = test["input"]
    if "eval_end" in data:
        period = _period(_date(data["eval_end"]))
    if period is None:
        raise ValueError(f"評価期間が分かりません（input.eval_end か period が必要です）: {name!r}")
    seen = _observe(rule, _employee(data, period), period, scope)
    wrong = [f"{key}: 期待 {want!r}、実際 {seen[key]!r}" for key, want in test["expect"].items()
             if seen[key] != want]
    return TestResult(name=name, passed=not wrong, detail="、".join(wrong))


def run_rule_tests(rule: Rule, tests: list[dict], scope: NormalScope = DEFAULT_SCOPE,
                   period: Period | None = None) -> list[TestResult]:
    """判定のテスト（choice を持つもの）は流さず、対象例・非対象例だけを判定にかける。"""
    return [run_rule_test(rule, test, scope, period) for test in tests if "choice" not in test]


def load_tests(text: str) -> list[dict]:
    return yaml.safe_load(text) or []
