"""履歴。人の操作、拒否、Claude の依頼を、利用者、役割、対象の版、結果つきで残す（AC25）。氏名と金額は書かない。"""

from datetime import UTC, datetime
from pathlib import Path

from agent.core.workspace import append_jsonl, read_jsonl


def path(home: Path) -> Path:
    return home / "var" / "audit.jsonl"


def record(home: Path, actor: dict | None, action: str, target: str, result: str, version: str = "") -> None:
    append_jsonl(path(home), {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "user": actor["name"] if actor else "kounin",
        "role": actor["role"] if actor else "kounin",
        "action": action, "target": target, "version": version, "result": result,
    })


def entries(home: Path) -> list[dict]:
    return list(reversed(read_jsonl(path(home))))
