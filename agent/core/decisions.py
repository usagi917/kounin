"""支給。3案、理事長の決定、失効、給与システム用 CSV（AC16、AC21〜AC23）。

決定は入力、ルール、係数、エンジンの版と結果の組に結びつく（INV6）。CSV は決定の記録からだけ作る（INV11、INV12）。
"""

import csv
import io
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from agent.core import audit, month as months
from agent.core.workspace import locked, read_json, write_json, write_text
from engine import rounding
from engine.hashing import stable_hash
from engine.models import Strict

CSV_COLUMNS = ["職員番号", "氏名", "本支給額", "精勤分配金", "特別加給金", "支給計"]
DANGEROUS = ("=", "+", "-", "@", "\t", "\r")
BAND_STEP = 50_000        # 案の比較に使う支給額の帯の幅
BAND_TOP = 1_000_000      # これより上は1つの帯にまとめる


class PayError(ValueError):
    """支給の操作ができない。理由を日本語の文にする。"""


class PayRow(Strict):
    emp_id: str
    name: str
    main_pay: int
    distribution: int
    special: int
    total: int


class Decided(Strict):
    """決定の記録。CSV の関数はこの型だけを受け取る。見込みの型は受け取らない（INV12）。"""
    month: str
    plan_id: str
    plan_name: str
    run_id: str
    result_hash: str
    rows: list[PayRow]


def _dir(home: Path, month: str) -> Path:
    return home / "var" / "decisions" / month


def _ready_run(home: Path, month: str) -> dict:
    run = months.latest(home, month)
    if run is None:
        raise PayError("先に今月分を計算してください")
    if months.is_stale(home, month):
        raise PayError("新しいデータか版があります。先に今月分を計算してください")
    if run["stopped"]:
        raise PayError(f"確認が必要な人が{run['stopped']}人います。全員の計算が終わるまで3案を作れません")
    return run


def _calc_plans(home: Path, month: str, run: dict) -> dict:
    built = months.assemble(home, month, run["input_version"])
    pack = built["pack"]
    names = {row["emp"].emp_id: row["emp"].display_name for row in built["rows"]}
    plans = []
    for plan in pack.settings["funding"]["plans"]:
        fund, result = months.estimate(built["staff"], built["placements"], pack, plan, built["directory"])
        rows = [PayRow(emp_id=row.emp_id, name=names[row.emp_id], main_pay=row.main_pay,
                       distribution=row.distribution_net, special=row.special_allowance,
                       total=row.total_pay).model_dump() for row in result.employees]
        plans.append({"id": plan["id"], "name": plan["name"], "fund": fund, "total_pay": result.total_pay,
                      "unit_price": result.unit_price, "rows": rows,
                      "result_hash": stable_hash({"run": run["id"], "plan": plan["id"], "rows": rows})})
    return {"run_id": run["id"], "plans": plans}


def build_plans(home: Path, month: str, actor: dict) -> dict:
    with locked(home):
        run = _ready_run(home, month)
        data = _calc_plans(home, month, run)
        write_json(_dir(home, month) / f"plans-{run['id'][:16]}.json", data)
        audit.record(home, actor, "3案の作成", month, "3案を作った",
                     f"入力 {run['input_version']}、ルール {run['pack_version']}")
        return data


def current_plans(home: Path, month: str) -> dict | None:
    run = months.latest(home, month)
    if run is None:
        return None
    path = _dir(home, month) / f"plans-{run['id'][:16]}.json"
    return read_json(path) if path.exists() else None


def _band_edges(plans: list[dict]) -> list[int]:
    """全案で共通の帯の下限。案を替えても同じ軸で比べられるようにする。"""
    totals = [row["total"] for plan in plans for row in plan["rows"]]
    low = min(totals) // BAND_STEP * BAND_STEP
    high = min(max(totals) // BAND_STEP * BAND_STEP, BAND_TOP)
    return list(range(low, max(high, low) + 1, BAND_STEP))


def _bands(rows: list[dict], edges: list[int]) -> list[dict]:
    counts = [0] * len(edges)
    for row in rows:
        counts[min((row["total"] - edges[0]) // BAND_STEP, len(edges) - 1)] += 1
    return [{"from": edge, "count": count} for edge, count in zip(edges, counts)]


def _moved(rows: list[dict], base: dict[str, int]) -> dict[str, int]:
    gaps = [row["total"] - base[row["emp_id"]] for row in rows]
    return {"up": sum(gap > 0 for gap in gaps), "down": sum(gap < 0 for gap in gaps),
            "same": sum(gap == 0 for gap in gaps)}


def summaries(data: dict) -> list[dict]:
    """案の比較に要る集計だけを返す。個人別の金額は含めない。"""
    plans = data["plans"]
    base = plans[0]
    edges = _band_edges(plans)
    before = {row["emp_id"]: row["total"] for row in base["rows"]}
    return [{key: plan[key] for key in ("id", "name", "fund", "total_pay", "unit_price")}
            | {"delta": plan["total_pay"] - base["total_pay"], "people": len(plan["rows"]),
               "average": rounding.yen(Fraction(plan["total_pay"], len(plan["rows"]))),
               "step": BAND_STEP, "bands": _bands(plan["rows"], edges), "moved": _moved(plan["rows"], before)}
            for plan in plans]


def decide(home: Path, month: str, plan_id: str, actor: dict, expected_run: str | None = None) -> dict:
    """expected_run を渡すと、見せた案と同じ結果の時だけ決める。"""
    with locked(home):
        run = _ready_run(home, month)
        if expected_run is not None and run["id"] != expected_run:
            raise PayError("見ていた案の後に計算が変わりました。人事担当に新しいURLをもらってください")
        data = current_plans(home, month)
        if data is None or data["run_id"] != run["id"]:
            raise PayError("先に3案を作ってください")
        plan = next((item for item in data["plans"] if item["id"] == plan_id), None)
        if plan is None:
            raise PayError("その案はありません")
        record = {"month": month, "plan_id": plan["id"], "plan_name": plan["name"], "run_id": run["id"],
                  "result_hash": plan["result_hash"], "by": actor["name"], "role": actor["role"],
                  "at": datetime.now(UTC).isoformat(timespec="seconds"), "rows": plan["rows"]}
        write_json(_dir(home, month) / "decision.json", record)
        audit.record(home, actor, "原資の決定", f"{month} 案{plan['id']}", "決定した",
                     f"入力 {run['input_version']}、ルール {run['pack_version']}")
        return record


def decision(home: Path, month: str) -> dict | None:
    path = _dir(home, month) / "decision.json"
    return read_json(path) if path.exists() else None


def is_expired(home: Path, month: str, record: dict) -> bool:
    """今の入力、ルール、係数、エンジンの組が決定の時と違えば失効である（AC22）。"""
    return months.run_identity(home, month)[0] != record["run_id"]


def valid_decision(home: Path, month: str) -> Decided:
    record = decision(home, month)
    if record is None:
        raise PayError("理事長の決定がまだありません")
    if is_expired(home, month, record):
        raise PayError("決定の後に入力、ルール、係数のどれかが変わりました。決定は失効しています")
    decided = Decided.model_validate({key: record[key] for key in Decided.model_fields})
    fresh = _calc_plans(home, month, months.latest(home, month))
    plan = next(item for item in fresh["plans"] if item["id"] == decided.plan_id)
    if plan["result_hash"] != decided.result_hash:
        raise PayError("決定の時の試算と今の計算が一致しません。決定は失効しています")
    return decided


def exported(home: Path, month: str, record: dict) -> bool:
    return (home / "var" / "exports" / f"賞与-{month}-案{record['plan_id']}.csv").exists()


def _safe(value: object) -> str:
    text = str(value)
    return "'" + text if isinstance(value, str) and text.startswith(DANGEROUS) else text


def to_csv(decided: Decided) -> str:
    if not isinstance(decided, Decided):
        raise TypeError("CSV は決定の記録からだけ作ります")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in decided.rows:
        writer.writerow([_safe(row.emp_id), _safe(row.name), row.main_pay, row.distribution, row.special, row.total])
    return out.getvalue()


def export(home: Path, month: str, actor: dict) -> tuple[str, str]:
    with locked(home):
        try:
            decided = valid_decision(home, month)
        except PayError as error:
            audit.record(home, actor, "CSV の出力", month, f"拒否：{error}")
            raise
        text = to_csv(decided)
        name = f"賞与-{month}-案{decided.plan_id}.csv"
        write_text(home / "var" / "exports" / name, text)
        audit.record(home, actor, "CSV の出力", f"{month} 案{decided.plan_id}", f"{len(decided.rows)}人分を出した")
        return name, text
