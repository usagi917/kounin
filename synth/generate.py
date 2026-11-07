"""架空の職員の生成。キーは全部ルールパックの表から取り、実在の職員の値は写さない。固定シードで同じ結果になる。"""

import argparse
import csv
import io
import random
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "rules" / "sample"
OUT = ROOT / "data" / "sample"
SEED = 20261108
MONTH = "2026-10"
PERIOD = "2026-winter"
PERIOD_START = date(2026, 5, 1)
PERIOD_END = date(2026, 10, 31)

STAFF_COLUMNS = ["職員番号", "氏名", "雇用区分", "職種", "表No", "等級", "号俸", "調整給", "処遇改善", "職位",
                 "管理職手当", "入職日", "特別加給金", "手入力の支給額", "手入力の分配金", "分配の母数"]
ATTENDANCE_COLUMNS = ["職員番号", "精勤係数", "欠勤等の率", "勤続月数", "未出勤率"]
SEGMENT_COLUMNS = ["職員番号", "職場", "種類", "開始日", "終了日"]
RANK_COLUMNS = ["職員番号", "期", "ランク"]
FINANCE_COLUMNS = ["期", "売上", "人件費", "営業利益", "経常利益"]

SURNAMES = ["朝霧", "雨宮", "綾瀬", "有栖", "泉川", "糸井", "稲葉", "卯月", "梅ヶ谷", "江波", "奥灘", "折原",
            "柏葉", "風間", "桂川", "叶谷", "鏑木", "烏丸", "桐生", "久遠", "楠見", "蔵前", "黒羽", "桑畑",
            "小鳥遊", "早乙女", "佐久良", "雫石", "東雲", "白樺", "杉森", "涼風", "千歳", "園部", "鷹取", "滝沢",
            "月島", "筒見", "露崎", "時任", "常盤", "鳥海", "長月", "南雲", "仁科", "沼倉", "野分", "羽生田"]
GIVEN = ["あかり", "朝陽", "彩葉", "碧", "伊織", "樹", "詩", "英治", "笑美", "織江", "海斗", "奏", "和葉",
         "清香", "銀河", "楓", "恋雪", "琴音", "咲良", "栞", "潤", "翔真", "澄", "想", "空", "大河", "千尋",
         "紬", "照", "透", "直", "渚", "望", "遥", "晴", "灯", "ひより", "文", "蛍", "真帆", "岬", "実月",
         "結", "悠", "雪乃", "蓉", "凛", "蓮"]
RANKS = {"S": 3, "A": 15, "B+": 20, "B": 40, "B-": 12, "C": 6, "D": 3, "E": 1}
GRADE_WEIGHTS = [10, 14, 18, 18, 15, 12, 8, 5]
ALLOWANCE = {1: 60000, 2: 40000, 3: 25000, 4: 15000}     # 管理職手当は架空
ATTENDANCE = ["0.95", "0.9", "0.85", "0.8", "0.7"]


def csv_text(columns: list[str], rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def month_end(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1) - timedelta(days=1)


def after_months(months: int) -> date:
    """評価期間の初めから months か月後の月末。"""
    index = PERIOD_START.month - 1 + months - 1
    return month_end(date(PERIOD_START.year + index // 12, index % 12 + 1, 1))


def names(rng: random.Random, count: int) -> list[str]:
    pairs = [f"{surname} {given}" for surname in SURNAMES for given in GIVEN]
    return rng.sample(pairs, count)


def staff_row(rng: random.Random, emp_id: str, name: str, jobs: list[str], table_of: dict[str, int],
              coef: dict) -> dict:
    job = rng.choice(jobs)
    table = table_of[job]
    grade = rng.choices(sorted(coef["grade_points"]), weights=GRADE_WEIGHTS)[0]
    hired = date.fromordinal(rng.randint(date(2002, 4, 1).toordinal(), date(2025, 10, 31).toordinal()))
    top = max(coef["base_pay"][table][grade])
    step = min(top, max(1, (2026 - hired.year) * 2 + rng.randint(-2, 4)))
    adjustment = 0 if rng.random() < 0.85 else rng.choice([-2000, -1000, 500, 1000, 1500, 2000, 3000, 5000])
    treatment = rng.choice([3000, 5000, 8000]) if rng.random() < 0.2 else 0
    return {"職員番号": emp_id, "氏名": name, "雇用区分": "正職員", "職種": job, "表No": table, "等級": grade,
            "号俸": step, "調整給": adjustment, "処遇改善": treatment, "職位": "", "管理職手当": 0,
            "入職日": hired.isoformat(), "特別加給金": 0, "手入力の支給額": "", "手入力の分配金": "", "分配の母数": ""}


def segment(emp_id: str, group: str, kind: str, start: date | None, end: date | None) -> dict:
    return {"職員番号": emp_id, "職場": group, "種類": kind, "開始日": start.isoformat() if start else "",
            "終了日": end.isoformat() if end else ""}


def other(rng: random.Random, groups: list[str], group: str) -> str:
    return rng.choice([g for g in groups if g != group])


def generate(count: int = 550) -> dict[str, str]:
    """ファイル名 → 中身 を返す。fixes/ は人が渡す見本である。"""
    rng = random.Random(SEED)
    coef = yaml.safe_load((PACK / "coefficients.yaml").read_text("utf-8"))
    groups = yaml.safe_load((PACK / "pack.yaml").read_text("utf-8"))["groups"]
    jobs = sorted(coef["job_coefficients"])
    tables = sorted(coef["base_pay"])
    table_of = {job: tables[index % len(tables)] for index, job in enumerate(jobs)}   # 職種と表の対応は架空

    ids = [f"{101 + index:06d}" for index in range(count)]
    people = names(rng, count)
    staff = [staff_row(rng, emp_id, name, jobs, table_of, coef) for emp_id, name in zip(ids, people)]
    by_id = {row["職員番号"]: row for row in staff}
    homes = {emp_id: groups[index % len(groups)] for index, emp_id in enumerate(rng.sample(ids, count))}
    pool = list(ids)
    rng.shuffle(pool)

    def take(number: int) -> list[str]:
        return [pool.pop() for _ in range(number)]

    attendance = {emp_id: {"職員番号": emp_id, "精勤係数": "1", "欠勤等の率": "0", "勤続月数": 6, "未出勤率": "0"}
                  for emp_id in ids}
    ranks = {emp_id: rng.choices(list(RANKS), weights=list(RANKS.values()))[0] for emp_id in ids}
    segments: dict[str, list[dict]] = {emp_id: [segment(emp_id, homes[emp_id], "通常", date.fromisoformat(
        by_id[emp_id]["入職日"]), None)] for emp_id in ids}

    # 止まる人。連絡の相手が違うように分ける
    gaps = take(3)                 # 所属の日付が欠けている → 人事担当がファイルを渡す
    support = take(3)              # 応援が4か月以上 → 前任が判定する
    tie = take(1)                  # 3か月ずつの異動 → 前任が判定する
    transfers = take(30)           # 通常の異動 → R001 で進む
    rotation = take(3)             # 新卒ローテ → R001 で進む
    hires = take(5)                # 入職4か月未満 → R002 で進む
    fixed = take(2)                # 手入力の支給額
    unrated = [emp_id for emp_id in pool if homes[emp_id] == groups[0]][:3] \
        + [emp_id for emp_id in pool if homes[emp_id] == groups[3]][:3]    # 考課が未提出 → 上司が答える
    pool = [emp_id for emp_id in pool if emp_id not in unrated]

    fixes = {}
    for emp_id in gaps:
        home, away = homes[emp_id], other(rng, groups, homes[emp_id])
        hired = date.fromisoformat(by_id[emp_id]["入職日"])
        segments[emp_id] = [segment(emp_id, home, "通常", hired, None), segment(emp_id, away, "通常", None, None)]
        fixes[emp_id] = [segment(emp_id, home, "通常", hired, after_months(4)),
                         segment(emp_id, away, "通常", date(2026, 9, 1), None)]
    for emp_id in support:
        home, away = homes[emp_id], other(rng, groups, homes[emp_id])
        hired = date.fromisoformat(by_id[emp_id]["入職日"])
        segments[emp_id] = [segment(emp_id, home, "通常", hired, after_months(2)),
                            segment(emp_id, away, "応援", date(2026, 7, 1), None)]
    for emp_id in tie:
        home, away = homes[emp_id], other(rng, groups, homes[emp_id])
        hired = date.fromisoformat(by_id[emp_id]["入職日"])
        segments[emp_id] = [segment(emp_id, home, "通常", hired, after_months(3)),
                            segment(emp_id, away, "通常", date(2026, 8, 1), None)]
    for emp_id in transfers:
        first = rng.choice([4, 5])
        home, away = homes[emp_id], other(rng, groups, homes[emp_id])
        hired = date.fromisoformat(by_id[emp_id]["入職日"])
        segments[emp_id] = [segment(emp_id, home, "通常", hired, after_months(first)),
                            segment(emp_id, away, "通常", date(2026, 5 + first, 1), None)]
    for emp_id in rotation:
        home, away = homes[emp_id], other(rng, groups, homes[emp_id])
        by_id[emp_id].update({"入職日": "2026-04-01", "等級": 1, "号俸": 1, "調整給": 0})
        segments[emp_id] = [segment(emp_id, home, "新卒ローテ", date(2026, 4, 1), after_months(4)),
                            segment(emp_id, away, "通常", date(2026, 9, 1), None)]
    for emp_id in hires:
        by_id[emp_id].update({"入職日": "2026-08-01", "号俸": 1})
        segments[emp_id] = [segment(emp_id, homes[emp_id], "通常", date(2026, 8, 1), None)]
        attendance[emp_id]["勤続月数"] = 3
    for emp_id, (amount, share, member) in zip(fixed, [(4800000, 40000, "1"), (3200000, 0, "")]):
        by_id[emp_id].update({"手入力の支給額": amount, "手入力の分配金": share, "分配の母数": member})

    positions = sorted(coef["position_classes"])
    for emp_id in rng.sample(pool, 40):
        position = rng.choice(positions)
        by_id[emp_id].update({"職位": position, "管理職手当": ALLOWANCE[coef["position_classes"][position]]})
    for emp_id in rng.sample(pool, round(count * 0.08)):
        attendance[emp_id]["精勤係数"] = rng.choice(ATTENDANCE)
    for key, number, values in (("欠勤等の率", 6, ["1.5", "3", "5"]), ("勤続月数", 5, [2, 3, 4, 5]),
                                ("未出勤率", 6, ["0.25", "0.5"])):
        for emp_id in rng.sample(pool, number):
            attendance[emp_id][key] = rng.choice(values)
    for emp_id in rng.sample(pool, 8):
        by_id[emp_id]["特別加給金"] = rng.choice([10000, 20000, 30000])

    rank_rows = [{"職員番号": emp_id, "期": PERIOD, "ランク": "" if emp_id in unrated else ranks[emp_id]}
                 for emp_id in ids]
    finance = [{"期": "前期", "売上": 8200000000, "人件費": 4900000000, "営業利益": 330000000, "経常利益": 360000000},
               {"期": "今期", "売上": 8500000000, "人件費": 5000000000, "営業利益": 410000000, "経常利益": 450000000}]
    segment_rows = [row for emp_id in ids for row in segments[emp_id]]
    fixed_rows = [row for emp_id in ids for row in fixes.get(emp_id, segments[emp_id])]
    bad_rows = [dict(row) for row in fixed_rows]
    bad_rows[0]["職場"] = "存在しない職場"
    bad_rows.append(dict(bad_rows[1], 職員番号="999999"))

    return {f"{MONTH}/職員.csv": csv_text(STAFF_COLUMNS, staff),
            f"{MONTH}/勤怠.csv": csv_text(ATTENDANCE_COLUMNS, [attendance[emp_id] for emp_id in ids]),
            f"{MONTH}/所属.csv": csv_text(SEGMENT_COLUMNS, segment_rows),
            f"{MONTH}/考課.csv": csv_text(RANK_COLUMNS, rank_rows),
            f"{MONTH}/決算.csv": csv_text(FINANCE_COLUMNS, finance),
            "fixes/所属.csv": csv_text(SEGMENT_COLUMNS, fixed_rows),
            "fixes/所属-誤りの見本.csv": csv_text(SEGMENT_COLUMNS, bad_rows)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--employees", type=int, default=550)
    args = parser.parse_args()
    for name, text in generate(args.employees).items():
        path = args.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"{name}: {text.count(chr(10)) - 1}行")


if __name__ == "__main__":
    main()
