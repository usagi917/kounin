"""定期昇給。確定ランクごとの号俸の増分で計算し、表の上限で止まる。読めない人は埋めずに止める。"""

from collections.abc import Mapping
from typing import Literal

from engine.models import Strict

RaiseStatus = Literal["昇給", "据え置き", "上限", "止まり"]


class RaiseResult(Strict):
    emp_id: str
    status: RaiseStatus
    step_before: int | None
    step_after: int | None
    pay_before: int | None
    pay_after: int | None


def raise_of(emp_id: str, rank: str | None, step: int | None, table: Mapping[int, int] | None,
             increments: Mapping[str, int]) -> RaiseResult:
    """table は本人の表と等級の、号俸ごとの基本給。"""
    table = table or {}
    before = table.get(step) if step is not None else None
    if rank not in increments or before is None:
        return RaiseResult(emp_id=emp_id, status="止まり", step_before=step, step_after=step,
                           pay_before=before, pay_after=before)
    wanted = step + increments[rank]
    after = min(wanted, max(table))
    status = "上限" if wanted > after else "昇給" if after > step else "据え置き"
    return RaiseResult(emp_id=emp_id, status=status, step_before=step, step_after=after,
                       pay_before=before, pay_after=table[after])


class RaiseSummary(Strict):
    raised: int
    held_flat: int           # 据え置き
    capped: int              # 上限
    stopped: int
    monthly_increase: int    # 基本給の月額の増分の合計


def summarize(results: list[RaiseResult]) -> RaiseSummary:
    counted = [item for item in results if item.status != "止まり"]
    return RaiseSummary(raised=sum(item.status == "昇給" for item in results),
                        held_flat=sum(item.status == "据え置き" for item in results),
                        capped=sum(item.status == "上限" for item in results),
                        stopped=len(results) - len(counted),
                        monthly_increase=sum(item.pay_after - item.pay_before for item in counted))
