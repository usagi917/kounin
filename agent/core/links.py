"""一時URL。ログインしない人へ、発行の時に固定した質問だけを届ける（AC31、INV24）。

URL は本人確認ではない。持っている人に、固定した質問への回答だけを許す鍵である。
生のトークンは保存せず、ハッシュだけを持つ。検査から保存と再計算までを1つの排他で行う。
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent.core import audit, decisions, inbox, ingest, month as months
from agent.core import pack as packs
from agent.core.workspace import append_jsonl, locked, read_jsonl, users

LIFETIME = timedelta(hours=72)
MAX_QUESTIONS, MAX_QUESTION, MAX_ANSWER = 10, 300, 2000
HR, CHAIR = "人事担当", "理事長"
WAITING, ANSWERED, EXPIRED, REVOKED = "回答待ち", "回答済み", "期限切れ", "取り消し"
UNUSABLE = "このURLは使えません。人事担当に新しいURLをもらってください。"


class LinkError(ValueError):
    """質問に答えられない。理由を日本語の文にする。"""


class LinkClosed(LinkError):
    """URL そのものが使えない。"""


class LinkDone(LinkClosed):
    """全部に答えが届いて閉じた。"""


def path(home: Path) -> Path:
    return home / "var" / "links.jsonl"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _stamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def fold(home: Path) -> dict[str, dict]:
    """追記の記録から今の状態を作る。"""
    items: dict[str, dict] = {}
    for event in read_jsonl(path(home)):
        if event["event"] == "issue":
            items[event["id"]] = {**event, "answers": {}, "revoked_at": None}
        elif event["event"] == "answer":
            items[event["id"]]["answers"][str(event["index"])] = {"text": event["text"], "at": event["at"]}
        else:
            items[event["id"]]["revoked_at"] = event["at"]
    return items


def contacts(home: Path) -> list[dict]:
    return [user for user in users(home) if user["role"] != HR]


def _contact(home: Path, user_id: str) -> dict:
    user = next((item for item in contacts(home) if item["id"] == user_id), None)
    if user is None:
        raise LinkError("その人へは URL を作れません")
    return user


def _actor(user: dict) -> dict:
    """履歴では、ログインした本人ではなく URL の持ち主の操作だと分かるようにする。"""
    return {**user, "role": f"{user['role']}・一時URL"}


def _clean(questions: object) -> list[str]:
    if not isinstance(questions, list):
        raise LinkError("質問が読めません")
    rows = [text for text in (str(item).strip() for item in questions) if text]
    if len(rows) > MAX_QUESTIONS:
        raise LinkError(f"質問は{MAX_QUESTIONS}問までです")
    if any(len(text) > MAX_QUESTION for text in rows):
        raise LinkError(f"質問は1問{MAX_QUESTION}文字までです")
    return rows


def _decidable_run(home: Path, month: str) -> str | None:
    """決められる3案があれば、その結果の識別子を返す。"""
    data = decisions.current_plans(home, month)
    if data is None or months.is_stale(home, month):
        return None
    record = decisions.decision(home, month)
    if record and record["run_id"] == data["run_id"]:
        return None
    return data["run_id"]


def _live(link: dict, home: Path, now: datetime) -> bool:
    return status(home, link, now) == WAITING


def issue(home: Path, month: str, to: str, questions: object, actor: dict,
          now: datetime | None = None) -> tuple[str, dict]:
    """質問を固定して URL を作る。同じ人の前の URL は取り消す。"""
    moment = _now(now)
    with locked(home):
        user = _contact(home, to)
        asked = [item["id"] for item in inbox.open_items(home, month)
                 if item["kind"] in (inbox.RANK, inbox.JUDGE) and inbox.addressed_to(item, user)]
        run_id = _decidable_run(home, month) if user["role"] == CHAIR else None
        rows = _clean(questions)
        if not asked and not rows and run_id is None:
            raise LinkError("聞くことがありません。質問を書いてください")
        for link in fold(home).values():
            if link["to"] == to and link["month"] == month and _live(link, home, moment):
                append_jsonl(path(home), {"event": "revoke", "id": link["id"], "at": _stamp(moment)})
        token = secrets.token_urlsafe(32)
        record = {"event": "issue", "id": digest(token), "month": month, "to": to, "items": asked, "questions": rows,
                  "run_id": run_id, "by": actor["name"], "at": _stamp(moment),
                  "expires_at": _stamp(moment + LIFETIME)}
        append_jsonl(path(home), record)
        audit.record(home, actor, "一時URLの発行", f"{month} {user['role']} {len(asked) + len(rows)}問",
                     "発行した", "案の決定を含む" if run_id else "")
        return token, record


def revoke(home: Path, month: str, identity: str, actor: dict) -> None:
    with locked(home):
        link = fold(home).get(identity)
        if link is None or link["month"] != month:
            raise LinkError("その URL はありません")
        if not link["revoked_at"]:
            append_jsonl(path(home), {"event": "revoke", "id": identity, "at": _stamp(_now(None))})
            audit.record(home, actor, "一時URLの取り消し", month, "取り消した")


def _decided(home: Path, link: dict) -> bool:
    record = decisions.decision(home, link["month"])
    return bool(link["run_id"] and record and record["run_id"] == link["run_id"])


def progress(home: Path, link: dict) -> tuple[int, int]:
    """答えの届いた数と、聞いている数を返す。解けて閉じた連絡は答えが届いたものとして数える。"""
    still_open = {item["id"] for item in inbox.open_items(home, link["month"])}
    done = sum(1 for item in link["items"] if item not in still_open) + len(link["answers"])
    total = len(link["items"]) + len(link["questions"])
    if link["run_id"]:
        return done + _decided(home, link), total + 1
    return done, total


def status(home: Path, link: dict, now: datetime | None = None) -> str:
    done, total = progress(home, link)
    if link["revoked_at"]:
        return REVOKED
    if done >= total:
        return ANSWERED
    return EXPIRED if _now(now) >= datetime.fromisoformat(link["expires_at"]) else WAITING


def open_link(home: Path, token: str, month: str, now: datetime | None = None) -> dict:
    """使える URL の記録を返す。読むだけでは何も消費しない。"""
    link = fold(home).get(digest(token))
    if link is None or link["month"] != month:
        raise LinkClosed(UNUSABLE)
    state = status(home, link, now)
    if state == ANSWERED:
        raise LinkDone("ありがとうございました。")
    if state == EXPIRED:
        raise LinkClosed("このURLは期限が切れました。人事担当に新しいURLをもらってください。")
    if state == REVOKED:
        raise LinkClosed(UNUSABLE)
    return link


def view(home: Path, token: str, month: str) -> dict:
    """URL の持ち主に見せる中身。固定した質問のほかは返さない。"""
    link = open_link(home, token, month)
    user = _contact(home, link["to"])
    run = months.latest(home, month)
    items = [item for item in inbox.open_items(home, month) if item["id"] in link["items"]]
    done, total = progress(home, link)
    decide = None
    if link["run_id"] and not _decided(home, link):
        data = decisions.current_plans(home, month)
        same = bool(data) and data["run_id"] == link["run_id"] and not months.is_stale(home, month)
        decide = {"changed": not same, "plans": decisions.summaries(data) if same else None}
    return {
        "month": month, "name": user["name"], "role": user["role"], "by": link["by"],
        "expires_at": link["expires_at"], "done": done, "total": total, "bundles": inbox.bundles(items, run) if items and run else [],
        "ranks": packs.for_date(home, months.base_date(month)).ranks,
        "questions": [{"index": index, "text": text, "answered": str(index) in link["answers"]}
                      for index, text in enumerate(link["questions"])],
        "decide": decide,
    }


def _item(home: Path, link: dict, item_id: object, kind: str, user: dict) -> dict:
    if item_id not in link["items"]:
        raise LinkError("この質問はこのURLに入っていません")
    item = inbox.find(home, link["month"], item_id)
    if item is None:
        raise LinkError("この質問には、もう答えが届いています")
    if item["kind"] != kind or not inbox.addressed_to(item, user):
        raise LinkError("この質問はこのURLに入っていません")
    return item


def answer_rank(home: Path, token: str, month: str, item_id: object, rank: object) -> dict:
    with locked(home):
        link = open_link(home, token, month)
        user = _contact(home, link["to"])
        item = _item(home, link, item_id, inbox.RANK, user)
        if rank not in packs.for_date(home, months.base_date(month)).ranks:
            raise LinkError("ランクを選んでください")
        ingest.answer_rank(home, month, item["emp_id"], rank, _actor(user))
        return months.run(home, month, None)[0]


def answer_judge(home: Path, token: str, month: str, item_id: object, group: object) -> dict:
    with locked(home):
        link = open_link(home, token, month)
        user = _contact(home, link["to"])
        item = _item(home, link, item_id, inbox.JUDGE, user)
        person = next(row for row in months.latest(home, month)["people"] if row["emp_id"] == item["emp_id"])
        if group not in person["options"]:
            raise LinkError("職場を選んでください")
        ingest.answer_judgment(home, month, item["emp_id"], group, _actor(user))
        return months.run(home, month, None)[0]


def answer_text(home: Path, token: str, month: str, index: object, text: object) -> None:
    with locked(home):
        link = open_link(home, token, month)
        user = _contact(home, link["to"])
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(link["questions"]):
            raise LinkError("この質問はこのURLに入っていません")
        if str(index) in link["answers"]:
            raise LinkError("この質問には、もう答えが届いています")
        body = str(text or "").strip()
        if not body:
            raise LinkError("答えを書いてください")
        if len(body) > MAX_ANSWER:
            raise LinkError(f"答えは{MAX_ANSWER}文字までです")
        append_jsonl(path(home), {"event": "answer", "id": link["id"], "index": index, "text": body,
                                  "at": _stamp(_now(None))})
        audit.record(home, _actor(user), "面談の回答", f"{month} 1問", "答えが届いた")


def decide(home: Path, token: str, month: str, plan_id: object) -> dict:
    with locked(home):
        link = open_link(home, token, month)
        if not link["run_id"] or _decided(home, link):
            raise LinkError("このURLでは案を決められません")
        user = _contact(home, link["to"])
        return decisions.decide(home, month, str(plan_id), _actor(user), expected_run=link["run_id"])


def _summary(home: Path, link: dict, now: datetime) -> dict:
    done, total = progress(home, link)
    return {"id": link["id"], "status": status(home, link, now), "expires_at": link["expires_at"],
            "done": done, "total": total}


def board(home: Path, month: str, run: dict | None, now: datetime | None = None) -> dict:
    """人事担当の画面に出す、誰に何を聞くかと、届いた答え。"""
    moment = _now(now)
    items = fold(home)
    mine = [link for link in items.values() if link["month"] == month]
    people = {user["id"]: user for user in users(home)}
    rows = []
    for user in contacts(home):
        waiting = [item for item in inbox.open_items(home, month)
                   if item["kind"] != inbox.FILE and inbox.addressed_to(item, user)]
        latest = next((link for link in reversed(mine) if link["to"] == user["id"] and not link["revoked_at"]), None)
        rows.append({"id": user["id"], "name": user["name"], "role": user["role"],
                     "bundles": inbox.bundles(waiting, run) if waiting and run else [],
                     "link": _summary(home, latest, moment) if latest else None})
    answers = [{"who": people[link["to"]]["name"], "role": people[link["to"]]["role"],
                "question": link["questions"][int(index)], "text": value["text"], "at": value["at"]}
               for link in mine for index, value in link["answers"].items()]
    return {"contacts": rows, "answers": sorted(answers, key=lambda row: row["at"], reverse=True)}
