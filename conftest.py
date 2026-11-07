"""試験の共通の部品。手元の環境変数が試験へ漏れないようにする。"""

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_home(monkeypatch):
    """手元に KOUNIN_HOME があっても、試験が実データに触らないようにする。"""
    monkeypatch.delenv("KOUNIN_HOME", raising=False)
