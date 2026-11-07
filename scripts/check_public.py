"""コミットされるファイルに実データが混ざっていないかを調べる。

実値そのものはこのファイルに書かない。公開リポジトリの外にある
../docs/real-data-denylist.txt があれば、その文字列も拒否する。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DENYLIST = ROOT.parent / "docs" / "real-data-denylist.txt"
FORBIDDEN_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb"}
PATTERNS = {
    "Excel のセル位置": re.compile(r"シート\s*[A-Z]{1,3}\d{1,5}"),
    "実在らしい法人名": re.compile(r"医療法人(?:社団|財団)?\s*[一-龥]{1,6}会"),
    "生年月日の値": re.compile(r"生年月日\s*[:：]\s*\d"),
    "職員番号の値": re.compile(r"職員番号\s*[:：]\s*\d"),
}


def load_denylist() -> list[re.Pattern[str]]:
    if not DENYLIST.exists():
        return []
    lines = DENYLIST.read_text(encoding="utf-8").splitlines()
    values = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    return [re.compile(rf"(?<![\d.A-Za-z]){re.escape(v)}(?![\dA-Za-z])") for v in values]


def check_file(name: str, denied: list[re.Pattern[str]]) -> list[str]:
    path = Path(name)
    if path.as_posix().startswith("data/raw/"):
        return [f"{name}: data/raw/ はコミットできません"]
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return [f"{name}: Excel ファイルはコミットできません"]
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    errors = []
    for number, line in enumerate(text.splitlines(), start=1):
        errors += [f"{name}:{number}: {label}" for label, rx in PATTERNS.items() if rx.search(line)]
        # 一致した実値は出力しない。ログに残さないためである。
        errors += [f"{name}:{number}: 禁止リストの値" for rx in denied if rx.search(line)]
    return errors


def main(names: list[str]) -> int:
    denied = load_denylist()
    errors = [error for name in names for error in check_file(name, denied)]
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
