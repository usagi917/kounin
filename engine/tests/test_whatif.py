"""whatif の試験。fixed8 を使い、期待値は点数と単価（1597円）から手で出した。"""

import pytest
import yaml

from engine.calc import CalcError, calculate
from engine.models import Coefficients, Employee, FundingInput, Placement
from engine.tests.test_golden import GOLDEN, INPUT
from engine.whatif import choice_effects, compare_eval_end

STAFF = [Employee.model_validate(item) for item in INPUT["employees"]]
COEF = Coefficients.model_validate(INPUT["coefficients"])
FUND = FundingInput.model_validate(INPUT["funding"])


def placements(name):
    return [Placement.model_validate(item) for item in INPUT["scenarios"][name]["placements"]]


def test_held_person_choices_change_peers():
    effects = choice_effects(STAFF, placements("held"), COEF, FUND, "G8", ["X", "Y"])
    # Y: 減点 16.5 + 15.4 = 31.9 を 2 人で 16.0 点ずつ。分配金 13000 → 26000。
    y = effects["Y"]
    assert y.self_distribution_yen == 0
    assert [(g.group, g.peers, g.peer_delta_yen) for g in y.groups] == [("Y", 2, 13000)]
    # X: 減点 39 + 15.4 = 54.4 を 3 人で 18.1 点ずつ。18.1 × 1597 → 29000、21000 から +8000。
    x = effects["X"]
    assert [(g.group, g.peers, g.peer_delta_yen) for g in x.groups] == [("X", 3, 8000)]


def test_member_moving_between_groups():
    effects = choice_effects(STAFF, placements("held"), COEF, FUND, "G1", ["X", "Y"])
    # X のまま：変化なし。
    assert effects["X"].self_distribution_yen == 21000
    assert [(g.peers, g.peer_delta_yen) for g in effects["X"].groups] == [(0, 0)]
    # Y へ：Y の受け手が 3 人になり 16.5 ÷ 3 = 5.5 点（9000円）。X に残る 2 人は 39 ÷ 2 = 19.5 点（31000円）。
    y = effects["Y"]
    assert y.self_distribution_yen == 9000
    assert [(g.group, g.peers, g.peer_delta_yen) for g in y.groups] == [("Y", 2, -4000), ("X", 2, 10000)]


def test_choice_effects_does_not_mutate_inputs():
    before = placements("held")
    choice_effects(STAFF, before, COEF, FUND, "G8", ["X", "Y"])
    assert before == placements("held")


def test_choice_effects_errors():
    with pytest.raises(CalcError):
        choice_effects(STAFF, placements("held"), COEF, FUND, "ZZ", ["X"])


def test_compare_eval_end_moves_everyone_to_period_end_group():
    ends = {"G8": "X"}   # 期末の所属は G8 だけ X、ほかは今のまま
    diff = compare_eval_end(STAFF, placements("resolved"), ends, COEF, FUND)
    # G1,G2,G4 が各 +8000、G6,G7 が各 -12000。G3,G5,G8 は変わらない。
    assert (diff.changed, diff.total_yen) == (5, 48000)


def test_compare_eval_end_same_groups_is_zero():
    ends = {p.emp_id: p.group for p in placements("resolved")}
    diff = compare_eval_end(STAFF, placements("resolved"), ends, COEF, FUND)
    assert (diff.changed, diff.total_yen) == (0, 0)


def test_compare_eval_end_keeps_held_person_held():
    ends = {"G8": "X", "G1": "X"}
    diff = compare_eval_end(STAFF, placements("held"), ends, COEF, FUND)
    assert (diff.changed, diff.total_yen) == (0, 0)   # G8 は保留のまま、動いた人もいない
    assert calculate(STAFF, placements("held"), COEF, FUND).employees[7].amount is None


def test_compare_eval_end_none_keeps_current_group():
    ends = {p.emp_id: None for p in placements("resolved")}
    assert compare_eval_end(STAFF, placements("resolved"), ends, COEF, FUND).changed == 0


def test_fixture_file_still_loadable():
    assert yaml.safe_load((GOLDEN / "fixed8-input.yaml").read_text(encoding="utf-8"))
