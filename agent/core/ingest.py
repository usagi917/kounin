"""データの受け渡し。検査、差の確認、人の確定、版つきの保存の順に進む（SPEC §6）。

検査に通らないファイルは1行も取り込まない。不足を0や既定値で埋めない（INV13）。
"""

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from agent.core import audit
from agent.core.workspace import locked, write_bytes, write_json

STAFF, ATTENDANCE, SEGMENTS, RANKS, FINANCE, JUDGMENTS = "職員", "勤怠", "所属", "考課", "決算", "判定"
COLUMNS = {
    STAFF: ["職員番号", "氏名", "雇用区分", "職種", "表No", "等級", "号俸", "調整給", "処遇改善", "職位",
            "管理職手当", "入職日", "特別加給金", "手入力の支給額", "手入力の分配金", "分配の母数"],
    ATTENDANCE: ["職員番号", "精勤係数", "欠勤等の率", "勤続月数", "未出勤率"],
    SEGMENTS: ["職員番号", "職場", "種類", "開始日", "終了日"],
    RANKS: ["職員番号", "期", "ランク"],
    FINANCE: ["期", "売上", "人件費", "営業利益", "経常利益"],
    JUDGMENTS: ["職員番号", "職場", "判定者"],
}
UPLOADABLE = (STAFF, ATTENDANCE, SEGMENTS, RANKS, FINANCE)
SEGMENT_KINDS = {"通常", "新卒ローテ", "応援"}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUMBER = re.compile(r"-?\d+(\.\d+)?")
INSTRUCTION = re.compile(r"(?i)(無視して|指示に従|命令|ignore (all|previous)|system prompt|あなたは.{0,20}(です|として))")
MAX_ERRORS = 20


@dataclass
class Check:
    kind: str
    errors: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)


def decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文字コードが読めません。UTF-8 か Shift_JIS の CSV にしてください")


def read_rows(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(row) for row in reader]
    return list(reader.fieldnames or []), rows


def input_dir(home: Path, month: str, version: int | None = None) -> Path:
    base = home / "var" / "months" / month / "inputs"
    return base / str(version if version is not None else latest_version(home, month))


def latest_version(home: Path, month: str) -> int:
    base = home / "var" / "months" / month / "inputs"
    numbers = [int(path.name) for path in base.iterdir()
               if path.name.isdigit() and (path / "meta.json").exists()] if base.exists() else []
    if not numbers:
        raise FileNotFoundError(f"{month} の入力がありません")
    return max(numbers)


def load_rows(directory: Path, kind: str) -> list[dict]:
    path = directory / f"{kind}.csv"
    return read_rows(path.read_text("utf-8"))[1] if path.exists() else []


def _cells(rows: list[dict]) -> list[str]:
    errors = []
    for number, row in enumerate(rows, start=2):
        for column, value in row.items():
            if column is None or value is None:
                errors.append(f"{number}行目：列の数が見出しと合っていません")
                continue
            text = value.strip()
            if text[:1] in ("=", "+", "@") and not NUMBER.fullmatch(text):
                errors.append(f"{number}行目「{column}」：数式のセルは取り込めません。値にしてください")
            if INSTRUCTION.search(text):
                errors.append(f"{number}行目「{column}」：指示のような文があります。データだけにしてください")
    return errors


def _ids(rows: list[dict], known: set[str]) -> list[str]:
    errors = []
    seen_width = {len(emp_id) for emp_id in known}
    for number, row in enumerate(rows, start=2):
        emp_id = row.get("職員番号", "").strip()
        if not emp_id:
            errors.append(f"{number}行目：職員番号が空欄です")
        elif known and emp_id not in known:
            if emp_id.isdigit() and len(emp_id) not in seen_width:
                errors.append(f"{number}行目：職員番号「{emp_id}」の桁が違います。先頭のゼロが落ちていないか確かめてください")
            else:
                errors.append(f"{number}行目：職員番号「{emp_id}」は名簿にいません")
    return errors


def _duplicates(rows: list[dict]) -> list[str]:
    seen, errors = set(), []
    for number, row in enumerate(rows, start=2):
        if row["職員番号"] in seen:
            errors.append(f"{number}行目：職員番号「{row['職員番号']}」が重複しています")
        seen.add(row["職員番号"])
    return errors


def _segments(rows: list[dict], groups: set[str]) -> list[str]:
    errors, spans = [], {}
    for number, row in enumerate(rows, start=2):
        if row["職場"] not in groups:
            errors.append(f"{number}行目：職場「{row['職場']}」は登録されていません")
        if row["種類"] not in SEGMENT_KINDS:
            errors.append(f"{number}行目：種類「{row['種類']}」は通常、新卒ローテ、応援のどれかにしてください")
        for column in ("開始日", "終了日"):
            if row[column] and not DATE.fullmatch(row[column]):
                errors.append(f"{number}行目「{column}」：日付は 2026-10-31 の形にしてください")
        if not all(not row[c] or DATE.fullmatch(row[c]) for c in ("開始日", "終了日")):
            continue
        start, end = row["開始日"], row["終了日"]
        if start and end and start > end:
            errors.append(f"{number}行目：開始日が終了日より後です")
        spans.setdefault(row["職員番号"], []).append((start, end, number))
    for items in spans.values():
        dated = sorted((start, end or "9999-12-31", number) for start, end, number in items if start)
        for (_, end, _), (start, _, number) in zip(dated, dated[1:]):
            if start <= end:
                errors.append(f"{number}行目：在籍期間が前の行と重なっています")
    return errors


def _staff(rows: list[dict], jobs: set[str]) -> list[str]:
    errors = []
    for number, row in enumerate(rows, start=2):
        if row["職種"] and row["職種"] not in jobs:
            errors.append(f"{number}行目：職種「{row['職種']}」は登録されていません")
        if row["入職日"] and not DATE.fullmatch(row["入職日"]):
            errors.append(f"{number}行目「入職日」：日付は 2026-10-31 の形にしてください")
    return errors


def check(kind: str, raw: bytes, *, known_ids: set[str], groups: set[str], jobs: set[str], ranks: set[str]) -> Check:
    result = Check(kind)
    if kind not in UPLOADABLE:
        result.errors.append(f"「{kind}」のファイルは受け取れません")
        return result
    try:
        columns, rows = read_rows(decode(raw))
    except (ValueError, csv.Error) as error:
        result.errors.append(str(error))
        return result
    if columns != COLUMNS[kind]:
        lost = [c for c in COLUMNS[kind] if c not in columns]
        extra = [c for c in columns if c not in COLUMNS[kind]]
        detail = "、".join(([f"足りない列：{'・'.join(lost)}"] if lost else [])
                          + ([f"知らない列：{'・'.join(extra)}"] if extra else [])) or "列の順が違います"
        result.errors.append(f"列が前と違います。{detail}")
        return result
    if not rows:
        result.errors.append("中身の行がありません")
        return result
    result.errors += _cells(rows)
    if result.errors:
        result.errors = result.errors[:MAX_ERRORS]
        return result
    if kind != FINANCE:
        result.errors += _ids(rows, set() if kind == STAFF else known_ids)
    if kind in (STAFF, ATTENDANCE, RANKS):
        result.errors += _duplicates(rows)
    if kind == STAFF:
        result.errors += _staff(rows, jobs)
    if kind == SEGMENTS:
        result.errors += _segments(rows, groups)
    if kind == RANKS:
        result.errors += [f"{n}行目：ランク「{row['ランク']}」は表にありません"
                          for n, row in enumerate(rows, start=2) if row["ランク"] and row["ランク"] not in ranks]
    if kind == ATTENDANCE:
        result.errors += [f"{n}行目「{c}」：数ではありません" for n, row in enumerate(rows, start=2)
                          for c in COLUMNS[kind][1:] if row[c] and not NUMBER.fullmatch(row[c])]
    result.errors = result.errors[:MAX_ERRORS]
    result.rows = [] if result.errors else rows
    return result


def row_key(kind: str, row: dict) -> str:
    return row["期"] if kind == FINANCE else row["職員番号"]


def differences(kind: str, before: list[dict], after: list[dict]) -> dict:
    """件数だけを返す。今のファイルとの差。"""
    def grouped(rows: list[dict]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for row in rows:
            result.setdefault(row_key(kind, row), []).append(row)
        return result
    old, new = grouped(before), grouped(after)
    return {"added": len(new.keys() - old.keys()), "removed": len(old.keys() - new.keys()),
            "changed": sum(old[key] != new[key] for key in old.keys() & new.keys()), "rows": len(after)}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stage(home: Path, month: str, kind: str, raw: bytes) -> str:
    """検査に通ったファイルを預かる。同じ中身は同じ名前になる。"""
    token = digest(raw)[:24]
    write_bytes(home / "var" / "months" / month / "staged" / f"{kind}-{token}.csv", raw)
    return token


def new_version(home: Path, month: str, replace: dict[str, bytes], meta: dict) -> int:
    """前の版を写し、渡されたファイルだけを置き換えた新しい版を作る。前の版は残す。"""
    current = latest_version(home, month)
    source = input_dir(home, month, current)
    target = input_dir(home, month, current + 1)
    for path in source.iterdir():
        if path.suffix == ".csv" and path.stem not in replace:
            write_bytes(target / path.name, path.read_bytes())
    for kind, data in replace.items():
        write_bytes(target / f"{kind}.csv", data)
    write_json(target / "meta.json", meta)     # 最後に書く。これがなければ版として数えない
    return current + 1


def confirm(home: Path, month: str, kind: str, token: str, actor: dict) -> tuple[int, bool]:
    """預かったファイルを確定する。同じ中身なら版を増やさない（INV18）。増えたかどうかも返す。"""
    with locked(home):
        staged = home / "var" / "months" / month / "staged" / f"{kind}-{token}.csv"
        if not staged.exists():
            raise FileNotFoundError("確定するファイルが見つかりません。もう一度渡してください")
        raw = decode(staged.read_bytes()).encode("utf-8")
        current = latest_version(home, month)
        now = input_dir(home, month, current) / f"{kind}.csv"
        if now.exists() and now.read_bytes() == raw:
            return current, False
        version = new_version(home, month, {kind: raw}, {"by": actor["name"], "kind": kind})
        audit.record(home, actor, "データの確定", f"{month} {kind}", "新しい入力の版", f"入力 {version}")
        return version, True


def rewrite(kind: str, rows: list[dict]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, COLUMNS[kind], lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def answer_rank(home: Path, month: str, emp_id: str, rank: str, actor: dict) -> int:
    """上司の答えを考課のファイルの新しい版にする。"""
    with locked(home):
        directory = input_dir(home, month)
        rows = load_rows(directory, RANKS)
        target = next((row for row in rows if row["職員番号"] == emp_id), None)
        if target is None:
            raise ValueError("その職員は考課のファイルにいません")
        target["ランク"] = rank
        version = new_version(home, month, {RANKS: rewrite(RANKS, rows)},
                              {"by": actor["name"], "kind": RANKS})
        audit.record(home, actor, "考課の回答", f"{month} 1人", "新しい入力の版", f"入力 {version}")
        return version


def answer_judgment(home: Path, month: str, emp_id: str, group: str, actor: dict) -> int:
    """前任の判定を判定のファイルの新しい版にする。その期のその人にだけ効く。"""
    with locked(home):
        directory = input_dir(home, month)
        rows = [row for row in load_rows(directory, JUDGMENTS) if row["職員番号"] != emp_id]
        rows.append({"職員番号": emp_id, "職場": group, "判定者": actor["name"]})
        version = new_version(home, month, {JUDGMENTS: rewrite(JUDGMENTS, rows)},
                              {"by": actor["name"], "kind": JUDGMENTS})
        audit.record(home, actor, "前任の判定", f"{month} 1人", "新しい入力の版", f"入力 {version}")
        return version


def input_hash(directory: Path) -> str:
    parts = {path.name: digest(path.read_bytes()) for path in sorted(directory.glob("*.csv"))}
    return digest(repr(sorted(parts.items())).encode("utf-8"))
