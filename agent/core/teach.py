"""教える。承認済みのパックと手順表と今月の判定だけを読む。教わっていないことは「まだ教わっていません」と答える（AC9〜AC12）。

ここは Claude を使わない偽物の答えである。Claude を使う時も、読めるものはこの範囲に限る。
"""

import re
from pathlib import Path

from agent.core import month as months
from agent.core import pack as packs
from engine.procedure import BONUS_STEPS, RAISE_STEPS

NOT_TAUGHT = "まだ教わっていません。前任か人事担当に確かめてください。"
PERSON_ID = re.compile(r"\d{6}")


def _person(text: str, run: dict | None) -> dict | None:
    if run is None:
        return None
    match = PERSON_ID.search(text)
    for person in run["people"]:
        if (match and person["emp_id"] == match.group()) or person["name"] in text \
                or person["name"].replace(" ", "") in text:
            return person
    return None


def _why_stopped(person: dict) -> dict:
    if person["category"] in ("通常ルール適用", "例外適用"):
        rule = f"（{'、'.join(person['rule_ids'])}）" if person["rule_ids"] else ""
        return {"text": f"{person['name']}さんは計算できています。{person['category']}{rule}です。",
                "sources": ["今月の判定"]}
    detail = "、".join(person["gaps"] + person["failed"]) or person["reason"]
    return {"text": f"{person['name']}さんは確認が必要です（{person['category']}）。足りないもの：{detail}。",
            "sources": ["今月の判定"]}


def answer(home: Path, month: str, text: str, can_see_record: bool) -> dict:
    run = months.latest(home, month)
    pack = packs.for_date(home, months.base_date(month))
    person = _person(text, run)
    if person and re.search(r"金額|いくら|なぜこの", text):
        steps = "、".join(step.split(" = ")[0].split("：")[0] for step in BONUS_STEPS[:9])
        reply = {"text": f"金額は手順どおりに計算しています：{steps}。金額と中間値は計算の記録に出します。",
                 "sources": ["計算の手順"]}
        if can_see_record:
            reply["record"] = {"name": person["name"], "trace": person["trace"]}
        return reply
    if person:
        return _why_stopped(person)
    if "賞与" in text and re.search(r"計算|どう|手順", text):
        return {"text": "賞与は次の順で計算します。", "steps": list(BONUS_STEPS), "sources": ["計算の手順"]}
    if "昇給" in text:
        return {"text": "定期昇給は次の順で計算します。", "steps": list(RAISE_STEPS), "sources": ["計算の手順"]}
    words = re.findall(r"[一-龥ァ-ヶー]{2,}", text)
    for rule in pack.rules:
        if any(word in rule.text for word in words if word not in ("賞与", "計算", "職員")):
            return {"text": rule.text, "sources": [f"ルール {rule.id}"]}
    return {"text": NOT_TAUGHT, "sources": [], "stopped": True}
