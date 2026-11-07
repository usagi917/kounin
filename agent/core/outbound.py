"""Claude へ渡す中身を1か所で作る（INV2）。氏名、金額、職員番号はここを通さない。"""

import re
from datetime import UTC, datetime
from pathlib import Path

from agent.core import inbox, links, month as months
from agent.core.workspace import SAMPLE_ENVIRONMENT, append_jsonl, config, users

ASKS = {inbox.RANK: "考課の入力", inbox.JUDGE: "数える職場の判断"}


def path(home: Path) -> Path:
    return home / "var" / "outbound.jsonl"


def allowed(home: Path) -> bool:
    """架空環境は使う。手元環境は claude_allowed が true の時だけ使う（SPEC §12）。"""
    settings = config(home)
    return settings.get("environment") == SAMPLE_ENVIRONMENT or settings.get("claude_allowed") is True


def question_payload(home: Path, month: str) -> tuple[dict, dict[str, str]]:
    """役割、担当の職場、頼む仕事の種類と人数だけを渡す。宛先は仮の記号にし、対応は手元に残す。"""
    run = months.latest(home, month)
    items = inbox.open_items(home, month)
    people, refs = [], {}
    for number, user in enumerate(links.contacts(home), start=1):
        ref = f"p{number}"
        refs[ref] = user["id"]
        waiting = [item["kind"] for item in items if item["kind"] != inbox.FILE and inbox.addressed_to(item, user)]
        people.append({"ref": ref, "role": user["role"], "groups": list(user.get("groups", [])),
                       "asks": [{"kind": label, "count": waiting.count(kind)}
                                for kind, label in ASKS.items() if waiting.count(kind)]})
    return {"period": run["period_name"] if run else month, "people": people}, refs


def squeeze(text: str) -> str:
    return re.sub(r"\s", "", text)


def names(home: Path, month: str) -> list[str]:
    """質問の文に入ってはいけない名前。利用者と計算の対象の職員の、氏名と2字以上の姓名を見る。"""
    people = [user["name"] for user in users(home)]
    run = months.latest(home, month)
    if run:
        people += [row["emp"].display_name for row in months.assemble(home, month, run["input_version"])["rows"]]
    found = set()
    for name in people:
        found.add(squeeze(name))
        found.update(part for part in name.split() if len(part) > 1)
    return sorted(found)


def record(home: Path, purpose: str, payload: dict) -> None:
    append_jsonl(path(home), {"at": datetime.now(UTC).isoformat(timespec="seconds"), "purpose": purpose, "payload": payload})
