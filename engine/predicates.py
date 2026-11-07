"""判定条件の語彙。純粋関数だけを置く。"""

import calendar
import operator
import re
from datetime import date

from engine.models import Employee, NormalScope, Period, Rule, Segment, UndecidedRange

DAYS_PER_MONTH = 30.4375
COMPARISONS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}
COMPARISON = re.compile(r"(<=|>=|<|>|==)\s*(\d+)")


def months(segment: Segment) -> int:
    """在籍の長さを整数の月数にする。開始日と終了日がそろっている在籍だけに使う。"""
    if segment.start is None or segment.end is None:
        raise ValueError("開始日か終了日がない在籍の月数は計算できない")
    return round(((segment.end - segment.start).days + 1) / DAYS_PER_MONTH)


def clip(emp: Employee, period: Period) -> Employee:
    """在籍を評価期間の中へ切り詰める。期間の外の在籍は落とす。"""
    segments = [segment.model_copy(update={"start": max(segment.start, period.start),
                                           "end": min(segment.end, period.end)})
                for segment in emp.segments if segment.start <= period.end and segment.end >= period.start]
    return emp.model_copy(update={"segments": segments})


def has_transfer(emp: Employee) -> bool:
    return len({segment.group for segment in emp.segments}) > 1


def has_rotation(emp: Employee) -> bool:
    return any(segment.kind == "新卒ローテ" for segment in emp.segments)


def segments_complete(emp: Employee) -> bool:
    return bool(emp.segments) and all(s.start is not None and s.end is not None for s in emp.segments)


def parse_comparison(expr: str) -> tuple[str, int]:
    """月数の比較（"< 4"）を演算子と数に分ける。"""
    match = COMPARISON.fullmatch(expr.strip())
    if match is None:
        raise ValueError(f"月数の比較は「< 4」の形で書いてください（<, <=, >, >=, ==）: {expr!r}")
    return match.group(1), int(match.group(2))


def tenure_months(hired: date, on: date) -> int:
    """入職日から基準日までの満月数。基準日が月末なら、入職日の日付が大きくても1か月と数える。"""
    months = (on.year - hired.year) * 12 + on.month - hired.month
    if on.day < hired.day and on.day != calendar.monthrange(on.year, on.month)[1]:
        months -= 1
    return months


def tenure_months_at_eval_end(emp: Employee, period: Period) -> int | None:
    """評価期間の末日時点の勤続月数。入職日がなければ None を返す。"""
    return None if emp.hire_date is None else tenure_months(emp.hire_date, period.end)


def validate_vocabulary(rule: Rule, ranks: set[str] | None = None) -> None:
    """語彙の検査。型で防げない値の誤りを日本語の理由で拒否する。"""
    if rule.when.tenure_months_at_eval_end is not None:
        parse_comparison(rule.when.tenure_months_at_eval_end)
    if rule.then.group_basis is None and rule.then.grade is None:
        raise ValueError("then には group_basis か grade のどちらかが必要です")
    if rule.then.grade is not None and ranks is not None and rule.then.grade not in ranks:
        raise ValueError(f"then.grade は考課ランク {sorted(ranks)} のどれかにしてください: {rule.then.grade}")


def matches(emp: Employee, rule: Rule, period: Period | None = None) -> bool:
    """when の条件をすべて満たすか。勤続月数の条件は、期間か入職日が分からなければ満たさない。"""
    when = rule.when
    if when.tenure_months_at_eval_end is not None:
        tenure = None if period is None else tenure_months_at_eval_end(emp, period)
        operator_, bound = parse_comparison(when.tenure_months_at_eval_end)
        if tenure is None or not COMPARISONS[operator_](tenure, bound):
            return False
    return ((when.has_transfer is None or when.has_transfer == has_transfer(emp))
            and (when.has_rotation is None or when.has_rotation == has_rotation(emp))
            and (when.employment_type is None or when.employment_type == emp.employment_type))


def counted_segments(emp: Employee, rule: Rule) -> list[Segment]:
    """ルールの除外を当てた後に、在籍の長さへ数える在籍を返す。"""
    def excluded(segment: Segment) -> bool:
        return any(item.kind == segment.kind
                   and (item.max_months is None or months(segment) <= item.max_months)
                   for item in rule.exclude_segments)
    return [segment for segment in emp.segments if not excluded(segment)]


def longest_groups(segments: list[Segment]) -> list[str]:
    """在籍が最長の職場を返す。2つ以上なら同点である。"""
    totals: dict[str, int] = {}
    for segment in segments:
        totals[segment.group] = totals.get(segment.group, 0) + months(segment)
    if not totals:
        return []
    longest = max(totals.values())
    return sorted(group for group, total in totals.items() if total == longest)


def eval_end_group(emp: Employee, period: Period) -> str | None:
    for segment in emp.segments:
        if segment.start and segment.end and segment.start <= period.end <= segment.end:
            return segment.group
    return None


def undecided_hit(emp: Employee, rule: Rule) -> UndecidedRange | None:
    for item in rule.undecided:
        if any(s.kind == item.kind and months(s) >= item.min_months for s in emp.segments):
            return item
    return None


def normal_scope_failures(emp: Employee, scope: NormalScope) -> list[str]:
    failures = []
    if emp.employment_type not in scope.employment_types:
        failures.append("employment_type")
    if has_transfer(emp) != scope.has_transfer:
        failures.append("has_transfer")
    return failures
