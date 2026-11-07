"""変更の前後の差。総額だけで見せないよう、増減の人数と大きい人を返す（AC14）。"""

from engine.models import CalcResult, Strict


class PersonDelta(Strict):
    emp_id: str
    before: int
    after: int
    delta: int


class Comparison(Strict):
    total_before: int
    total_after: int
    total_delta: int
    up: int
    down: int
    same: int
    skipped: int             # どちらかに金額がない人。差に数えない
    largest_up: list[PersonDelta]
    largest_down: list[PersonDelta]


def compare(before: CalcResult, after: CalcResult, top: int = 5) -> Comparison:
    old = {row.emp_id: row.total_pay for row in before.employees}
    new = {row.emp_id: row.total_pay for row in after.employees}
    pairs = [PersonDelta(emp_id=emp_id, before=old[emp_id], after=new[emp_id], delta=new[emp_id] - old[emp_id])
             for emp_id in sorted(old.keys() & new.keys())
             if old[emp_id] is not None and new[emp_id] is not None]
    rises = sorted((p for p in pairs if p.delta > 0), key=lambda p: (-p.delta, p.emp_id))
    falls = sorted((p for p in pairs if p.delta < 0), key=lambda p: (p.delta, p.emp_id))
    total_before, total_after = sum(p.before for p in pairs), sum(p.after for p in pairs)
    return Comparison(total_before=total_before, total_after=total_after, total_delta=total_after - total_before,
                      up=len(rises), down=len(falls), same=len(pairs) - len(rises) - len(falls),
                      skipped=len(old.keys() | new.keys()) - len(pairs),
                      largest_up=rises[:top], largest_down=falls[:top])
