"""正規化ハッシュ。項目の順序に依らず、中身が違えば別の値になる。"""

from datetime import date

from engine.hashing import stable_hash
from engine.models import Employee, Segment


def test_項目の順序に依らず同じ値():
    a = {"emp_id": "P", "segments": [{"group": "X", "kind": "通常"}], "rank": "B"}
    b = {"rank": "B", "segments": [{"kind": "通常", "group": "X"}], "emp_id": "P"}
    assert stable_hash(a) == stable_hash(b)


def test_入れ子の順序にも依らない():
    assert stable_hash({"a": {"x": 1, "y": 2}, "b": [1, 2]}) == stable_hash({"b": [1, 2], "a": {"y": 2, "x": 1}})


def test_値が違えば別の値():
    assert stable_hash({"rank": "B"}) != stable_hash({"rank": "A"})


def test_並びのある配列は順序を区別する():
    assert stable_hash({"ids": ["P", "N"]}) != stable_hash({"ids": ["N", "P"]})


def test_日本語と日付を扱える():
    value = {"氏名": "合成職員P", "hire_date": date(2025, 4, 1)}
    assert stable_hash(value) == stable_hash({"hire_date": date(2025, 4, 1), "氏名": "合成職員P"})
    assert len(stable_hash(value)) == 64


def test_モデルを辞書にした順序に依らない():
    emp = Employee(emp_id="P", display_name="合成職員P", segments=[Segment(group="X")])
    dumped = emp.model_dump(mode="json")
    assert stable_hash(dumped) == stable_hash(dict(reversed(list(dumped.items()))))
