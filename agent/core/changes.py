"""変更。案は構造化差分だけで持ち、コードが範囲を検査し、エンジンが前後を計算する（AC13〜AC15、AC17）。

案の文面の解釈は FAKE では固定の型で行う。Claude を使う時も、同じ差分の形だけを受け取る（INV19）。
"""

import copy
import dataclasses
import re
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path

import yaml

from agent.core import audit, ingest, month as months
from agent.core import pack as packs
from agent.core.workspace import locked, read_json, write_json, write_text
from engine.compare import compare
from engine.hashing import stable_hash
from engine.models import Coefficients

CODE_CHANGE = "コードの変更が要ります"
NEEDS_CODE = re.compile(r"(新しい|追加|足す|足して|廃止|なくす|手順|控除|手当|順番|式を)")
VALUE = r"(?P<v>\d+(?:\.\d+)?(?:/\d+)?)"
PATTERNS = (
    (re.compile(rf"(?P<k>.+?)の職種係数を{VALUE}に"), lambda m: (["job_coefficients", m["k"]], f"{m['k']}の職種係数")),
    (re.compile(rf"(?:ランク)?(?P<k>\S+?)の(?:考課|ランク)係数を{VALUE}に"),
     lambda m: (["rank_coefficients", m["k"]], f"ランク{m['k']}の考課係数")),
    (re.compile(rf"等級(?P<g>\d+)の(?:ランク)?(?P<k>\S+?)のポイントを{VALUE}に"),
     lambda m: (["grade_points", int(m["g"]), m["k"]], f"等級{m['g']}のランク{m['k']}のポイント")),
    (re.compile(rf"配分率を{VALUE}に"), lambda m: (["funding", "base_share"], "基本給連動の配分率")),
    (re.compile(rf"役付の月数を{VALUE}に"), lambda m: (["funding", "position_months"], "役付の月数")),
    (re.compile(r"前期の原資を(?P<v>\d+)円?に"), lambda m: (["funding", "previous_total_yen"], "前期の原資")),
    (re.compile(r"表(?P<t>\d+)の?等級(?P<g>\d+)の?(?P<s>\d+)号俸を(?P<v>\d+)円?に"),
     lambda m: (["base_pay", int(m["t"]), int(m["g"]), int(m["s"])], f"基本給表 表{m['t']} 等級{m['g']} {m['s']}号俸")),
)
ROOTS = {"job_coefficients": 2, "rank_coefficients": 2, "grade_points": 3, "position_points": 3, "base_pay": 4,
         "funding": 2}
FUNDING_KEYS = {"base_share", "position_months", "previous_total_yen"}


class ChangeError(ValueError):
    """変更を受け付けられない。理由を日本語の文にする。"""


def parse(text: str) -> dict:
    """文面から差分を作る。読めなければ止まる。計算の手順の変更は作らない。"""
    for pattern, build in PATTERNS:
        if match := pattern.search(text):
            path, label = build(match)
            return {"ops": [{"path": path, "label": label, "after": match["v"]}]}
    if NEEDS_CODE.search(text):
        return {"stop": CODE_CHANGE,
                "why": "係数や月数の変更ではなく、計算の手順そのものの変更に見えます。kounin は手順を書き換えません。"}
    return {"stop": "読み取れませんでした",
            "why": "「看護師の職種係数を1.2にする」のように、何をいくつにするかを書いてください。"}


def _lookup(path: list, coefficients: dict, settings: dict):
    node = settings if path[0] == "funding" else coefficients
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ChangeError(f"「{key}」は今の表にありません")
        node = node[key]
    return node


def _check_value(path: list, value: str) -> None:
    number = Fraction(value)
    head, tail = path[0], path[-1]
    if head == "funding" and tail == "base_share" and not 0 < number < 1:
        raise ChangeError("配分率は0より大きく1より小さい値にしてください")
    if head == "job_coefficients" and not 0 < number <= 5:
        raise ChangeError("職種係数は0より大きく5以下にしてください")
    if head == "rank_coefficients" and not 0 <= number <= 5:
        raise ChangeError("考課係数は0以上5以下にしてください")
    if head in ("base_pay", "funding") and tail in ("previous_total_yen",) or head == "base_pay":
        if number.denominator != 1 or number <= 0:
            raise ChangeError("金額は1円以上の整数にしてください")


def validate(ops: list[dict], pack: packs.Pack) -> list[dict]:
    """範囲の検査。未信頼の案として全項目を調べ、今の値を足して返す（INV19）。"""
    if not ops:
        raise ChangeError("変更がありません")
    checked = []
    for op in ops:
        path = op["path"]
        if path[0] not in ROOTS or len(path) != ROOTS[path[0]]:
            raise ChangeError(CODE_CHANGE)
        if path[0] == "funding" and path[1] not in FUNDING_KEYS:
            raise ChangeError(CODE_CHANGE)
        before = _lookup(path, pack.coefficients_raw, pack.settings)
        try:
            _check_value(path, str(op["after"]))
        except (ValueError, ZeroDivisionError):
            raise ChangeError(f"「{op['after']}」は数として読めません") from None
        checked.append({**op, "before": str(before), "after": str(op["after"])})
    return checked


def _typed(path: list, value: str):
    return int(value) if path[0] == "base_pay" or path[-1] == "previous_total_yen" else value


def applied(pack: packs.Pack, ops: list[dict]) -> tuple[dict, dict]:
    coefficients, settings = copy.deepcopy(pack.coefficients_raw), copy.deepcopy(pack.settings)
    for op in ops:
        path = op["path"]
        node = (settings if path[0] == "funding" else coefficients)
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = _typed(path, op["after"])
    return coefficients, settings


def _variant(pack: packs.Pack, ops: list[dict]) -> packs.Pack:
    coefficients, settings = applied(pack, ops)
    return dataclasses.replace(pack, coefficients_raw=coefficients, settings=settings,
                               coefficients=Coefficients.model_validate(coefficients))


def effect(home: Path, month: str, pack: packs.Pack, ops: list[dict]) -> dict:
    """今月の入力で前後を計算する。総額だけで見せない（AC14）。"""
    before_built = months.assemble(home, month, pack=pack)
    after_pack = _variant(pack, ops)
    after_built = months.assemble(home, month, pack=after_pack)
    plan = pack.settings["funding"]["plans"][0]
    _, before = months.estimate(before_built["staff"], before_built["placements"], pack, plan,
                                before_built["directory"])
    _, after = months.estimate(after_built["staff"], after_built["placements"], after_pack, plan,
                               after_built["directory"])
    result = compare(before, after).model_dump()
    names = {row["emp"].emp_id: (row["emp"].display_name, row["group"]) for row in before_built["rows"]}
    for key in ("largest_up", "largest_down"):
        for item in result[key]:
            item["name"], item["group"] = names[item["emp_id"]]
    return result


def path_of(home: Path, identity: str) -> Path:
    return home / "var" / "changes" / f"{identity}.json"


def propose(home: Path, month: str, text: str, effective_from: str, actor: dict) -> dict:
    with locked(home):
        parsed = parse(text)
        if "stop" in parsed:
            audit.record(home, actor, "変更の案", "文面から", f"できなかった：{parsed['stop']}")
            return parsed
        try:
            day = date.fromisoformat(effective_from)
        except ValueError:
            raise ChangeError("施行日は 2026-10-01 の形で入れてください") from None
        pack = packs.for_date(home, months.base_date(month))
        ops = validate(parsed["ops"], pack)
        version = ingest.latest_version(home, month)
        identity = stable_hash({"ops": ops, "from": day.isoformat(), "pack": pack.digest,
                                "inputs": ingest.input_hash(ingest.input_dir(home, month, version))})[:16]
        path = path_of(home, identity)
        if path.exists():
            return read_json(path)
        record = {"id": identity, "text": text, "ops": ops, "effective_from": day.isoformat(), "status": "案",
                  "month": month, "base_version": pack.version, "base_digest": pack.digest, "input_version": version,
                  "effect": effect(home, month, pack, ops), "by": actor["name"],
                  "at": datetime.now(UTC).isoformat(timespec="seconds")}
        write_json(path, record)
        audit.record(home, actor, "変更の案", record["ops"][0]["label"], "差を見せた", f"ルール {pack.version}")
        return record


def _next_version(home: Path) -> str:
    numbers = [int(path.name) for path in (home / "pack").iterdir() if path.name.isdigit()]
    return str(max(numbers) + 1)


def _write_version(home: Path, pack: packs.Pack, record: dict, actor: dict) -> str:
    """前の版を写して新しい版を作る。前の版は書き換えない（INV5）。"""
    version = _next_version(home)
    target = home / "pack" / version
    coefficients, settings = applied(pack, record["ops"])
    for path in pack.path.rglob("*"):
        if path.is_file() and path.name != "meta.json":
            write_text(target / path.relative_to(pack.path), path.read_text("utf-8"))
    write_text(target / "coefficients.yaml",
               yaml.safe_dump(coefficients, allow_unicode=True, sort_keys=False, default_flow_style=None, width=100000))
    write_text(target / "pack.yaml", yaml.safe_dump(settings, allow_unicode=True, sort_keys=False))
    write_json(target / "meta.json", {"effective_from": record["effective_from"], "approved_by": actor["name"],
                                      "summary": record["text"], "change": record["id"]})
    return version


def approve(home: Path, identity: str, actor: dict) -> dict:
    with locked(home):
        path = path_of(home, identity)
        if not path.exists():
            raise ChangeError("その案は見つかりません")
        record = read_json(path)
        if record["status"] != "案":
            return record                                                # 二重送信は増やさない（INV18）
        month = record["month"]
        pack = packs.for_date(home, months.base_date(month))
        if pack.digest != record["base_digest"] or ingest.latest_version(home, month) != record["input_version"]:
            record["status"] = "失効"
            write_json(path, record)
            audit.record(home, actor, "変更の承認", record["ops"][0]["label"], "拒否：案が古い")
            raise ChangeError("案を作った後に入力かルールが変わりました。案を作り直してください")
        if any(ref.effective_from.isoformat() == record["effective_from"] for ref in packs.versions(home)):
            raise ChangeError("同じ施行日の版があります。施行日を変えて案を作り直してください")
        version = _write_version(home, pack, record, actor)
        record.update(status="承認", version=version, approved_by=actor["name"],
                      approved_at=datetime.now(UTC).isoformat(timespec="seconds"))
        write_json(path, record)
        audit.record(home, actor, "変更の承認", record["ops"][0]["label"], "新しい版になった", f"ルール {version}")
        return record


def reject(home: Path, identity: str, actor: dict) -> dict:
    with locked(home):
        path = path_of(home, identity)
        if not path.exists():
            raise ChangeError("その案は見つかりません")
        record = read_json(path)
        if record["status"] == "案":
            record["status"] = "取り下げ"
            write_json(path, record)
            audit.record(home, actor, "変更の取り下げ", record["ops"][0]["label"], "取り下げた")
        return record


def listing(home: Path) -> list[dict]:
    base = home / "var" / "changes"
    records = [read_json(path) for path in base.glob("*.json")] if base.exists() else []
    return sorted(records, key=lambda record: record["at"], reverse=True)


def version_list(home: Path) -> list[dict]:
    result = []
    for ref in packs.versions(home):
        pack = packs.load(home, ref.id)
        result.append({"version": ref.id, "effective_from": ref.effective_from.isoformat(), "summary": pack.summary})
    return list(reversed(result))
