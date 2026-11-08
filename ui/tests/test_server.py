"""画面の API。役割、CSRF、Host、宛先の検査を確かめる（INV20、AC6、AC21）。"""

import base64
import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent.core import demo, inbox, month as months, workspace
from ui.auth import Attempts, hash_password, verify_password
from ui.server import create_app

MONTH = "2026-10"


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    workspace.init_sample(path)
    demo.load_sample(path)
    return path


@pytest.fixture
def app(home):
    return create_app(home)


def client(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8020")


CREDENTIALS = {"login_id": "amamiya", "password": "kounin-2026"}
FIXES = workspace.ROOT / "data" / "sample" / "fixes"


def login(app) -> tuple[TestClient, dict]:
    session = client(app)
    assert session.post("/api/login", json=CREDENTIALS).status_code == 200
    csrf = session.get("/api/session").json()["csrf"]
    return session, {"X-CSRF-Token": csrf}


def test_ログインなしは入れない(app):
    assert client(app).get("/api/today").status_code == 401


def test_loopback以外のHostを拒否する(app):
    session = TestClient(app, base_url="http://evil.example:8020")
    assert session.get("/api/session").status_code == 403


def test_別のOriginからのログインを拒否する(app):
    response = client(app).post("/api/login", json=CREDENTIALS, headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_CSRFのトークンがない更新を拒否する(app):
    session, _ = login(app)
    assert session.post("/api/run").status_code == 403


def test_パスワードが違うと入れない(app):
    wrong = client(app).post("/api/login", json={**CREDENTIALS, "password": "x"})
    missing = client(app).post("/api/login", json={"login_id": "nobody", "password": "x"})
    assert wrong.status_code == missing.status_code == 400
    assert wrong.json() == missing.json()


@pytest.mark.parametrize("body", [{"user_id": "hr"}, {"user_id": "boss-a"}, {"login_id": "", "password": ""}])
def test_利用者を選ぶだけでは入れない(app, body):
    assert client(app).post("/api/login", json=body).status_code == 400


def test_失敗が続くと正しいパスワードでも締め出す(app):
    session = client(app)
    for _ in range(5):
        assert session.post("/api/login", json={**CREDENTIALS, "password": "x"}).status_code == 400
    assert session.post("/api/login", json=CREDENTIALS).status_code == 429


def test_パスワードの情報を画面へ返さない(app):
    text = client(app).get("/api/session").text
    assert "password" not in text and "amamiya" not in text and "users" not in text


def test_パスワードのハッシュを照合できる():
    stored = hash_password("正しい")
    assert verify_password("正しい", stored) and not verify_password("違う", stored)
    assert not verify_password("kounin", None)


def test_締め出しは時間が過ぎると解ける():
    attempts = Attempts()
    for _ in range(5):
        attempts.fail("a", now=100.0)
    assert attempts.locked("a", now=100.0 + 299) and not attempts.locked("a", now=100.0 + 300)


def test_今の工程と職場の内訳を返す(app):
    hr, hr_h = login(app)
    assert hr.get("/api/pulse").json()["stage"] == 0
    assert hr.get("/api/today").json()["headcount"] == 550
    hr.post("/api/run", headers=hr_h)
    assert hr.get("/api/pulse").json()["stage"] == 1
    groups = hr.get("/api/today").json()["groups"]
    assert sum(row["total"] for row in groups) == 550 and sum(row["waiting"] for row in groups) == 13
    assert set(groups[0]) == {"name", "total", "waiting"}


def test_答えが届くと工程の印が変わる(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    before = hr.get("/api/pulse").json()["signature"]
    link = holder(app, issue(hr, hr_h, "boss-c", ("質問",)))
    after_issue = hr.get("/api/pulse").json()["signature"]
    link.post("/api/link/answer", json={"index": 0, "text": "はい"})
    assert len({before, after_issue, hr.get("/api/pulse").json()["signature"]}) == 3


def test_工程は決定と出力で進む(app):
    hr, hr_h = login(app)
    solve_to_plans(app, hr, hr_h)
    assert hr.get("/api/pulse").json()["stage"] == 3
    holder(app, issue(hr, hr_h, "chair")).post("/api/link/decide", json={"plan_id": "A"})
    assert hr.get("/api/pulse").json()["stage"] == 4
    hr.post("/api/pay/export", headers=hr_h)
    assert hr.get("/api/pulse").json()["stage"] == 5


def holder(app, token: str) -> TestClient:
    session = client(app)
    session.headers["X-Link-Token"] = token
    return session


def issue(hr, headers, to: str, questions: tuple[str, ...] = ()) -> str:
    response = hr.post("/api/links", json={"to": to, "questions": list(questions)}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["token"]


def people_of(view: dict) -> list[dict]:
    return [person for bundle in view["bundles"] for person in bundle["people"]]


def upload(hr, headers, name: str = "所属.csv") -> dict:
    return hr.post("/api/files/check?kind=所属", content=(FIXES / name).read_bytes(), headers=headers).json()


def solve_to_plans(app, hr, hr_h) -> None:
    """一時URLで考課と判定を全部答え、3案まで作る。"""
    hr.post("/api/run", headers=hr_h)
    checked = upload(hr, hr_h)
    hr.post("/api/files/confirm", json={"kind": "所属", "token": checked["token"]}, headers=hr_h)
    for boss in ("boss-a", "boss-b"):
        link = holder(app, issue(hr, hr_h, boss))
        for person in people_of(link.get("/api/link").json()):
            assert link.post("/api/link/rank", json={"item_id": person["item_id"], "rank": "B"}).status_code == 200
    link = holder(app, issue(hr, hr_h, "predecessor"))
    for person in people_of(link.get("/api/link").json()):
        result = link.post("/api/link/judge", json={"item_id": person["item_id"], "group": person["options"][0]})
        assert result.status_code == 200
    assert hr.post("/api/pay/plans", headers=hr_h).status_code == 200


def test_一周の操作が画面のAPIで通る(app, home):
    hr, hr_h = login(app)
    run = hr.post("/api/run", headers=hr_h).json()["run"]
    assert run["stopped"] == 13

    checked = upload(hr, hr_h, "所属-誤りの見本.csv")
    assert not checked["ok"] and len(checked["errors"]) == 2
    checked = upload(hr, hr_h)
    assert checked["ok"] and checked["diff"]["changed"] == 3
    assert hr.post("/api/files/confirm", json={"kind": "所属", "token": checked["token"]},
                   headers=hr_h).json()["stopped"] == 10

    boss = holder(app, issue(hr, hr_h, "boss-a"))
    other = holder(app, issue(hr, hr_h, "boss-b"))
    people = people_of(boss.get("/api/link").json())
    assert len(people) == 3
    denied = other.post("/api/link/rank", json={"item_id": people[0]["item_id"], "rank": "B"})
    assert denied.status_code == 400
    for person in people:
        assert boss.post("/api/link/rank", json={"item_id": person["item_id"], "rank": "B"}).status_code == 200
    for person in people_of(other.get("/api/link").json()):
        other.post("/api/link/rank", json={"item_id": person["item_id"], "rank": "A"})

    pre = holder(app, issue(hr, hr_h, "predecessor"))
    for person in people_of(pre.get("/api/link").json()):
        result = pre.post("/api/link/judge", json={"item_id": person["item_id"], "group": person["options"][0]}).json()
    assert result["stopped"] == 0
    assert inbox.open_items(home, MONTH) == []

    assert hr.post("/api/pay/plans", headers=hr_h).status_code == 200
    chair = holder(app, issue(hr, hr_h, "chair"))
    view = chair.get("/api/link").json()
    assert [plan["id"] for plan in view["decide"]["plans"]] == ["A", "B", "C"]
    assert "rows" not in view["decide"]["plans"][0] and "result_hash" not in view["decide"]["plans"][0]
    assert chair.post("/api/link/decide", json={"plan_id": "B"}).status_code == 200
    assert hr.post("/api/pay/decide", json={"plan_id": "A"}, headers=hr_h).status_code == 404
    assert chair.post("/api/pay/export").status_code == 401
    response = hr.post("/api/pay/export", headers=hr_h)
    assert response.status_code == 200 and "text/csv" in response.headers["content-type"]
    assert response.content.decode("utf-8-sig").count("\r\n") == 551
    assert hr.get("/api/pay").json()["decision"]["expired"] is False
    assert hr.get("/api/pay").json()["decision"]["by"] == "楠見 悠"


def test_一時URLでは人事のAPIを呼べない(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    link = holder(app, issue(hr, hr_h, "boss-a"))
    for path in ("/api/today", "/api/inbox", "/api/history", "/api/pay", "/api/changes"):
        assert link.get(path).status_code == 401
    assert link.post("/api/run").status_code == 401
    assert link.post("/api/links", json={"to": "boss-b"}).status_code == 401


def test_ログインした人事でもトークンなしでは一時URLの操作をできない(app):
    hr, hr_h = login(app)
    assert hr.get("/api/link").status_code == 410
    assert hr.post("/api/link/rank", json={"item_id": "x", "rank": "B"}, headers=hr_h).status_code == 410


def test_一時URLの発行は人事だけでCSRFが要る(app):
    assert client(app).post("/api/links", json={"to": "boss-a"}).status_code == 401
    hr, _ = login(app)
    assert hr.post("/api/links", json={"to": "boss-a"}).status_code == 403


def test_人事自身と知らない人へは発行しない(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    for to in ("hr", "nobody"):
        assert hr.post("/api/links", json={"to": to, "questions": ["質問"]}, headers=hr_h).status_code == 400


def test_聞くことがなければ発行しない(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    assert hr.post("/api/links", json={"to": "chair"}, headers=hr_h).status_code == 400


def test_GETでは使い切らず何度でも開ける(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    link = holder(app, issue(hr, hr_h, "boss-a"))
    assert link.get("/api/link").json() == link.get("/api/link").json()


def test_取り消しと作り直しで前のURLは使えない(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    first = holder(app, issue(hr, hr_h, "boss-a"))
    second = holder(app, issue(hr, hr_h, "boss-a"))
    assert first.get("/api/link").status_code == 410
    assert second.get("/api/link").status_code == 200
    contact = next(row for row in hr.get("/api/inbox").json()["contacts"] if row["id"] == "boss-a")
    assert hr.post("/api/links/revoke", json={"id": contact["link"]["id"]}, headers=hr_h).status_code == 200
    assert second.get("/api/link").status_code == 410


def test_答えの値は許された値だけ(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    checked = upload(hr, hr_h)
    hr.post("/api/files/confirm", json={"kind": "所属", "token": checked["token"]}, headers=hr_h)
    boss = holder(app, issue(hr, hr_h, "boss-a"))
    item = people_of(boss.get("/api/link").json())[0]["item_id"]
    assert boss.post("/api/link/rank", json={"item_id": item, "rank": "Z"}).status_code == 400
    assert boss.post("/api/link/rank", json={"item_id": "nothing", "rank": "B"}).status_code == 400
    assert boss.post("/api/link/judge", json={"item_id": item, "group": "東病棟"}).status_code == 400
    assert boss.post("/api/link/decide", json={"plan_id": "A"}).status_code == 400
    pre = holder(app, issue(hr, hr_h, "predecessor"))
    person = people_of(pre.get("/api/link").json())[0]
    assert pre.post("/api/link/judge", json={"item_id": person["item_id"], "group": "医事課"}).status_code == 400


def test_同じ質問に2回は答えられない(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    checked = upload(hr, hr_h)
    hr.post("/api/files/confirm", json={"kind": "所属", "token": checked["token"]}, headers=hr_h)
    boss = holder(app, issue(hr, hr_h, "boss-a"))
    item = people_of(boss.get("/api/link").json())[0]["item_id"]
    assert boss.post("/api/link/rank", json={"item_id": item, "rank": "B"}).status_code == 200
    assert boss.post("/api/link/rank", json={"item_id": item, "rank": "A"}).status_code == 400


def test_面談の質問は自由に書けて答えが人事に届く(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    token = issue(hr, hr_h, "boss-c", ("今期、負担が増えた人はいますか", "<b>来期</b>の心配はありますか"))
    link = holder(app, token)
    view = link.get("/api/link").json()
    assert [row["text"] for row in view["questions"]][1] == "<b>来期</b>の心配はありますか"
    assert link.post("/api/link/answer", json={"index": 0, "text": ""}).status_code == 400
    assert link.post("/api/link/answer", json={"index": 9, "text": "はい"}).status_code == 400
    assert link.post("/api/link/answer", json={"index": 0, "text": "あ" * 2001}).status_code == 400
    assert link.post("/api/link/answer", json={"index": 0, "text": "夜勤が\n増えました"}).status_code == 200
    assert link.post("/api/link/answer", json={"index": 0, "text": "別の答え"}).status_code == 400
    answers = hr.get("/api/inbox").json()["answers"]
    assert answers[0]["text"] == "夜勤が\n増えました" and answers[0]["who"] == "南雲 望"
    assert link.post("/api/link/answer", json={"index": 1, "text": "ありません"}).status_code == 200
    closed = link.get("/api/link")
    assert closed.status_code == 410 and closed.json()["done"] is True


def test_質問の長さと数を制限する(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    too_many = hr.post("/api/links", json={"to": "boss-c", "questions": ["質問"] * 11}, headers=hr_h)
    too_long = hr.post("/api/links", json={"to": "boss-c", "questions": ["あ" * 301]}, headers=hr_h)
    assert too_many.status_code == 400 and too_long.status_code == 400


def test_案が変わると理事長のURLでは決められない(app):
    hr, hr_h = login(app)
    solve_to_plans(app, hr, hr_h)
    chair = holder(app, issue(hr, hr_h, "chair"))
    record = hr.post("/api/changes/propose", json={"text": "配分率を0.55にする", "effective_from": "2026-10-01"},
                     headers=hr_h).json()
    hr.post("/api/changes/approve", json={"id": record["id"]}, headers=hr_h)
    view = chair.get("/api/link").json()
    assert view["decide"]["changed"] is True and view["decide"]["plans"] is None
    assert chair.post("/api/link/decide", json={"plan_id": "A"}).status_code == 400
    assert hr.get("/api/pay").json()["decision"] is None


def test_生のトークンを保存しない(app, home):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    token = issue(hr, hr_h, "boss-a")
    holder(app, token).get("/api/link")
    for name in ("links.jsonl", "audit.jsonl"):
        assert token not in (home / "var" / name).read_text("utf-8")


def test_一時URLの答えは履歴で一時URLと分かる(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    link = holder(app, issue(hr, hr_h, "boss-c", ("質問",)))
    link.post("/api/link/answer", json={"index": 0, "text": "本文の内容"})
    entries = hr.get("/api/history").json()["entries"]
    row = next(item for item in entries if item["action"] == "面談の回答")
    assert row["user"] == "南雲 望" and row["role"] == "上司・一時URL"
    assert "本文の内容" not in row["target"] + row["result"]
    assert any(item["action"] == "一時URLの発行" for item in entries)


def test_使えないURLの更新は履歴に残る(app):
    hr, hr_h = login(app)
    assert holder(app, "wrong").post("/api/link/answer", json={"index": 0, "text": "x"}).status_code == 410
    assert any(item["action"] == "一時URLの拒否" for item in hr.get("/api/history").json()["entries"])


def test_一時URLの画面はキャッシュさせない(app):
    hr, hr_h = login(app)
    hr.post("/api/run", headers=hr_h)
    page = client(app).get("/q")
    assert page.status_code == 200 and page.headers["referrer-policy"] == "no-referrer"
    assert "no-store" in page.headers["cache-control"]
    view = holder(app, issue(hr, hr_h, "boss-a")).get("/api/link")
    assert "no-store" in view.headers["cache-control"]


def test_変更の案と承認(app, home):
    hr, headers = login(app)
    hr.post("/api/run", headers=headers)
    stopped = hr.post("/api/changes/propose", json={"text": "新しい控除を足す", "effective_from": "2026-10-01"},
                      headers=headers).json()
    assert stopped["stop"] == "コードの変更が要ります"
    record = hr.post("/api/changes/propose", json={"text": "配分率を0.55にする", "effective_from": "2026-10-01"},
                     headers=headers).json()
    assert record["effect"]["up"] > 0
    assert hr.post("/api/changes/approve", json={"id": record["id"]}, headers=headers).json()["run"]["pack_version"] == "2"
    assert hr.get("/api/changes").json()["effective_from"] == "2026-10-02"
    assert months.latest(home, MONTH)["pack_version"] == "2"


def test_金額はAPIの聞く本文に入らない(app):
    hr, headers = login(app)
    hr.post("/api/run", headers=headers)
    reply = hr.post("/api/ask", json={"text": "賞与はどう計算するの"}, headers=headers).json()
    assert len(reply["steps"]) == 11


@pytest.mark.skipif(shutil.which("node") is None, reason="node がない")
def test_画面のスクリプトに構文の誤りがない():
    script = Path(__file__).resolve().parents[1] / "static" / "app.js"
    result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_質問の下書きは人事担当だけが頼めて確認の画面に出る(tmp_path):
    home = tmp_path / "home"
    workspace.init_sample(home)
    demo.load_sample(home)

    async def ask(payload):
        return {"people": [{"ref": row["ref"], "questions": ["異動の予定はありますか"]} for row in payload["people"]]}
    app = create_app(home, ask)
    assert client(app).post("/api/questions/draft", headers={"X-CSRF-Token": "x"}).status_code in (401, 403)
    hr, hr_h = login(app)
    assert hr.post("/api/run", headers=hr_h).status_code == 200
    assert hr.get("/api/inbox").json()["drafts"] is None
    result = hr.post("/api/questions/draft", headers=hr_h).json()
    assert result["source"] == "claude" and result["drafts"]["boss-a"] == ["異動の予定はありますか"]
    assert hr.get("/api/inbox").json()["drafts"] == result["drafts"]


def blank(tmp_path):
    home = tmp_path / "blank"
    workspace.init_sample(home)
    return create_app(home)


def encoded(files=None) -> dict:
    return {kind: base64.b64encode(raw).decode() for kind, raw in (files or demo.sample_files()).items()}


def test_データがない時も全部の画面が開ける(tmp_path):
    hr, _ = login(blank(tmp_path))
    data = hr.get("/api/today").json()
    assert data["empty"] is True and data["kinds"] == ["職員", "勤怠", "所属", "考課", "決算"]
    for path in ("/api/pulse", "/api/inbox", "/api/pay", "/api/changes", "/api/history"):
        assert hr.get(path).status_code == 200, path


def test_5つのファイルを置いて確定すると計算される(tmp_path):
    hr, headers = login(blank(tmp_path))
    result = hr.post("/api/files/first/check", json={"files": encoded()}, headers=headers).json()
    assert result["ok"] is True and result["headcount"] == 550
    assert result["files"]["職員"] == {"rows": 550, "errors": []}
    assert hr.get("/api/today").json()["empty"] is True
    done = hr.post("/api/files/first/confirm", json={"token": result["token"]}, headers=headers).json()
    assert done == {"version": 1, "grew": True, "stopped": 13}
    today = hr.get("/api/today").json()
    assert "empty" not in today and today["run"]["total"] == 550 and today["headcount"] == 550


def test_検査に当たるファイルがあると札を出さず1行も入らない(tmp_path):
    hr, headers = login(blank(tmp_path))
    files = demo.sample_files()
    files["勤怠"] = files["勤怠"].replace(b"000101,", b"999999,", 1)
    result = hr.post("/api/files/first/check", json={"files": encoded(files)}, headers=headers).json()
    assert result["ok"] is False and "token" not in result
    assert result["files"]["勤怠"]["errors"] and result["files"]["職員"]["errors"] == []
    assert hr.get("/api/today").json()["empty"] is True


def test_最初のデータは入力がある環境では受け取らない(app):
    hr, headers = login(app)
    response = hr.post("/api/files/first/check", json={"files": encoded()}, headers=headers)
    assert response.status_code == 400 and "もう" in response.json()["error"]


def test_最初のデータの送り方が違うと日本語で断る(tmp_path):
    hr, headers = login(blank(tmp_path))
    for sent in ({}, {"files": "x"}, {"files": {"職員": "%%%"}}):
        response = hr.post("/api/files/first/check", json=sent, headers=headers)
        assert response.status_code == 400, sent
        assert response.json()["error"]


def test_最初のデータもログインとCSRFを通す(tmp_path):
    app = blank(tmp_path)
    assert client(app).post("/api/files/first/check", json={"files": {}}).status_code == 401
    hr, _ = login(app)
    assert hr.post("/api/files/first/check", json={"files": {}}).status_code == 403
    assert hr.post("/api/files/first/confirm", json={"token": "x"}).status_code == 403


def test_計算の前は確認が終わったと見せず質問の下書きも頼まない(tmp_path):
    home = tmp_path / "blank"
    workspace.init_sample(home)
    called = []

    async def ask(payload):
        called.append(payload)
        return {"people": []}
    hr, headers = login(create_app(home, ask))
    assert hr.get("/api/inbox").json()["calculated"] is False
    response = hr.post("/api/questions/draft", headers=headers)
    assert response.status_code == 400 and "計算" in response.json()["error"] and called == []


def test_静的ファイルは毎回サーバーへ確かめさせる(app):
    response = client(app).get("/static/app.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
