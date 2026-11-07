"""面談の質問の下書き。Claude が考え、人が直して足し、URL を作る時に人が確定する（INV7）。"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from agent.core import audit, outbound
from agent.core.workspace import locked, read_json, write_json

MAX_PER_PERSON = 3
MAX_LENGTH = 80
TIMEOUT = 60.0      # 秒。答えがなくても人が書けるので、長く待たない
PURPOSE = "面談の質問の下書き"
MONEY = re.compile(r"[0-9０-９][0-9０-９,，]*\s*(円|万)|円")

Ask = Callable[[dict], Awaitable[dict]]


def _path(home: Path, month: str) -> Path:
    return home / "var" / "questions" / f"{month}.json"


def cached(home: Path, month: str) -> dict[str, list[str]] | None:
    path = _path(home, month)
    return read_json(path) if path.exists() else None


def _usable(text: object, names: list[str]) -> bool:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_LENGTH:
        return False
    flat = outbound.squeeze(text)
    return not MONEY.search(text) and not any(name in flat for name in names)


def clean(answer: dict, refs: dict[str, str], names: list[str]) -> dict[str, list[str]]:
    """渡した宛先の分だけを受け、名前や金額の入った文は捨てる。"""
    drafts = {}
    for row in answer.get("people", []):
        user_id = refs.get(row.get("ref"))
        rows = [text.strip() for text in row.get("questions", []) if _usable(text, names)][:MAX_PER_PERSON]
        if user_id and rows:
            drafts[user_id] = rows
    return drafts


async def draft(home: Path, month: str, actor: dict, ask: Ask) -> dict:
    """控えがあれば Claude を呼ばない。Claude が使えなくても、人が書けばよいので止めない。"""
    saved = cached(home, month)
    if saved is not None:
        return {"drafts": saved, "source": "claude"}
    if not outbound.allowed(home):
        return {"drafts": {}, "source": "off"}
    payload, refs = outbound.question_payload(home, month)
    with locked(home):
        outbound.record(home, PURPOSE, payload)
    try:
        answer = await asyncio.wait_for(ask(payload), TIMEOUT)
        drafts = clean(answer, refs, outbound.names(home, month))
    except Exception:     # 下書きがなくても人が書ける。失敗の理由で業務を止めない
        return {"drafts": {}, "source": "none"}
    if not drafts:
        return {"drafts": {}, "source": "none"}
    with locked(home):
        write_json(_path(home, month), drafts)
        audit.record(home, actor, "質問の下書き", month, f"{len(drafts)}人分を kounin が下書きした")
    return {"drafts": drafts, "source": "claude"}
