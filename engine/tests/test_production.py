"""本番化の計算（役付加給、控除、内訳、手入力の職員）。期待値は engine を使わず、Decimal で手計算して固定した。

共通の土台：基本給10万円、点数100、総月数2、配分率0.5（基本給連動1.000か月、額部分1.000か月）。
1人だけなら 単価 = 基礎 ÷ 100、基本給連動 = 基礎、額部分 = 100 × 単価、計 = 基礎 + 100 × 単価。
"""

from fractions import Fraction

import pytest

from engine.calc import CalcError, calculate, total_basis
from engine.models import Coefficients, Employee, FundingInput, Placement

COEF = Coefficients(
    base_pay={1: {1: {1: 100000}}},
    grade_points={1: {"B": "100"}},
    job_coefficients={"J": "1"},
    rank_coefficients={"B": "1"},
    position_classes={"長": 1, "班": 2},
    position_points={"B": {1: "10", 2: "6"}},
)
FUND = FundingInput(total_months="2", base_share="0.5")


def emp(emp_id, att="1", adj=0, group="X", **kw):
    return Employee(emp_id=emp_id, display_name="合成", employment_type="正職員", job_type="J", table_no=1,
                    grade=1, step=1, adjustment_pay=adj, rank="B", attendance_coefficient=att,
                    segments=[{"group": group}], **kw)


def fixed(emp_id, **kw):
    """表の等級も考課もない手入力の職員。"""
    return Employee(emp_id=emp_id, display_name="合成", segments=[], **kw)


def place(emp_id, group="X"):
    return Placement(emp_id=emp_id, group=group)


def rows(staff, placements=None, coef=COEF, funding=FUND):
    placements = placements or [place(e.emp_id) for e in staff]
    result = calculate(staff, placements, coef, funding)
    assert result.ok, result.error
    return {e.emp_id: e for e in result.employees}, result


# ---- 既定値のままなら今の金額から変わらない ----

def test_defaults_keep_previous_amounts():
    out, result = rows([emp("A"), emp("B", att="0.5")])
    a, b = out["A"], out["B"]
    # 単価 1000。A: 100000 + 150×1000 = 250000、B: 100000 + 50×1000 = 150000（test_calc と同じ）
    assert (a.gross, a.amount, a.distribution_yen, a.distribution_net) == (250000, 250000, 50000, 50000)
    assert (b.gross, b.amount, b.distribution_net) == (150000, 150000, 0)
    assert (a.main_pay, a.total_pay, a.special_allowance, a.position_pay, a.treatment_pay) == (200000, 250000, 0, 0, 0)
    assert (a.deduction_absence, a.deduction_service, a.deduction_attendance, a.fixed) == (0, 0, 0, False)
    assert (result.total_amount, result.total_pay, result.position_unit_price) == (400000, 400000, 0)
    assert result.total_base_pay == 200000


# ---- 処遇改善は計算の基礎に入る ----

def test_treatment_pay_enters_the_basis():
    # 基礎 105000。基本給連動 105000、額部分は 単価 round(105000/100)=1050 × 100。計 210000。
    out, result = rows([emp("A", treatment_pay=5000)])
    a = out["A"]
    assert (a.base_pay, a.treatment_pay, a.base_linked, a.amount_part, a.gross, a.amount) == (
        100000, 5000, 105000, 105000, 210000, 210000)
    assert result.total_base_pay == 105000 and result.amount_fund == 105000 and result.unit_price == 1050


# ---- 控除3種の丸めの境界 ----

def test_absence_deduction_is_floored():
    # 調整給40：基礎100040、単価 round(1000.40)=1000、計 100040+100000 = 200040。
    # ① = 200040 × 1.7 ÷ 100 = 3400.68 → 切り捨てで 3400（四捨五入なら 3401）。支給額 = 千円丸め(196640) = 197000。
    a = rows([emp("A", adj=40, absence_hundredths="1.7")])[0]["A"]
    assert (a.gross, a.deduction_absence, a.amount) == (200040, 3400, 197000)


def test_service_deduction_rounds_half_up_and_full_service_is_zero():
    # 調整給1：計 200001。勤続3か月（満了6）：② = 200001 × 3 ÷ 6 = 100000.5 → 四捨五入で 100001。
    # 支給額 = 千円丸め(200001 − 100001 = 100000) = 100000。
    a = rows([emp("A", adj=1, service_months=3)])[0]["A"]
    assert (a.gross, a.deduction_service, a.amount) == (200001, 100001, 100000)
    for months in (None, 6, 7):   # None は満了扱い。満了以上は0
        assert rows([emp("A", adj=1, service_months=months)])[0]["A"].deduction_service == 0


def test_attendance_deduction_rounds_half_up():
    # 計 200001 × 0.5 = 100000.5 → 四捨五入で 100001。
    a = rows([emp("A", adj=1, attendance_loss_rate="0.5")])[0]["A"]
    assert (a.deduction_attendance, a.amount) == (100001, 100000)


def test_three_deductions_together_and_breakdown():
    # 計 200001。① 200001×1.7÷100 = 3400.017 → 3400。② 200001×2÷6 = 66667。③ 200001×0.05 = 10000.05 → 10000。
    # 支給額 = 千円丸め(200001 − 3400 − 66667 − 10000 = 119934) = 120000。特別加給金 7000 → 支給計 127000。
    a = rows([emp("A", adj=1, absence_hundredths="1.7", service_months=4, attendance_loss_rate="0.05",
                  special_allowance=7000)])[0]["A"]
    assert (a.deduction_absence, a.deduction_service, a.deduction_attendance) == (3400, 66667, 10000)
    assert (a.amount, a.distribution_net, a.main_pay) == (120000, 0, 120000)
    assert (a.special_allowance, a.total_pay) == (7000, 127000)


def test_amount_is_zero_when_deductions_exceed_gross():
    # 欠勤150％：① = 200000 × 1.5 = 300000。計 − ① = −100000 → 0。
    out, result = rows([emp("A", absence_hundredths="150", special_allowance=3000)])
    a = out["A"]
    assert (a.deduction_absence, a.amount, a.total_pay) == (300000, 0, 3000)
    assert result.total_pay == 3000


# ---- 役付加給 ----

def test_position_unit_price_and_pay():
    # 管理職手当の合計 30000 + 20000 = 50000、役付の月数 1.1 → 55000。ポイント 10 + 6 = 16。
    # 単価 = 55000 ÷ 16 = 3437.5 → 3438（四捨五入）。長: 10 × 3438 = 34380、班: 6 × 3438 = 20628。
    # 3人とも基礎 10万円（管理職手当は基礎に入れない）。単価 1000、計 = 200000 + 役付加給。
    staff = [emp("P1", position="長", manager_allowance=30000), emp("P2", position="班", manager_allowance=20000),
             emp("P3")]
    funding = FundingInput(total_months="2", base_share="0.5", position_months="1.1")
    out, result = rows(staff, funding=funding)
    assert result.position_unit_price == 3438 and result.total_base_pay == 300000
    assert (out["P1"].position_pay, out["P2"].position_pay, out["P3"].position_pay) == (34380, 20628, 0)
    assert (out["P1"].gross, out["P2"].gross, out["P3"].gross) == (234380, 220628, 200000)
    assert (out["P1"].amount, out["P2"].amount, out["P3"].amount) == (234000, 221000, 200000)


def test_position_unit_is_zero_without_points_or_allowance():
    funding = FundingInput(total_months="2", base_share="0.5", position_months="1")
    out, result = rows([emp("P1", position="長"), emp("P3")], funding=funding)   # 管理職手当が0
    assert result.position_unit_price == 0 and out["P1"].position_pay == 0
    zero = COEF.model_copy(update={"position_points": {"B": {1: "0", 2: "0"}}})
    out, result = rows([emp("P1", position="長", manager_allowance=30000)], coef=zero, funding=funding)
    assert result.position_unit_price == 0 and out["P1"].position_pay == 0


def test_unknown_position_is_calc_error():
    with pytest.raises(CalcError):
        calculate([emp("P1", position="謎")], [place("P1")], COEF, FUND)


# ---- 精勤分配金の控除 ----

def test_distribution_is_deducted_at_the_same_rates_without_clipping():
    # B の減点 50 を A が受ける。分配額 = 千円丸め(50 × 1000) = 50000、A の計 250000、単価 1000。
    # 控除は 10％・勤続4か月・未出勤0.05。
    # 計：① 25000、② 250000×2÷6 = 83333.33 → 83333、③ 12500。支給額 = 千円丸め(129167) = 129000。
    # 分配額：① 5000、② 50000×2÷6 = 16666.67 → 16667、③ 2500。精勤分配金 = 千円丸め(50000 − 24167 = 25833) = 26000。
    # 本支給額 = 129000 − 26000 = 103000。
    out, _ = rows([emp("A", absence_hundredths="10", service_months=4, attendance_loss_rate="0.05"),
                   emp("B", att="0.5")])
    a = out["A"]
    assert (a.deduction_absence, a.deduction_service, a.deduction_attendance) == (25000, 83333, 12500)
    assert (a.amount, a.distribution_yen, a.distribution_net, a.main_pay) == (129000, 50000, 26000, 103000)


def test_distribution_net_can_be_negative_and_is_not_clipped():
    # 欠勤150％：分配額 50000 − ①(75000) = −25000 → 千円丸めで −25000。支給額は 0。本支給額 = 0 − (−25000) = 25000。
    out, _ = rows([emp("A", absence_hundredths="150"), emp("B", att="0.5")])
    a = out["A"]
    assert (a.amount, a.distribution_net, a.main_pay) == (0, -25000, 25000)


# ---- 手入力の職員 ----

def test_fixed_employee_is_outside_points_price_and_fund():
    # F は等級も考課もない。額 5000000、精勤分配金 30000、特別加給金 20000。点数・単価・財源には入らない。
    f = fixed("F", fixed_amount=5000000, fixed_distribution=30000, special_allowance=20000)
    out, result = rows([emp("A"), emp("B", att="0.5"), f], [place("A"), place("B"), Placement(emp_id="F")])
    assert result.unit_price == 1000 and result.total_base_pay == 200000 and Fraction(result.total_points) == 200
    # A は B の減点 50 を 1人で受ける（F は職場に数えない）
    assert out["A"].addition == "50.0"
    row = out["F"]
    assert (row.fixed, row.amount, row.distribution_net, row.main_pay, row.total_pay) == (
        True, 5000000, 30000, 4970000, 5020000)
    assert (row.group, row.status, row.base_pay, row.points, row.deduction_absence) == (None, "確定", 0, "0.0", 0)
    # 総支給は手入力を含み、財源側の支給額の合計は含まない
    assert result.total_amount == 250000 + 150000
    assert result.total_pay == 400000 + 5020000


def test_fixed_member_counts_in_the_group_and_gets_no_reduction():
    # B の減点 50 を A と F（職場の母数に数える）で 25.0 ずつ分ける。F の分配金は手入力のまま。
    f = fixed("F", fixed_amount=5000000, fixed_distribution=30000, distribution_member=True)
    out, result = rows([emp("A"), emp("B", att="0.5"), f], [place("A"), place("B"), place("F")])
    assert out["A"].addition == "25.0" and out["A"].amount == 225000      # 100000 + 125 × 1000
    assert out["F"].distribution_net == 30000 and out["F"].reduction == "0.0" and out["F"].group == "X"
    assert result.group_shares == {"X": "25.0"} and Fraction(result.unallocated_points) == 0
    assert result.unit_price == 1000


def test_fixed_member_with_low_attendance_is_not_a_receiver():
    f = fixed("F", fixed_amount=5000000, distribution_member=True, attendance_coefficient="0.5")
    out, _ = rows([emp("A"), emp("B", att="0.5"), f], [place("A"), place("B"), place("F")])
    assert out["A"].addition == "50.0" and out["F"].reduction == "0.0"   # F に減点はなく、受け手にもならない


def test_fixed_member_without_group_is_an_error_but_non_member_is_fine():
    with pytest.raises(CalcError, match="職場がない"):
        calculate([emp("A"), fixed("F", fixed_amount=1, distribution_member=True)],
                  [place("A"), Placement(emp_id="F")], COEF, FUND)
    out, _ = rows([emp("A"), fixed("F", fixed_amount=1)], [place("A"), Placement(emp_id="F")])
    assert out["F"].amount == 1


def test_held_fixed_employee_has_no_amount():
    f = fixed("F", fixed_amount=5000000, special_allowance=1000)
    out, result = rows([emp("A"), f], [place("A"), Placement(emp_id="F", held=True)])
    row = out["F"]
    assert (row.status, row.amount, row.main_pay, row.total_pay, row.distribution_net) == ("保留", None, None, None, None)
    assert result.total_pay == out["A"].total_pay


def test_held_person_has_no_payments():
    out, _ = rows([emp("A"), emp("H", special_allowance=1000)], [place("A"), Placement(emp_id="H", held=True)])
    h = out["H"]
    assert (h.amount, h.gross, h.main_pay, h.total_pay, h.distribution_net) == (None, None, None, None, None)
    assert (h.position_pay, h.deduction_absence, h.deduction_service, h.deduction_attendance) == (0, 0, 0, 0)


def test_total_basis_ignores_fixed():
    assert total_basis([emp("A", treatment_pay=500), fixed("F", fixed_amount=1)], COEF) == 100500


# ---- 分配点の手入力 ----

def test_group_share_override_is_used_as_is():
    # X の受け手 A、B に 12.5 点ずつ。C の減点 50 との差は「配分できなかった点数」に足さない。
    # 分配金 = 千円丸め(12.5 × 1000) = 千円丸め(12500) = 13000。
    coef = COEF.model_copy(update={"group_share_overrides": {"X": "12.5"}})
    people = [emp("A"), emp("B"), emp("C", att="0.5"), emp("D", group="Y"), emp("E", group="Y", att="0.5")]
    placements = [place("A"), place("B"), place("C"), place("D", "Y"), place("E", "Y")]
    out, result = rows(people, placements, coef=coef)
    assert (out["A"].addition, out["A"].addition_exact, out["A"].distribution_yen) == ("12.5", "12.5", 13000)
    assert out["B"].addition == "12.5" and out["C"].addition == "0.0"
    # Y は通常どおり：E の減点 50 を D が受ける。
    assert out["D"].addition == "50.0" and Fraction(result.unallocated_points) == 0
    assert result.group_shares == {"X": "12.5", "Y": "50.0"}
    step = next(t for t in out["A"].trace if t.name == "再分配")
    assert "手入力の分配点" in step.value
    assert "手入力" not in next(t for t in out["D"].trace if t.name == "再分配").value


def test_override_keeps_receiver_conditions():
    coef = COEF.model_copy(update={"group_share_overrides": {"X": "12.5"}})
    out, result = rows([emp("A", att="0.5"), emp("B")], coef=coef)
    assert out["A"].addition == "0.0" and out["B"].addition == "12.5"
    assert Fraction(result.unallocated_points) == 0   # A の減点 50 も手入力の結果として扱う


def test_override_holds_exact_fraction():
    coef = COEF.model_copy(update={"group_share_overrides": {"X": "100/3"}})
    out, _ = rows([emp("A"), emp("B", att="0.5")], coef=coef)
    assert Fraction(out["A"].addition_exact) == Fraction(100, 3) == Fraction(out["A"].addition)


# ---- トレース ----

def test_trace_has_steps_6_7_9():
    out, _ = rows([emp("A", special_allowance=1000)])
    assert [t.step for t in out["A"].trace] == [1, 2, 3, 3, 4, 5, 6, 7, 8, 9]
    names = {t.step: t.name for t in out["A"].trace}
    assert names[6] == "役付加給" and names[7] == "控除" and names[9] == "内訳"


# ---- もしも計算（whatif）は新しい計算でも仲間の分配金の変化を正しく返す ----

def test_whatif_with_deductions_uses_the_distribution_before_deductions():
    from engine.whatif import choice_effects
    # X：A（欠勤10％あり）と B（減点50）、Y：C。単価 1000（3人：点数300、原資300000）。
    # C を X へ置くと、B の減点 50 を A と C が 25.0 点（25000円）ずつ受ける。A の分配額は 50000 → 25000（控除前）。
    staff = [emp("A", absence_hundredths="10"), emp("B", att="0.5"), emp("C", group="Y")]
    placements = [place("A"), place("B"), place("C", "Y")]
    effects = choice_effects(staff, placements, COEF, FUND, "C", ["X", "Y"])
    x = effects["X"]
    assert x.self_distribution_yen == 25000
    assert [(g.group, g.peers, g.peer_delta_yen) for g in x.groups] == [("X", 1, -25000), ("Y", 0, 0)]
    assert effects["Y"].self_distribution_yen == 0


def test_whatif_ignores_fixed_members_distribution():
    from engine.whatif import choice_effects
    f = fixed("F", fixed_amount=1000000, fixed_distribution=30000, distribution_member=True)
    staff = [emp("A"), emp("B", att="0.5"), emp("C", group="Y"), f]
    placements = [place("A"), place("B"), place("C", "Y"), place("F")]
    effects = choice_effects(staff, placements, COEF, FUND, "C", ["X"])
    # X の受け手は A と C。母数に F が入るので B の減点 50 は A、C、F で割る（16.7 点）。
    assert effects["X"].self_distribution_yen == 17000   # 16.7 × 単価（点数300、原資300000 → 1000）= 16700 → 17000
    assert [(g.peers, g.peer_delta_yen) for g in effects["X"].groups][0] == (1, 17000 - 25000)   # 元は A と F で 25.0 点ずつ（25000円）


def test_point_grade_is_used_for_points_only():
    """考課期間の等級が基本給の等級と違う人。点数は考課期間の等級、基本給は今の等級で引く。"""
    coef = COEF.model_copy(update={"grade_points": {1: {"B": "100"}, 2: {"B": "50"}}})
    both = emp("A", point_grade=2)
    result = calculate([both], [place("A")], coef, FUND)
    assert result.employees[0].points == "50.0"
    assert result.employees[0].base_pay == 100000
