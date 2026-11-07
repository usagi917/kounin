"""性質テスト。乱数のシードを固定した小さな生成器で、さまざまな入力に対して成り立つ性質を確かめる。"""

import random
from fractions import Fraction

import pytest

from engine.calc import calculate
from engine.models import Coefficients, Employee, FundingInput, Placement

COEF = Coefficients(
    base_pay={1: {g: {s: 150_000 + 23_457 * g + 4_321 * s for s in (1, 2, 3)} for g in (1, 2, 3)}},
    grade_points={g: {"S": "13" + str(g), "A": "11" + str(g), "B": "9" + str(g), "C": "7" + str(g)}
                  for g in (1, 2, 3)},
    job_coefficients={"看護": "1.1", "介護": "1", "事務": "0.9"},
    rank_coefficients={"S": "1.1", "A": "1.05", "B": "1", "C": "0.95"},
)
ATTENDANCE = ["1", "1", "1", "0.95", "0.9", "0.85", "0.7", "0.5"]
SEEDS = range(200)


def generate(seed, hold=False):
    """職員と配置を作る。hold が False なら全グループに受け手が必ずいる。"""
    rng = random.Random(seed)
    groups = [f"G{i}" for i in range(rng.randint(1, 4))]
    staff, placements = [], []
    for g in groups:
        for _ in range(rng.randint(2, 7)):
            staff.append((g, rng.choice(ATTENDANCE)))
        staff.append((g, "1"))   # 受け手を保証する
    people, held_ids = [], []
    for i, (g, att) in enumerate(staff):
        emp_id = f"E{i}"
        people.append(Employee(
            emp_id=emp_id, display_name="合成", employment_type="正職員",
            job_type=rng.choice(list(COEF.job_coefficients)), table_no=1, grade=rng.randint(1, 3), step=rng.randint(1, 3), adjustment_pay=rng.randint(-5000, 5000),
            rank=rng.choice("SABC"), attendance_coefficient=att, segments=[{"group": g}]))
        if hold and rng.random() < 0.2:
            held_ids.append(emp_id)
            placements.append(Placement(emp_id=emp_id, held=True, candidates=[rng.choice(groups)]))
        else:
            placements.append(Placement(emp_id=emp_id, group=g))
    funding = FundingInput(total_months=rng.choice(["2.2", "3", "4.65", "1234/567"]),
                           base_share=rng.choice(["0.6", "0.5", "1/3"]))
    return people, placements, funding


@pytest.mark.parametrize("seed", SEEDS)
def test_points_are_conserved_before_rounding(seed):
    people, placements, funding = generate(seed)
    result = calculate(people, placements, COEF, funding)
    reduction = sum(Fraction(e.reduction) for e in result.employees)
    addition = sum(Fraction(e.addition_exact) for e in result.employees)
    assert addition == reduction and Fraction(result.unallocated_points) == 0


@pytest.mark.parametrize("seed", SEEDS)
def test_amounts_are_non_negative_thousands(seed):
    people, placements, funding = generate(seed, hold=True)
    result = calculate(people, placements, COEF, funding)
    assert result.ok
    for e in result.employees:
        assert e.distribution_yen >= 0 and e.distribution_yen % 1000 == 0
        if e.amount is None:
            assert e.status == "保留"
        else:
            assert e.amount >= 0 and e.amount % 1000 == 0
    assert result.total_amount == sum(e.amount or 0 for e in result.employees)


@pytest.mark.parametrize("seed", SEEDS)
def test_residual_only_without_held(seed):
    people, placements, funding = generate(seed, hold=True)
    result = calculate(people, placements, COEF, funding)
    has_held = any(p.held for p in placements)
    assert (result.rounding_residual is None) == has_held


@pytest.mark.parametrize("seed", SEEDS)
def test_unit_price_and_total_points_ignore_placement(seed):
    people, placements, funding = generate(seed, hold=True)
    resolved = [Placement(emp_id=p.emp_id, group=p.group or p.candidates[0]) for p in placements]
    held_run = calculate(people, placements, COEF, funding)
    resolved_run = calculate(people, resolved, COEF, funding)
    assert held_run.unit_price == resolved_run.unit_price
    assert held_run.total_points == resolved_run.total_points
    expected = sum(Fraction(e.points) for e in held_run.employees)   # 保留者の点数も含む
    assert Fraction(held_run.total_points) == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_candidate_groups_are_provisional(seed):
    people, placements, funding = generate(seed, hold=True)
    result = calculate(people, placements, COEF, funding)
    candidates = {c for p in placements if p.held for c in p.candidates}
    for e in result.employees:
        if e.status != "保留":
            assert (e.status == "暫定") == (e.group in candidates)


def test_generator_is_deterministic():
    assert generate(7, hold=True) == generate(7, hold=True)


# ---- 職員 ID と職場 ID の全単射の置き換えで、判定も金額も変わらない（本番化の不変条件 11） ----

def relabel(seed: int):
    """職員 ID と職場 ID を、別の名前へ1対1で置き換える写像（乱数で順序も変える）。"""
    rng = random.Random(seed + 10_000)
    people, placements, funding = generate(seed, hold=True)
    emp_ids = [p.emp_id for p in people]
    groups = sorted({s.group for p in people for s in p.segments} | {c for pl in placements for c in pl.candidates})
    new_emp = [f"Q{n:04d}" for n in range(len(emp_ids))]
    new_group = [f"職場-{n}" for n in range(len(groups))]
    rng.shuffle(new_emp)
    rng.shuffle(new_group)
    emp_map, group_map = dict(zip(emp_ids, new_emp, strict=True)), dict(zip(groups, new_group, strict=True))
    moved = [p.model_copy(update={"emp_id": emp_map[p.emp_id],
                                  "segments": [s.model_copy(update={"group": group_map[s.group]}) for s in p.segments]})
             for p in people]
    moved_placements = [pl.model_copy(update={"emp_id": emp_map[pl.emp_id],
                                              "group": group_map.get(pl.group) if pl.group else None,
                                              "candidates": [group_map[c] for c in pl.candidates]})
                        for pl in placements]
    return (people, placements, funding), (moved, moved_placements, funding), emp_map, group_map


@pytest.mark.parametrize("seed", SEEDS)
def test_ID_を1対1で置き換えても金額と丸めが変わらない(seed):
    (people, placements, funding), (moved, moved_placements, _), emp_map, group_map = relabel(seed)
    before = calculate(people, placements, COEF, funding)
    after = calculate(moved, moved_placements, COEF, funding)
    assert before.ok and after.ok
    assert (before.unit_price, before.total_points, before.total_amount) == (
        after.unit_price, after.total_points, after.total_amount)
    mapped = {e.emp_id: e for e in after.employees}
    for e in before.employees:
        other = mapped[emp_map[e.emp_id]]
        assert (e.amount, e.total_pay, e.status, e.distribution_yen) == (
            other.amount, other.total_pay, other.status, other.distribution_yen)
        assert (group_map[e.group] if e.group else None) == other.group


def _judge_rule():
    from engine.models import Rule
    return Rule.model_validate({
        "id": "R010", "org_id": "o", "version": 1, "status": "approved", "effective": {"from": "a", "to": None},
        "when": {"has_transfer": True}, "exclude_segments": [{"kind": "新卒ローテ"}, {"kind": "応援", "max_months": 4}],
        "then": {"group_basis": "longest_tenure"},
        "undecided": [{"id": "U1", "kind": "応援", "min_months": 5, "judgment": "J4"}], "text": "合成"})


@pytest.mark.parametrize("seed", range(60))
def test_ID_を1対1で置き換えても判定が変わらない(seed):
    from datetime import date

    from engine.judge import judge
    from engine.models import NormalScope, Period, Segment
    rng = random.Random(seed)
    period = Period(id="p", start=date(2026, 5, 1), end=date(2026, 10, 31))
    ends = [date(2026, m, 28) for m in range(5, 11)]
    groups, kinds = ["A", "B", "C"], ["通常", "応援", "新卒ローテ"]
    people = []
    for n in range(30):
        bounds = [0, *sorted(rng.sample(range(1, 6), rng.randint(0, 2))), 6]
        parts = [Segment(group=rng.choice(groups), kind=rng.choice(kinds), start=date(2026, 5 + lo, 1), end=ends[hi - 1])
                 for lo, hi in zip(bounds, bounds[1:], strict=False)]
        people.append(Employee(emp_id=f"E{n}", display_name="合成", employment_type="正職員", segments=parts))
    scope = NormalScope(employment_types=["正職員"], has_transfer=False)
    mapping = {g: f"職場{n}" for n, g in enumerate(rng.sample(groups, len(groups)))}
    ids = {p.emp_id: f"Z{n}" for n, p in enumerate(rng.sample(people, len(people)))}
    moved = [p.model_copy(update={"emp_id": ids[p.emp_id],
                                  "segments": [s.model_copy(update={"group": mapping[s.group]}) for s in p.segments]})
             for p in people]
    for old, new in zip(people, moved, strict=True):
        a, b = judge(old, [_judge_rule()], scope, period), judge(new, [_judge_rule()], scope, period)
        assert (a.category, a.reason_code, a.rule_ids, a.undecided_id) == (b.category, b.reason_code, b.rule_ids, b.undecided_id)
        assert (mapping[a.group] if a.group else None) == b.group
        assert (mapping[a.eval_end_group] if a.eval_end_group else None) == b.eval_end_group
