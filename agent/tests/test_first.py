"""最初のデータ。空の環境に5つのファイルを渡し、人が確定して初めて入力になる（SPEC §6、INV13、INV18）。"""

import shutil

import pytest

from agent.core import audit, demo, ingest, month as months
from agent.core import pack as packs
from agent.core import workspace

MONTH = "2026-10"
HR = {"id": "hr", "name": "雨宮 文", "role": "人事担当"}


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    workspace.init_sample(path)
    return path


def rules(home):
    return packs.for_date(home, months.base_date(MONTH))


def errors(results):
    return {kind: result.errors for kind, result in results.items() if result.errors}


def test_環境は入力なしで始まる(home):
    assert not ingest.has_input(home, MONTH)
    with pytest.raises(months.MonthError):
        months.run(home, MONTH, HR)


def test_見本の5つのファイルは検査に通る(home):
    assert errors(ingest.check_first(demo.sample_files(), rules(home))) == {}


def test_入れた結果は版1で見本のファイルと同じ中身になる(home):
    files = demo.sample_files()
    token = ingest.stage_first(home, MONTH, files)
    assert ingest.confirm_first(home, MONTH, token, HR) == (1, True)
    directory = ingest.input_dir(home, MONTH, 1)
    assert {path.stem: path.read_bytes() for path in directory.glob("*.csv")} == files
    assert (directory / "meta.json").exists()
    run, _ = months.run(home, MONTH, HR)
    assert run["total"] == 550 and run["stopped"] == 13
    assert any(row["action"] == "データの確定" for row in audit.entries(home))


def test_足りないファイルがあると検査しない(home):
    files = demo.sample_files()
    del files[ingest.FINANCE]
    with pytest.raises(ValueError, match="決算"):
        ingest.check_first(files, rules(home))


def test_知らない種類のファイルは受け取らない(home):
    files = {**demo.sample_files(), ingest.JUDGMENTS: b"x"}
    with pytest.raises(ValueError, match="判定"):
        ingest.check_first(files, rules(home))


def test_勤怠の職員番号は同時に渡した名簿で調べる(home):
    files = demo.sample_files()
    files[ingest.ATTENDANCE] = files[ingest.ATTENDANCE].replace(b"000101,", b"999999,", 1)
    found = errors(ingest.check_first(files, rules(home)))
    assert list(found) == [ingest.ATTENDANCE] and "名簿にいません" in found[ingest.ATTENDANCE][0]


def test_職員のファイルが壊れていても残りの番号の検査を通過扱いにしない(home):
    files = {**demo.sample_files(), ingest.STAFF: "職員番号\n000101\n".encode()}
    found = errors(ingest.check_first(files, rules(home)))
    assert ingest.STAFF in found


def test_Shift_JISのファイルはUTF_8にして入れる(home):
    files = demo.sample_files()
    files[ingest.STAFF] = files[ingest.STAFF].decode("utf-8").encode("cp932")
    assert errors(ingest.check_first(files, rules(home))) == {}
    ingest.confirm_first(home, MONTH, ingest.stage_first(home, MONTH, files), HR)
    assert (ingest.input_dir(home, MONTH, 1) / "職員.csv").read_bytes() == demo.sample_files()[ingest.STAFF]


def test_INV18_同じ確定を繰り返しても版は増えない(home):
    token = ingest.stage_first(home, MONTH, demo.sample_files())
    ingest.confirm_first(home, MONTH, token, HR)
    assert ingest.confirm_first(home, MONTH, token, HR) == (1, False)
    assert ingest.latest_version(home, MONTH) == 1


def test_既に入力があれば別の内容で上書きしない(home):
    files = demo.sample_files()
    ingest.confirm_first(home, MONTH, ingest.stage_first(home, MONTH, files), HR)
    other = {**files, ingest.FINANCE: files[ingest.FINANCE].replace(b"8500000000", b"8600000000")}
    token = ingest.stage_first(home, MONTH, other)
    with pytest.raises(ValueError, match="もう"):
        ingest.confirm_first(home, MONTH, token, HR)
    assert (ingest.input_dir(home, MONTH, 1) / "決算.csv").read_bytes() == files[ingest.FINANCE]


@pytest.mark.parametrize("token", ["", "../../x", "abc", "0" * 24])
def test_預かっていない札では確定できない(home, token):
    with pytest.raises(FileNotFoundError):
        ingest.confirm_first(home, MONTH, token, HR)
    assert not ingest.has_input(home, MONTH)


def test_途中で落ちた版は入力として数えない(home):
    token = ingest.stage_first(home, MONTH, demo.sample_files())
    ingest.confirm_first(home, MONTH, token, HR)
    (ingest.input_dir(home, MONTH, 1) / "meta.json").unlink()
    assert not ingest.has_input(home, MONTH)
    assert ingest.confirm_first(home, MONTH, token, HR) == (1, True)


def test_リセットすると入力のない状態へ戻る(home):
    demo.load_sample(home)
    workspace.reset_sample(home)
    assert not ingest.has_input(home, MONTH)


def test_確認まで終えた環境も同じ入口からデータを入れる(tmp_path):
    home = tmp_path / "home"
    demo.ready(home)
    run = months.latest(home, MONTH)
    assert run["stopped"] == 0
    assert any(row["action"] == "データの確定" and "最初のデータ" in row["target"] for row in audit.entries(home))
    shutil.rmtree(home)


@pytest.mark.parametrize("kind, old, new, word", [
    (ingest.STAFF, b",4,17,2000,", b",abc,17,2000,", "等級"),
    (ingest.STAFF, b",2018-11-06,", b",2018-02-30,", "入職日"),
    (ingest.SEGMENTS, b",2018-11-06,", b",2018-02-30,", "開始日"),
    (ingest.ATTENDANCE, b"000101,1,0,6,0", b"000101,1,0,x,0", "勤続月数"),
    (ingest.FINANCE, b"450000000", b"abc", "経常利益"),
])
def test_検査を通った値は計算で落ちない(home, kind, old, new, word):
    files = demo.sample_files()
    assert old in files[kind]
    files[kind] = files[kind].replace(old, new, 1)
    found = errors(ingest.check_first(files, rules(home)))
    assert list(found) == [kind] and word in found[kind][0]


def test_預かった組が欠けていれば確定しない(home):
    token = ingest.stage_first(home, MONTH, demo.sample_files())
    (ingest.first_dir(home, MONTH, token) / "決算.csv").unlink()
    with pytest.raises(FileNotFoundError):
        ingest.confirm_first(home, MONTH, token, HR)
    assert not ingest.has_input(home, MONTH)
