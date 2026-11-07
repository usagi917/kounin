"""もしも計算。職員の所属だけを替えて再計算し、差を返す。"""

from engine.calc import CalcError, calculate
from engine.models import (CalcResult, ChoiceEffect, Coefficients, Employee, ExcelDiff, FundingInput,
                           GroupEffect, Placement)


def _run(staff, placements, coefficients, funding) -> CalcResult:
    result = calculate(staff, placements, coefficients, funding)
    if not result.ok:
        raise CalcError(result.error or "計算に失敗した")
    return result


def _place(placements: list[Placement], emp_id: str, group: str) -> list[Placement]:
    """emp_id だけを group の確定した配置へ替える。"""
    change = {"group": group, "held": False, "candidates": []}
    return [p.model_copy(update=change) if p.emp_id == emp_id else p for p in placements]


def _group_effect(group: str, emp_id: str, before: CalcResult, after: CalcResult) -> GroupEffect:
    """本人を除く、その職場の仲間の分配金の変化。"""
    old = {e.emp_id: e.distribution_yen for e in before.employees if e.group == group}
    deltas = [e.distribution_yen - old.get(e.emp_id, 0) for e in after.employees
              if e.group == group and e.emp_id != emp_id]
    changed = [d for d in deltas if d != 0]
    return GroupEffect(group=group, peers=len(changed), peer_delta_yen=changed[0] if changed else 0)


def choice_effects(staff: list[Employee], placements: list[Placement], coefficients: Coefficients,
                   funding: FundingInput, emp_id: str, choices: list[str]) -> dict[str, ChoiceEffect]:
    """emp_id を各選択肢の職場へ置いて再計算する。

    今の配置との差を、選択肢の職場と、今いる職場（あれば）の仲間について返す。
    """
    if emp_id not in {p.emp_id for p in placements}:
        raise CalcError(f"職員 {emp_id} の配置がない")
    before = _run(staff, placements, coefficients, funding)
    current = next(e.group for e in before.employees if e.emp_id == emp_id)
    effects = {}
    for choice in choices:
        after = _run(staff, _place(placements, emp_id, choice), coefficients, funding)
        mine = next(e for e in after.employees if e.emp_id == emp_id)
        groups = [choice] + ([current] if current not in (None, choice) else [])
        effects[choice] = ChoiceEffect(
            choice=choice, self_distribution_yen=mine.distribution_yen,
            groups=[_group_effect(group, emp_id, before, after) for group in groups])
    return effects


def compare_eval_end(staff: list[Employee], placements: list[Placement],
                     eval_end_groups: dict[str, str | None], coefficients: Coefficients,
                     funding: FundingInput) -> ExcelDiff:
    """保留でない全員を期末の所属で数えた結果と比べる。保留の人は両方で保留のまま。"""
    moved = [p.model_copy(update={"group": eval_end_groups[p.emp_id]})
             if not p.held and eval_end_groups.get(p.emp_id) else p for p in placements]
    base = _run(staff, placements, coefficients, funding)
    other = _run(staff, moved, coefficients, funding)
    diffs = [abs(b.amount - o.amount) for b, o in zip(base.employees, other.employees)
             if b.amount is not None and o.amount is not None]
    return ExcelDiff(changed=sum(1 for d in diffs if d), total_yen=sum(diffs))
