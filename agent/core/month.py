"""月の実行。全職員を判定し、賞与と昇給の見込みを計算する（AC1〜AC3、AC20）。

金額は engine の純粋関数だけが計算する。ここは入力を組み立てて結果を保存する。
"""

import calendar
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from agent.core import audit, inbox, ingest
from agent.core import pack as packs
from agent.core.workspace import locked, read_json, write_json
from engine import ENGINE_VERSION
from engine.calc import CalcError, calculate, is_fixed, total_basis
from engine.funding import funding_input, plan_total
from engine.hashing import stable_hash
from engine.judge import effective_rank, judge
from engine.models import Category, Decision, Employee, JudgeResult, Placement, Segment
from engine import predicates
from engine.predicates import eval_end_group
from engine.raises import raise_of, summarize
from engine.versions import VersionError

STOPPED = (Category.MISSING, Category.UNREGISTERED, Category.CONFLICT)


class MonthError(RuntimeError):
    """月を回せない。理由を日本語の文にする。"""


@dataclass
class Person:
    emp: Employee
    missing: list[str] = field(default_factory=list)   # 計算に要るのに空の項目


def base_date(month: str) -> date:
    year, number = (int(part) for part in month.split("-"))
    return date(year, number, calendar.monthrange(year, number)[1])


def _int(row: dict, column: str, person: Person | None = None) -> int | None:
    text = (row.get(column) or "").strip()
    if not text:
        if person is not None:
            person.missing.append(column)
        return None
    return int(text)


def _segments(rows: list[dict], on: date) -> list[Segment]:
    """終了日の空欄は、最後の行に限り「在籍中」と読む。途中の行の空欄は欠けとして残す。"""
    result = []
    for index, row in enumerate(rows):
        start = date.fromisoformat(row["開始日"]) if row["開始日"] else None
        end = date.fromisoformat(row["終了日"]) if row["終了日"] else None
        if end is None and start is not None and index == len(rows) - 1:
            end = on
        result.append(Segment(group=row["職場"], start=start, end=end, kind=row["種類"]))
    return result


def _person(row: dict, attendance: dict | None, segments: list[dict], rank: str | None, on: date) -> Person:
    person = Person(emp=Employee(emp_id=row["職員番号"], display_name=row["氏名"], segments=[]))
    fixed = _int(row, "手入力の支給額")
    fields: dict = {
        "employment_type": row["雇用区分"] or None, "job_type": row["職種"] or None,
        "table_no": _int(row, "表No"), "grade": _int(row, "等級"), "step": _int(row, "号俸"),
        "adjustment_pay": _int(row, "調整給", person) or 0, "treatment_pay": _int(row, "処遇改善", person) or 0,
        "position": row["職位"] or None, "manager_allowance": _int(row, "管理職手当", person) or 0,
        "special_allowance": _int(row, "特別加給金", person) or 0, "fixed_amount": fixed,
        "fixed_distribution": _int(row, "手入力の分配金") or 0, "distribution_member": row["分配の母数"] == "1",
        "hire_date": date.fromisoformat(row["入職日"]) if row["入職日"] else None, "rank": rank or None,
        "segments": _segments(segments, on),
    }
    if attendance is None:
        person.missing.append("勤怠")
    else:
        names = {"attendance_coefficient": "精勤係数", "absence_hundredths": "欠勤等の率",
                 "attendance_loss_rate": "未出勤率"}
        for key, column in names.items():
            if attendance[column].strip():
                fields[key] = attendance[column].strip()
            else:
                person.missing.append(column)
        fields["service_months"] = _int(attendance, "勤続月数", person)
    person.emp = person.emp.model_copy(update=fields)
    return person


def load_people(directory: Path, period_id: str, on: date) -> list[Person]:
    attendance = {row["職員番号"]: row for row in ingest.load_rows(directory, ingest.ATTENDANCE)}
    ranks = {row["職員番号"]: row["ランク"] for row in ingest.load_rows(directory, ingest.RANKS)
             if row["期"] == period_id}
    segments: dict[str, list[dict]] = {}
    for row in ingest.load_rows(directory, ingest.SEGMENTS):
        segments.setdefault(row["職員番号"], []).append(row)
    return [_person(row, attendance.get(row["職員番号"]), segments.get(row["職員番号"], []),
                    ranks.get(row["職員番号"]), on)
            for row in ingest.load_rows(directory, ingest.STAFF)]


def _calc_gaps(person: Person, rank: str | None, pack: packs.Pack) -> list[str]:
    """計算に要るのに読めない項目を返す。埋めずに止めるためである（INV13）。"""
    emp = person.emp
    gaps = list(person.missing)
    if emp.hire_date is None:
        gaps.append("入職日")
    if is_fixed(emp):
        return gaps
    coef = pack.coefficients
    if rank is None:
        gaps.append("考課")
    elif rank not in coef.rank_coefficients:
        gaps.append("考課のランク")
    if emp.job_type is None or emp.job_type not in coef.job_coefficients:
        gaps.append("職種")
    steps = coef.base_pay.get(emp.table_no, {}).get(emp.grade)
    if steps is None:
        gaps.append("表Noと等級")
    elif emp.step not in steps:
        gaps.append("号俸")
    if emp.position is not None and emp.position not in coef.position_classes:
        gaps.append("職位")
    return gaps


def _decisions(directory: Path) -> dict[str, Decision]:
    return {row["職員番号"]: Decision(id=f"判定-{row['職員番号']}", emp_id=row["職員番号"], group=row["職場"])
            for row in ingest.load_rows(directory, ingest.JUDGMENTS)}


def _placement(emp: Employee, verdict: JudgeResult, rank: str | None) -> Placement:
    if verdict.category in STOPPED:
        candidates = sorted({segment.group for segment in emp.segments})
        return Placement(emp_id=emp.emp_id, held=True, candidates=candidates, rank=rank)
    group = verdict.group or (emp.segments[-1].group if emp.segments else None)
    return Placement(emp_id=emp.emp_id, group=group, rule_ids=verdict.rule_ids, rank=rank)


def _history(emp: Employee, period) -> str:
    """対象期間の在籍の長さを画面の言葉にする。日付が欠けていれば空にする。"""
    if not predicates.segments_complete(emp) or len(emp.segments) < 2:
        return ""
    parts = [f"{item.group} {predicates.months(item)}か月" + ("" if item.kind == "通常" else f"（{item.kind}）")
             for item in predicates.clip(emp, period).segments]
    return " → ".join(parts)


def _finance(directory: Path) -> dict[str, dict]:
    return {row["期"]: row for row in ingest.load_rows(directory, ingest.FINANCE)}


def assemble(home: Path, month: str, version: int | None = None, pack: packs.Pack | None = None) -> dict:
    """判定まで進める。計算へ渡す staff と placements、1人ごとの判定を返す。"""
    on = base_date(month)
    pack = pack or packs.for_date(home, on)
    info = pack.period_on(on)
    period = info.period.model_copy(update={"end": min(info.period.end, on)})
    directory = ingest.input_dir(home, month, version)
    decisions = _decisions(directory)
    rows, staff, placements = [], [], []
    for person in load_people(directory, info.period.id, on):
        emp = person.emp
        verdict = judge(emp, list(pack.rules), pack.scope, period, decisions.get(emp.emp_id))
        rank = effective_rank(emp, list(pack.rules), period)
        gaps = _calc_gaps(person, rank, pack)
        if gaps and verdict.category not in STOPPED:
            verdict = verdict.model_copy(update={"category": Category.MISSING, "reason_code": "入力の欠け"})
        if gaps or verdict.category == Category.MISSING:
            excluded = True
        else:
            excluded = False
            staff.append(emp)
            placements.append(_placement(emp, verdict, rank))
        rows.append({"emp": emp, "verdict": verdict, "rank": rank, "gaps": gaps, "excluded": excluded,
                     "group": verdict.group or eval_end_group(emp, period)
                     or next((s.group for s in reversed(emp.segments)), None)})
    return {"on": on, "pack": pack, "info": info, "period": period, "directory": directory, "rows": rows,
            "staff": staff, "placements": placements}


def estimate(staff: list[Employee], placements: list[Placement], pack: packs.Pack, plan: dict, directory: Path):
    """案の原資で全員を計算する。"""
    funding = pack.settings["funding"]
    finance = _finance(directory)
    now, before = finance.get("今期"), finance.get("前期")
    if plan["type"] == "profit_share" and not (now and before and now["経常利益"] and before["経常利益"]):
        raise MonthError("決算のファイルに経常利益がないため、増益の案を作れません")
    total = plan_total(plan, funding["previous_total_yen"], int(now["経常利益"]) if now else 0,
                       int(before["経常利益"]) if before else 0)
    basis = total_basis(staff, pack.coefficients)
    result = calculate(staff, placements, pack.coefficients,
                       funding_input(total, basis, funding["base_share"], funding["position_months"]))
    if not result.ok:
        raise MonthError(result.error or "計算に失敗しました")
    return total, result


def run_identity(home: Path, month: str) -> tuple[str, int, packs.Pack]:
    pack = packs.for_date(home, base_date(month))
    version = ingest.latest_version(home, month)
    identity = stable_hash({"month": month, "inputs": ingest.input_hash(ingest.input_dir(home, month, version)),
                            "pack": pack.digest, "engine": ENGINE_VERSION})
    return identity, version, pack


def _raises(built: dict) -> dict:
    pack = built["pack"]
    results = []
    for row in built["rows"]:
        emp = row["emp"]
        if is_fixed(emp):
            continue
        table = pack.coefficients.base_pay.get(emp.table_no, {}).get(emp.grade)
        results.append(raise_of(emp.emp_id, row["rank"], emp.step, table, pack.increments))
    return {"results": {item.emp_id: item.model_dump() for item in results},
            "summary": summarize(results).model_dump()}


def _build(home: Path, month: str, identity: str, version: int, pack: packs.Pack) -> dict:
    built = assemble(home, month, version, pack)
    plan = pack.settings["funding"]["plans"][0]
    total, result = estimate(built["staff"], built["placements"], pack, plan, built["directory"])
    raises = _raises(built)
    calc = {row.emp_id: row for row in result.employees}
    people, counts = [], {category.value: 0 for category in Category}
    for row in built["rows"]:
        emp, verdict = row["emp"], row["verdict"]
        counts[verdict.category.value] += 1
        item = calc.get(emp.emp_id)
        people.append({
            "emp_id": emp.emp_id, "name": emp.display_name, "job": emp.job_type, "group": row["group"],
            "category": verdict.category.value, "reason": verdict.reason_code, "gaps": row["gaps"],
            "failed": verdict.failed_conditions, "missing": verdict.missing, "rule_ids": verdict.rule_ids,
            "rank": row["rank"], "options": sorted({s.group for s in emp.segments}),
            "history": _history(emp, built["period"]),
            "amount": item.total_pay if item else None, "trace": [t.model_dump() for t in item.trace] if item else [],
            "raise": raises["results"].get(emp.emp_id),
        })
    stopped = sum(counts[category.value] for category in STOPPED)
    info = built["info"]
    return {"id": identity, "month": month, "input_version": version, "pack_version": pack.version,
            "engine_version": ENGINE_VERSION, "base_date": built["on"].isoformat(), "period_id": info.period.id,
            "period_name": info.name, "payment_date": info.payment_date.isoformat(), "counts": counts,
            "total": len(people), "stopped": stopped, "proceeding": len(people) - stopped,
            "estimate": {"plan": plan["name"], "fund": total, "total_pay": result.total_pay,
                         "unit_price": result.unit_price, "excluded": sum(r["excluded"] for r in built["rows"])},
            "raises": raises["summary"], "people": people}


def run_path(home: Path, month: str, identity: str) -> Path:
    return home / "var" / "months" / month / "runs" / f"{identity}.json"


def pointer(home: Path, month: str) -> Path:
    return home / "var" / "months" / month / "run.json"


def run(home: Path, month: str, actor: dict | None) -> tuple[dict, bool]:
    """回す。同じ組の結果があれば作り直さない。作ったかどうかも返す。"""
    with locked(home):
        try:
            identity, version, pack = run_identity(home, month)
            path = run_path(home, month, identity)
            created = not path.exists()
            if created:
                write_json(path, _build(home, month, identity, version, pack))
            result = read_json(path)
            write_json(pointer(home, month), {"id": identity})
            inbox.sync(home, month, result)
        except (CalcError, VersionError, packs.PackError, FileNotFoundError, ValueError) as error:
            audit.record(home, actor, "今月分の計算", month, f"できなかった：{error}")
            raise MonthError(str(error)) from error
        audit.record(home, actor, "今月分の計算", month,
                     f"{result['proceeding']}人を計算し、{result['stopped']}人は確認が必要"
                     + ("" if created else "（同じ結果）"),
                     f"入力 {version}、ルール {pack.version}")
        return result, created


def latest(home: Path, month: str) -> dict | None:
    path = pointer(home, month)
    if not path.exists():
        return None
    return read_json(run_path(home, month, read_json(path)["id"]))


def is_stale(home: Path, month: str) -> bool:
    path = pointer(home, month)
    return path.exists() and read_json(path)["id"] != run_identity(home, month)[0]
