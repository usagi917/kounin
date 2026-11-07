"""賞与の計算。純粋関数で、金額は整数の円、点数は Fraction で持つ。"""

from collections.abc import Mapping
from fractions import Fraction

from engine import rounding as r
from engine.models import (CalcResult, Coefficients, Employee, EmployeeCalc, FundingInput, Placement,
                           TraceStep)


class CalcError(ValueError):
    """表にない等級など、入力の誤り。理由を日本語の文にする。"""


def _text(value: Fraction, digits: int) -> str:
    """digits 桁で割り切れる値は小数で、そうでなければ分数で書く。"""
    scaled = value * 10 ** digits
    if scaled.denominator != 1:
        return str(value)
    sign, whole = ("-" if scaled < 0 else ""), abs(scaled.numerator)
    if digits == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole // 10 ** digits}.{whole % 10 ** digits:0{digits}d}"


def _need(emp: Employee, name: str, value):
    if value is None:
        raise CalcError(f"職員 {emp.emp_id}: {name}が未入力")
    return value


def _find(table: Mapping, key, emp: Employee, label: str):
    if key not in table:
        raise CalcError(f"職員 {emp.emp_id}: {label}「{key}」が表にない")
    return table[key]


def is_fixed(emp: Employee) -> bool:
    """手入力の支給額を持つ職員は、点数・単価・財源の計算に入れない。"""
    return emp.fixed_amount is not None


class _Row:
    """職員1人分の途中経過。"""

    def __init__(self, emp: Employee, placement: Placement, coef: Coefficients):
        self.emp, self.placement = emp, placement
        self.held = placement.held
        self.fixed = is_fixed(emp)
        member = not self.fixed or emp.distribution_member   # 職場の分配の母数に数える職員
        if not self.held and placement.group is None and member:
            raise CalcError(f"職員 {emp.emp_id}: 保留でないのに職場がない")
        self.group = None if self.held or not member else placement.group
        self.attendance = r.to_fraction(emp.attendance_coefficient)
        self.manual = False
        self.addition = self.addition_exact = Fraction(0)
        if self.fixed:   # 表の等級や考課がなくてもよい。点数も減点もない
            self.base_pay = self.basis = 0
            self.points = self.reduction = self.pos_points = self.rank_coef = Fraction(0)
            self.receiver = member and not self.held and self.attendance >= 1
            return
        grade = emp.point_grade if emp.point_grade is not None else _need(emp, "等級", emp.grade)
        self.base_pay = base_pay_of(emp, coef)
        self.basis = self.base_pay + emp.treatment_pay
        rank = placement.rank or _need(emp, "考課", emp.rank)
        job = _find(coef.job_coefficients, _need(emp, "職種", emp.job_type), emp, "職種")
        base_point = _find(_find(coef.grade_points, grade, emp, "等級（ポイント表）"), rank, emp, "考課")
        self.rank_coef = r.to_fraction(_find(coef.rank_coefficients, rank, emp, "考課（係数表）"))
        self.points = r.points(r.to_fraction(base_point) * r.to_fraction(job))
        self.pos_points = Fraction(0)
        if emp.position is not None:
            klass = _find(coef.position_classes, emp.position, emp, "職位")
            table = _find(coef.position_points, rank, emp, "考課（役付ポイント表）")
            self.pos_points = r.to_fraction(_find(table, klass, emp, "職位の区分"))
        self.reduction = (r.points(self.points * (1 - self.attendance))
                          if self.attendance < 1 else Fraction(0))
        self.receiver = not self.held and self.attendance >= 1


def base_pay_of(emp: Employee, coef: Coefficients) -> int:
    """手順1。考課の有無に依らない。基本給 + 調整給。"""
    table = _find(coef.base_pay, _need(emp, "表No", emp.table_no), emp, "表No")
    steps = _find(table, _need(emp, "等級", emp.grade), emp, "等級")
    return _find(steps, _need(emp, "号俸", emp.step), emp, "号俸") + emp.adjustment_pay


def basis_of(emp: Employee, coef: Coefficients) -> int:
    """計算の基礎 = 基本給 + 調整給 + 処遇改善。財源と基本給連動はこの値を使う。"""
    return base_pay_of(emp, coef) + emp.treatment_pay


def total_basis(staff: list[Employee], coef: Coefficients) -> int:
    """手入力の職員を除く、計算の基礎の合計。"""
    return sum(basis_of(emp, coef) for emp in staff if not is_fixed(emp))


def _rows(staff: list[Employee], placements: list[Placement], coef: Coefficients) -> list[_Row]:
    by_id: dict[str, Placement] = {}
    for placement in placements:
        if placement.emp_id in by_id:
            raise CalcError(f"職員 {placement.emp_id}: 配置が重複している")
        by_id[placement.emp_id] = placement
    ids = {emp.emp_id for emp in staff}
    if len(ids) != len(staff):
        raise CalcError("職員の ID が重複している")
    if missing := sorted(ids - by_id.keys()):
        raise CalcError(f"配置がない職員がいる: {', '.join(missing)}")
    if extra := sorted(by_id.keys() - ids):
        raise CalcError(f"職員にいない配置がある: {', '.join(extra)}")
    return [_Row(emp, by_id[emp.emp_id], coef) for emp in staff]


def _redistribute(rows: list[_Row], overrides: Mapping[str, str]) -> tuple[dict[str, Fraction], Fraction]:
    """減点をグループ内の受け手へ再分配する。職場ごとの1人分と、配分できなかった点数を返す。

    overrides にある職場は、1人当たりの分配点を手入力の値のまま受け手に配る（丸めない）。
    減点の合計との差は手入力の結果として扱い、配分できなかった点数に足さない。
    """
    shares: dict[str, Fraction] = {}
    unallocated = sum((row.reduction for row in rows if row.held), Fraction(0))
    for group in sorted({row.group for row in rows if row.group is not None}):
        members = [row for row in rows if row.group == group]
        receivers = [row for row in members if row.receiver]
        if group in overrides:
            shares[group] = share = r.to_fraction(overrides[group])
            for row in receivers:
                row.addition = row.addition_exact = share
                row.manual = True
            continue
        total = sum((row.reduction for row in members), Fraction(0))
        if not receivers:
            shares[group] = Fraction(0)
            unallocated += total
            continue
        shares[group] = r.points(total / len(receivers))
        for row in receivers:
            row.addition, row.addition_exact = shares[group], total / len(receivers)
    return shares, unallocated


def _status(row: _Row, candidate_groups: set[str]) -> str:
    if row.held:
        return "保留"
    return "暫定" if row.group in candidate_groups else "確定"


def _deductions(base: int, emp: Employee, full: int) -> tuple[int, int, int]:
    """手順7。①欠勤等（切り捨て）、②未勤務月数、③出勤率（②③は円丸め）。計にも精勤分配金にも同じ率を使う。"""
    amount = Fraction(base)
    absence = r.floor_yen(amount * r.to_fraction(emp.absence_hundredths) / 100)
    short = emp.service_months is not None and full > 0 and emp.service_months < full
    service = r.yen(amount * (full - emp.service_months) / full) if short else 0
    attendance = r.yen(amount * r.to_fraction(emp.attendance_loss_rate))
    return absence, service, attendance


class _Pay:
    """職員1人分の支給系の金額。保留の人は支給の金額が None で、控除と役付加給は0。"""

    def __init__(self, special: int, position_unit: int):
        self.special, self.position_unit = special, position_unit
        self.amount = self.gross = self.distribution_net = self.main_pay = self.total_pay = None
        self.position_pay = self.d_absence = self.d_service = self.d_attendance = 0


def _breakdown(pay: _Pay) -> str:
    return (f"本支給額 {pay.main_pay}円 / 精勤分配金 {pay.distribution_net}円 / "
            f"特別加給金 {pay.special}円（支給計 {pay.total_pay}円）")


def _trace(row: _Row, final: Fraction, unit_price: int, base_linked: int, part: int, pay: _Pay) -> list[TraceStep]:
    rules = row.placement.rule_ids
    where = "保留" if row.held else row.group
    emp = row.emp
    if row.fixed:
        return [TraceStep(step=8, name="支給額", value="保留" if row.held else f"{pay.amount}円（手入力）"),
                TraceStep(step=9, name="内訳", value="保留" if row.held else _breakdown(pay))]
    if row.held:
        position = "保留"
    elif emp.position:
        position = f"{emp.position}（{_text(row.pos_points, 1)} 点）× 単価 {pay.position_unit}円 = {pay.position_pay}円"
    else:
        position = "職位なし"
    treatment = f" + 処遇改善 {emp.treatment_pay}円" if emp.treatment_pay else ""
    received = f"{where}で {_text(row.addition, 1)} 点を受ける"
    return [
        TraceStep(step=1, name="基本給",
                  value=f"{row.base_pay}円（調整給 {emp.adjustment_pay}円を含む）{treatment}"),
        TraceStep(step=2, name="成績ポイント", value=_text(row.points, 1)),
        TraceStep(step=3, name="精勤係数の減点",
                  value=f"係数 {emp.attendance_coefficient} で {_text(row.reduction, 1)} 点"),
        TraceStep(step=3, name="再分配", value=f"手入力の分配点：{received}" if row.manual else received,
                  rule_ids=rules),
        TraceStep(step=4, name="額部分", value=f"{_text(final, 1)} 点 × 単価 {unit_price}円 = {part}円"),
        TraceStep(step=5, name="基本給連動分", value=f"{base_linked}円"),
        TraceStep(step=6, name="役付加給", value=position),
        TraceStep(step=7, name="控除", value="保留" if row.held else (
            f"欠勤等 {pay.d_absence}円、未勤務月数 {pay.d_service}円、出勤率 {pay.d_attendance}円")),
        TraceStep(step=8, name="支給額",
                  value="保留" if pay.amount is None else f"{pay.amount}円（千円未満を四捨五入）"),
        TraceStep(step=9, name="内訳", value="保留" if row.held else _breakdown(pay)),
    ]


def _pay(row: _Row, base_linked: int, part: int, distribution: int, position_unit: int, full: int) -> _Pay:
    """手順6、7、9。保留でなければ金額を入れる。"""
    emp = row.emp
    pay = _Pay(emp.special_allowance, position_unit)
    if row.held:
        return pay
    if row.fixed:
        pay.amount, pay.distribution_net = emp.fixed_amount, emp.fixed_distribution
    else:
        pay.position_pay = r.yen(row.pos_points * position_unit)
        pay.gross = base_linked + part + pay.position_pay
        pay.d_absence, pay.d_service, pay.d_attendance = _deductions(pay.gross, emp, full)
        pay.amount = r.thousand(pay.gross - pay.d_absence - pay.d_service - pay.d_attendance)
        pay.distribution_net = r.thousand_signed(distribution - sum(_deductions(distribution, emp, full)))
    pay.main_pay = pay.amount - pay.distribution_net
    pay.total_pay = pay.amount + pay.special
    return pay


def calculate(staff: list[Employee], placements: list[Placement], coefficients: Coefficients,
              funding: FundingInput) -> CalcResult:
    rows = _rows(staff, placements, coefficients)
    total_months = r.to_fraction(funding.total_months)
    base_months = r.months(total_months * r.to_fraction(funding.base_share))
    amount_months = total_months - base_months
    basis_total = sum(row.basis for row in rows)   # 手入力の職員は0
    head = dict(total_base_pay=basis_total, base_months=_text(base_months, 3),
                amount_months=_text(amount_months, 3))
    total_points = sum((row.points for row in rows), Fraction(0))   # 保留者の減点前の点数を含める
    if total_points == 0:
        return CalcResult(ok=False, error="成績ポイントの合計が0のため、単価を決められない", **head)
    amount_fund = r.yen(basis_total * amount_months)
    unit_price = r.yen(Fraction(amount_fund) / total_points)
    # 手順6：役付単価。財源は管理職手当の合計 × 役付の月数、分母は全職員の役付ポイントの合計
    pool = sum(row.emp.manager_allowance for row in rows if not row.fixed)
    position_total = sum((row.pos_points for row in rows), Fraction(0))
    position_unit = (r.yen(pool * r.to_fraction(funding.position_months) / position_total)
                     if position_total > 0 and pool > 0 else 0)
    shares, unallocated = _redistribute(rows, coefficients.group_share_overrides)
    candidates = {group for row in rows if row.held for group in row.placement.candidates}

    employees, total_amount, total_pay = [], 0, 0
    for row in rows:
        zero = Fraction(0)
        final = row.points - row.reduction + row.addition
        base_linked = r.yen(row.basis * base_months * row.rank_coef)
        part = r.yen(final * unit_price)
        # 手入力の職員は点数を持たない。分配の母数には数えるが、分配点は受けない
        distribution = 0 if row.fixed else r.thousand(row.addition * unit_price)
        pay = _pay(row, base_linked, part, distribution, position_unit, coefficients.full_service_months)
        if not row.fixed and pay.amount is not None:
            total_amount += pay.amount
        total_pay += pay.total_pay or 0
        employees.append(EmployeeCalc(
            emp_id=row.emp.emp_id, group=row.group, status=_status(row, candidates),
            base_pay=row.base_pay, points=_text(row.points, 1), reduction=_text(row.reduction, 1),
            addition=_text(zero if row.fixed else row.addition, 1),
            addition_exact=_text(zero if row.fixed else row.addition_exact, 1),
            final_points=_text(zero if row.fixed else final, 1), base_linked=base_linked,
            amount_part=part, distribution_yen=distribution, amount=pay.amount,
            treatment_pay=0 if row.fixed else row.emp.treatment_pay, position_pay=pay.position_pay,
            gross=pay.gross, deduction_absence=pay.d_absence, deduction_service=pay.d_service,
            deduction_attendance=pay.d_attendance, distribution_net=pay.distribution_net, main_pay=pay.main_pay,
            special_allowance=pay.special, total_pay=pay.total_pay, fixed=row.fixed,
            rule_ids=row.placement.rule_ids, trace=_trace(row, final, unit_price, base_linked, part, pay)))
    residual = (None if any(row.held for row in rows)
                else r.yen(basis_total * total_months) - total_amount)
    return CalcResult(ok=True, amount_fund=amount_fund, total_points=_text(total_points, 1),
                      unit_price=unit_price, unallocated_points=_text(unallocated, 1),
                      group_shares={group: _text(share, 1) for group, share in shares.items()},
                      total_amount=total_amount, total_pay=total_pay, position_unit_price=position_unit,
                      rounding_residual=residual, employees=employees, **head)
