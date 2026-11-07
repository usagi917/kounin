"""連絡。止まった人を宛先ごとの連絡にする（AC5、AC6）。再計算で増やさず、解けたら閉じる（AC3）。"""

from datetime import UTC, datetime
from pathlib import Path

from agent.core import ingest
from agent.core.workspace import append_jsonl, read_jsonl, users
from engine.hashing import stable_hash

FILE, RANK, JUDGE = "file", "rank", "judge"
ATTENDANCE_GAPS = {"勤怠", "精勤係数", "欠勤等の率", "未出勤率", "勤続月数"}
RANK_GAPS = {"考課", "考課のランク"}


def path(home: Path) -> Path:
    return home / "var" / "inbox.jsonl"


def _boss_of(group: str | None, people: list[dict]) -> dict | None:
    return next((user for user in people if user["role"] == "上司" and group in user.get("groups", [])), None)


def _hr() -> dict:
    return {"role": "人事担当", "user_id": None}


def _route(person: dict, people: list[dict]) -> dict | None:
    """誰に何を聞くかを決める。止まっていない人は None を返す。"""
    gaps, category = set(person["gaps"]), person["category"]
    if category in ("通常ルール適用", "例外適用"):
        return None
    if category == "情報不足":
        if person["missing"] and "segments" in person["missing"]:
            return {"kind": FILE, "file": ingest.SEGMENTS, "reason": "所属の日付が欠けている", "to": _hr()}
        if gaps & ATTENDANCE_GAPS:
            return {"kind": FILE, "file": ingest.ATTENDANCE, "reason": "勤怠が欠けている", "to": _hr()}
        if gaps and gaps <= RANK_GAPS:
            boss = _boss_of(person["group"], people)
            to = {"role": "上司", "user_id": boss["id"]} if boss else _hr()
            return {"kind": RANK, "reason": "考課が届いていない", "to": to}
        return {"kind": FILE, "file": ingest.STAFF, "reason": "職員の項目が欠けている", "to": _hr()}
    return {"kind": JUDGE, "reason": "どの職場で数えるかが決まっていない", "to": {"role": "前任", "user_id": None}}


def sync(home: Path, month: str, run: dict) -> None:
    """今の結果に合わせて連絡を開け閉めする。呼び出し側が排他を持つ。"""
    people = users(home)
    wanted = {}
    for person in run["people"]:
        route = _route(person, people)
        if route is None:
            continue
        identity = stable_hash({"month": month, "kind": route["kind"], "emp": person["emp_id"],
                                "reason": route["reason"]})[:16]
        wanted[identity] = {"id": identity, "month": month, "emp_id": person["emp_id"], **route}
    current = {item["id"] for item in open_items(home, month)}
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for identity in sorted(wanted.keys() - current):
        append_jsonl(path(home), {"event": "open", "at": now, **wanted[identity]})
    for identity in sorted(current - wanted.keys()):
        append_jsonl(path(home), {"event": "close", "at": now, "id": identity, "month": month})


def open_items(home: Path, month: str) -> list[dict]:
    items: dict[str, dict] = {}
    for event in read_jsonl(path(home)):
        if event["month"] != month:
            continue
        if event["event"] == "open":
            items[event["id"]] = event
        else:
            items.pop(event["id"], None)
    return list(items.values())


def closed_count(home: Path, month: str) -> int:
    return sum(1 for event in read_jsonl(path(home)) if event["month"] == month and event["event"] == "close")


def addressed_to(item: dict, user: dict) -> bool:
    to = item["to"]
    return to["role"] == user["role"] and to.get("user_id") in (None, user["id"])


def find(home: Path, month: str, identity: str) -> dict | None:
    return next((item for item in open_items(home, month) if item["id"] == identity), None)


def mine(home: Path, month: str, user: dict) -> list[dict]:
    return [item for item in open_items(home, month) if addressed_to(item, user)]


def bundles(items: list[dict], run: dict) -> list[dict]:
    """宛先ごと、用件ごとに束ねる。画面に出す言葉だけを作る。"""
    people = {person["emp_id"]: person for person in run["people"]}
    grouped: dict[tuple, list[dict]] = {}
    for item in items:
        grouped.setdefault((item["kind"], item.get("file")), []).append(item)
    result = []
    for (kind, file), members in sorted(grouped.items(), key=lambda pair: (pair[0][0], pair[0][1] or "")):
        rows = [{"item_id": item["id"], "emp_id": item["emp_id"], "name": people[item["emp_id"]]["name"],
                 "group": people[item["emp_id"]]["group"], "job": people[item["emp_id"]]["job"],
                 "options": people[item["emp_id"]]["options"], "reason": item["reason"],
                 "history": people[item["emp_id"]]["history"]} for item in members]
        title = {FILE: f"{file}のファイルを渡してください", RANK: "考課を答えてください",
                 JUDGE: "どの職場で数えるかを決めてください"}[kind]
        how = {FILE: "ファイルを選ぶと、kounin が検査して差を見せます。",
               RANK: "評点はあなたが決めます。kounin は埋めません。",
               JUDGE: "押した判定はこの期のこの人にだけ効きます。"}[kind]
        result.append({"kind": kind, "file": file, "title": title, "how": how, "count": len(rows),
                       "people": sorted(rows, key=lambda row: row["emp_id"])})
    return result
