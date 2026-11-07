"""作業領域。環境の切り替え、排他、途中状態を残さない書き込み（INV21、INV22）。"""

import argparse
import fcntl
import json
import os
import shutil
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
HOME_ENV = "KOUNIN_HOME"
SAMPLE_HOME = ROOT / "var" / "sample-home"
SAMPLE_ENVIRONMENT = "架空環境"
LOCAL_ENVIRONMENT = "手元環境"
FIRST_EFFECTIVE = "2025-04-01"
SAMPLE_MONTH = "2026-10"
_held = threading.local()


class WorkspaceError(RuntimeError):
    """作業領域を開けない。理由を日本語の文にする。"""


def resolve_home() -> Path:
    value = os.environ.get(HOME_ENV)
    return Path(value) if value else SAMPLE_HOME


@contextmanager
def locked(home: Path) -> Iterator[None]:
    """作業領域の全更新を1つの排他に通す。別のプロセスからの更新とも直列になる。

    同じスレッドの入れ子は外側の排他に乗る。検査から保存までを1つの区間にするためである。
    """
    path = home / "var" / ".lock"
    held = _held.__dict__.setdefault("paths", set())
    if path in held:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        held.add(path)
        try:
            yield
        finally:
            held.discard(path)
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_bytes(path: Path, data: bytes) -> None:
    """一時ファイルへ書いてから置き換える。途中で落ちても前の内容か新しい内容のどちらかが残る。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def write_text(path: Path, text: str) -> None:
    write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        out.flush()
        os.fsync(out.fileno())


def read_jsonl(path: Path) -> list[dict]:
    """末尾の行が途中で切れていたら、その行だけを捨てる。途中の行が壊れていれば止める。"""
    if not path.exists():
        return []
    lines = path.read_text("utf-8").split("\n")
    rows = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if index >= len(lines) - 2:
                break
            raise WorkspaceError(f"{path.name} の {index + 1} 行目が壊れています") from None
    return rows


def config(home: Path) -> dict:
    return yaml.safe_load((home / "config.yaml").read_text("utf-8"))


def users(home: Path) -> list[dict]:
    return yaml.safe_load((home / "users.yaml").read_text("utf-8"))["users"]


def is_ready(home: Path) -> bool:
    return (home / "config.yaml").exists()


def init_sample(home: Path) -> None:
    """架空環境を作る。既にあれば触らない。"""
    if is_ready(home):
        return
    pack = home / "pack" / "1"
    shutil.copytree(ROOT / "rules" / "sample", pack)
    write_json(pack / "meta.json", {"effective_from": FIRST_EFFECTIVE, "summary": "最初の版", "approved_by": None})
    data = ROOT / "data" / "sample"
    inputs = home / "var" / "months" / SAMPLE_MONTH / "inputs" / "1"
    shutil.copytree(data / SAMPLE_MONTH, inputs)
    write_json(inputs / "meta.json", {"note": "毎月のファイルが届いた状態", "by": None})
    shutil.copy(data / "users.yaml", home / "users.yaml")
    write_text(home / "config.yaml", yaml.safe_dump(
        {"environment": SAMPLE_ENVIRONMENT, "current_month": SAMPLE_MONTH, "claude_allowed": False},
        allow_unicode=True))


def refresh_sample_users(home: Path) -> None:
    """架空環境の利用者を見本の最新へ合わせる。手元環境には触らない。"""
    if config(home).get("environment") == SAMPLE_ENVIRONMENT:
        shutil.copy(ROOT / "data" / "sample" / "users.yaml", home / "users.yaml")


def reset_sample(home: Path) -> None:
    """架空環境だけを初期化する。手元環境は拒否する（INV21）。"""
    if is_ready(home) and config(home).get("environment") != SAMPLE_ENVIRONMENT:
        raise WorkspaceError("手元環境は初期化できません")
    if home.exists():
        shutil.rmtree(home)
    init_sample(home)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["reset"])
    args = parser.parse_args()
    if args.command == "reset":
        reset_sample(resolve_home())
        print(f"初期化しました: {resolve_home()}")


if __name__ == "__main__":
    main()
