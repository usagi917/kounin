"""公開の検査が禁止の見本を拒否することを確かめる（T04）。見本は実在しない値で作り、このファイル自体が検査に当たらないよう実行時につなぐ。"""

import subprocess
import sys

import pytest

from agent.core.workspace import ROOT

SCRIPT = ROOT / "scripts" / "check_public.py"


def run(*paths) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, paths)], capture_output=True, text=True)


@pytest.mark.parametrize("name, text, label", [
    ("a.md", "シート" + "AB12 の値", "セル位置"),
    ("b.md", "医療法人" + "社団 架空会", "法人名"),
    ("c.md", "生年月日" + ": 1990-01-01", "生年月日"),
    ("d.md", "職員番号" + ": 123456", "職員番号"),
])
def test_禁止の見本を拒否する(tmp_path, name, text, label):
    path = tmp_path / name
    path.write_text(text, "utf-8")
    result = run(path)
    assert result.returncode == 1 and label in result.stderr


def test_Excelを拒否する(tmp_path):
    path = tmp_path / "x.xlsx"
    path.write_bytes(b"PK")
    assert run(path).returncode == 1


def test_普通の文は通す(tmp_path):
    path = tmp_path / "ok.md"
    path.write_text("架空の職員で動きます", "utf-8")
    assert run(path).returncode == 0
