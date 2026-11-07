"""一時URLの期限と排他（INV24）。時刻を渡して境界を確かめる。"""

from datetime import UTC, datetime, timedelta

import pytest

from agent.core import demo, links, month as months, workspace

MONTH = "2026-10"
HR = {"id": "hr", "name": "雨宮 文", "role": "人事担当"}
AT = datetime(2026, 11, 7, 3, 0, tzinfo=UTC)


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    workspace.init_sample(path)
    demo.load_sample(path)
    months.run(path, MONTH, HR)
    return path


def test_期限の直前まで開けて期限ちょうどから閉じる(home):
    token, link = links.issue(home, MONTH, "boss-a", ["質問"], HR, now=AT)
    limit = datetime.fromisoformat(link["expires_at"])
    assert limit == AT + links.LIFETIME
    assert links.open_link(home, token, MONTH, now=limit - timedelta(seconds=1))["id"] == link["id"]
    with pytest.raises(links.LinkClosed, match="期限"):
        links.open_link(home, token, MONTH, now=limit)


def test_別の月では開けない(home):
    token, _ = links.issue(home, MONTH, "boss-a", ["質問"], HR)
    with pytest.raises(links.LinkClosed):
        links.open_link(home, token, "2026-11")


def test_locked_は同じスレッドの入れ子で止まらない(home):
    with workspace.locked(home):
        with workspace.locked(home):
            pass
    with workspace.locked(home):
        pass
