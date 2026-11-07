"""ルールのテスト実行（F3-4）。雛形 R002 の既存テストと、対象例・非対象例を流す。"""

import re
from pathlib import Path

import pytest
import yaml

from engine.ruletest import load_tests, run_rule_test, run_rule_tests
from engine.models import Rule

PACK = Path(__file__).resolve().parents[2] / "rules" / "sample"


def pack_rule(name: str) -> Rule:
    text = next((PACK / "rules").glob(f"{name}-*.md")).read_text(encoding="utf-8")
    return Rule.model_validate(yaml.safe_load(re.search(r"```yaml\n(.*?)```", text, re.DOTALL).group(1)))


R002 = pack_rule("R002")


def test_R002_の既存テスト2件が通る():
    tests = load_tests((PACK / "tests" / "R002.yaml").read_text(encoding="utf-8"))
    results = run_rule_tests(R002, tests)
    assert [(r.name, r.passed) for r in results] == [("入職2か月はB", True), ("入職4か月ちょうどは入力どおり", True)]


def test_対象例と非対象例():
    target = {"name": "対象", "input": {"hire_date": "2026-08-01", "eval_end": "2026-10-31", "grade_input": "S"},
              "expect": {"matched": True, "grade": "B"}}
    other = {"name": "非対象", "input": {"hire_date": "2024-04-01", "eval_end": "2026-10-31", "grade_input": "S"},
             "expect": {"matched": False, "grade": "S"}}
    assert all(r.passed for r in run_rule_tests(R002, [target, other]))


def test_期待と違えば理由つきで失敗する():
    test = {"name": "誤り", "input": {"hire_date": "2026-08-01", "eval_end": "2026-10-31", "grade_input": "A"},
            "expect": {"grade": "A"}}
    result = run_rule_test(R002, test)
    assert not result.passed
    assert "grade" in result.detail and "'B'" in result.detail


def test_入職日がないと条件に当たらない():
    test = {"name": "入職日なし", "input": {"eval_end": "2026-10-31", "grade_input": "A"},
            "expect": {"matched": False, "grade": "A"}}
    assert run_rule_test(R002, test).passed


def test_区分と職場も確かめられる():
    rule = Rule.model_validate({
        "id": "R010", "org_id": "hospital-a", "status": "approved", "effective": {"from": "2026-summer"},
        "when": {"has_transfer": True}, "exclude_segments": [{"kind": "新卒ローテ"}],
        "then": {"group_basis": "longest_tenure"}})
    segments = [{"group": "X", "start": "2026-05-01", "end": "2026-08-31"},
                {"group": "Y", "start": "2026-09-01", "end": "2026-10-31"}]
    test = {"name": "長い職場", "input": {"eval_end": "2026-10-31", "segments": segments},
            "expect": {"matched": True, "category": "例外適用", "group": "X"}}
    assert run_rule_test(rule, test).passed


def test_判定のテストは流さない():
    tests = [{"id": "J1", "choice": "X", "segments": []}]
    assert run_rule_tests(R002, tests) == []


@pytest.mark.parametrize("test,message", [
    ({"name": "a", "input": {"eval_end": "2026-10-31"}, "expect": {"salary": 1}}, "expect にない項目"),
    ({"name": "b", "input": {"eval_end": "2026-10-31", "wage": 1}, "expect": {"grade": "A"}}, "input にない項目"),
    ({"name": "c", "input": {"hire_date": "2026-08-01"}, "expect": {"grade": "B"}}, "評価期間"),
    ({"name": "d", "expect": {"grade": "B"}}, "input と expect"),
])
def test_形の誤りは理由つきで拒否する(test, message):
    with pytest.raises(ValueError, match=message):
        run_rule_test(R002, test)
