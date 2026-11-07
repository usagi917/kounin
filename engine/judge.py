"""5区分の判定。上から順に調べ、最初に当たった区分を返す。"""

from engine import predicates as p
from engine.models import Category, Decision, Employee, JudgeResult, NormalScope, Period, Rule


def _resolve(emp: Employee, rule: Rule, period: Period) -> str | None:
    """ルールが決める職場を返す。決まらなければ None を返す。"""
    if rule.then.group_basis == "eval_end":
        return p.eval_end_group(emp, period)
    longest = p.longest_groups(p.counted_segments(emp, rule))
    return longest[0] if len(longest) == 1 else None


def _clipped(emp: Employee, period: Period) -> Employee:
    return p.clip(emp, period) if p.segments_complete(emp) else emp


def effective_rank(emp: Employee, rules: list[Rule], period: Period) -> str | None:
    """考課ランクを返す。then.grade のルールが当たれば上書きし、無ければ職員の rank を返す。"""
    emp = _clipped(emp, period)
    for rule in rules:
        if rule.then.grade is not None and p.matches(emp, rule, period):
            return rule.then.grade
    return emp.rank


def judge(emp: Employee, rules: list[Rule], scope: NormalScope, period: Period,
          decision: Decision | None = None) -> JudgeResult:
    end_group = p.eval_end_group(emp, period)
    if decision is not None:
        if decision.emp_id != emp.emp_id:
            raise ValueError(f"個別判断 {decision.id} は別の職員のものです: {decision.emp_id}")
        return JudgeResult(emp_id=emp.emp_id, category=Category.EXCEPTION, eval_end_group=end_group,
                           group=decision.group, rule_ids=[decision.id], reason_code="個別判断")
    rules = [rule for rule in rules if rule.then.group_basis is not None]   # 所属を決めるのはこのルールだけ

    def result(category: Category, **fields) -> JudgeResult:
        return JudgeResult(emp_id=emp.emp_id, category=category, eval_end_group=end_group, **fields)

    emp = _clipped(emp, period)
    matched = [rule for rule in rules if p.matches(emp, rule, period)]

    missing = []
    if emp.employment_type is None:
        missing.append("employment_type")
    placeless = emp.fixed_amount is not None and not emp.distribution_member and not emp.segments
    if (p.has_transfer(emp) or matched or not emp.segments) and not p.segments_complete(emp) and not placeless:
        missing.append("segments")
    if missing:
        return result(Category.MISSING, reason_code="入力の欠け", missing=missing)

    rule_ids = [rule.id for rule in matched]

    undecided = [(rule, hit) for rule in matched if (hit := p.undecided_hit(emp, rule))]
    resolved = {_resolve(emp, rule, period) for rule in matched}
    if not undecided and len(resolved - {None}) > 1:
        return result(Category.CONFLICT, reason_code="ルールの食い違い", rule_ids=rule_ids)

    if undecided:
        rule, hit = undecided[0]
        return result(Category.UNREGISTERED, reason_code="担当者が決めなかった", rule_ids=[rule.id],
                      undecided_id=hit.id, judgment_id=hit.judgment,
                      failed_conditions=[f"{hit.kind}が{hit.min_months}か月以上"],
                      normal_scope_failed=p.normal_scope_failures(emp, scope))

    if matched:
        if None in resolved:
            return result(Category.UNREGISTERED, reason_code="判定なし", rule_ids=rule_ids,
                          failed_conditions=["除外後の在籍の最長が決まらない"],
                          normal_scope_failed=p.normal_scope_failures(emp, scope))
        return result(Category.EXCEPTION, group=resolved.pop(), rule_ids=rule_ids,
                      reason_code="例外ルールに一致")

    scope_failed = p.normal_scope_failures(emp, scope)
    if not scope_failed:
        return result(Category.NORMAL, group=emp.segments[0].group if emp.segments else None,
                      reason_code="通常の範囲")
    return result(Category.UNREGISTERED, reason_code="判定なし",
                  failed_conditions=["適用できる例外ルールがない"], normal_scope_failed=scope_failed)
