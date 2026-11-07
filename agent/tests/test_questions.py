"""面談の質問の下書き（Claude へ渡す中身、検査、控え）。Claude は偽物に置き換える。"""

import asyncio
from pathlib import Path

import pytest
import yaml

from agent.core import audit, demo, links, month as months, outbound, questions, workspace
from agent.core.workspace import read_jsonl

MONTH = "2026-10"
HR = {"id": "hr", "name": "人事", "role": "人事担当"}


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    workspace.init_sample(path)
    months.run(path, MONTH, HR)
    return path


def fake(answer):
    calls = []

    async def ask(payload):
        calls.append(payload)
        return answer(payload) if callable(answer) else answer
    ask.calls = calls
    return ask


def same_for_all(payload):
    return {"people": [{"ref": row["ref"], "questions": ["今期、負担が増えた職場はありますか"]} for row in payload["people"]]}


def draft(home, ask):
    return asyncio.run(questions.draft(home, MONTH, HR, ask))


def test_Claudeへ渡す中身に氏名と金額と職員番号がない(home):
    payload, _ = outbound.question_payload(home, MONTH)
    text = str(payload)
    people = links.board(home, MONTH, months.latest(home, MONTH))["contacts"]
    assert not any(row["name"] in text or row["id"] in text for row in people)
    run = months.latest(home, MONTH)
    assert not any(person["name"] in text or person["emp_id"] in text for person in run["people"])
    assert "円" not in text
    assert set(payload) == {"period", "people"}
    assert all(set(row) == {"ref", "role", "groups", "asks"} for row in payload["people"])


def test_下書きは人ごとに返り2回目はClaudeを呼ばない(home):
    ask = fake(same_for_all)
    first = draft(home, ask)
    assert first["source"] == "claude"
    ids = {row["id"] for row in links.contacts(home)}
    assert set(first["drafts"]) == ids
    second = draft(home, ask)
    assert second["drafts"] == first["drafts"] and len(ask.calls) == 1
    assert questions.cached(home, MONTH) == first["drafts"]


def test_送った中身と下書きの事実を残す(home):
    draft(home, fake(same_for_all))
    logged = read_jsonl(outbound.path(home))
    assert logged[-1]["purpose"] == "面談の質問の下書き" and logged[-1]["payload"]["people"]
    assert audit.entries(home)[0]["action"] == "質問の下書き"


@pytest.mark.parametrize("bad", [
    "雨宮 文さんの評価はどうですか",
    "雨宮　文さんの評価はどうですか",
    "賞与は500,000円で足りますか",
    "x" * (questions.MAX_LENGTH + 1),
    "",
    123,
])
def test_おかしな質問は捨てる(home, bad):
    def answer(payload):
        return {"people": [{"ref": row["ref"], "questions": [bad, "異動の予定はありますか"]} for row in payload["people"]]}
    drafts = draft(home, fake(answer))["drafts"]
    assert all(rows == ["異動の予定はありますか"] for rows in drafts.values())


def test_利用者でない職員の氏名の質問も捨てる(home):
    built = months.assemble(home, MONTH, months.latest(home, MONTH)["input_version"])
    name = built["rows"][0]["emp"].display_name

    def answer(payload):
        return {"people": [{"ref": row["ref"], "questions": [f"{name.replace(' ', '　')}さんはどうですか", "異動の予定はありますか"]} for row in payload["people"]]}
    drafts = draft(home, fake(answer))["drafts"]
    assert all(rows == ["異動の予定はありますか"] for rows in drafts.values())


def test_Claudeが答えなくても時間で切り上げて次を受ける(home, monkeypatch):
    monkeypatch.setattr(questions, "TIMEOUT", 0.01)

    async def silent(payload):
        await asyncio.sleep(30)
    assert draft(home, silent) == {"drafts": {}, "source": "none"}
    assert draft(home, fake(same_for_all))["source"] == "claude"


def test_質問は1人3問までにする(home):
    def answer(payload):
        return {"people": [{"ref": row["ref"], "questions": [f"質問その{'一二三四五'[i]}ですか" for i in range(5)]} for row in payload["people"]]}
    drafts = draft(home, fake(answer))["drafts"]
    assert all(len(rows) == questions.MAX_PER_PERSON for rows in drafts.values())


def test_知らない宛先の答えは使わない(home):
    drafts = draft(home, fake({"people": [{"ref": "誰か", "questions": ["異動の予定はありますか"]}]}))
    assert drafts == {"drafts": {}, "source": "none"}


def test_Claudeが失敗しても止まらず控えも残さない(home):
    async def broken(payload):
        raise RuntimeError("つながらない")
    assert draft(home, broken) == {"drafts": {}, "source": "none"}
    assert questions.cached(home, MONTH) is None


def test_手元環境はclaude_allowedがfalseならClaudeを呼ばない(home):
    path = home / "config.yaml"
    config = yaml.safe_load(path.read_text("utf-8"))
    path.write_text(yaml.safe_dump({**config, "environment": "手元環境", "claude_allowed": False}, allow_unicode=True), "utf-8")
    ask = fake(same_for_all)
    assert draft(home, ask) == {"drafts": {}, "source": "off"}
    assert ask.calls == [] and not outbound.path(home).exists()


def test_架空環境はclaude_allowedを変えずに使える(home):
    assert workspace.config(home)["claude_allowed"] is False
    assert outbound.allowed(home) is True


def test_デモは確認を全部終えて3案の前で待つ(tmp_path):
    home = tmp_path / "demo"
    demo.ready(home)
    run = months.latest(home, MONTH)
    assert run["stopped"] == 0 and not months.is_stale(home, MONTH)
    actions = [row for row in audit.entries(home)]
    assert any("一時URL" in row["role"] for row in actions)
    assert not (home / "var" / "decisions").exists()
