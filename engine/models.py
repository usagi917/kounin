"""全層で使う型。金額の型は画面と保存だけが読み、Claude へ渡さない（outbound.py が守る）。"""

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SegmentKind = Literal["通常", "新卒ローテ", "応援"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Segment(Strict):
    group: str
    start: date | None = None
    end: date | None = None
    kind: SegmentKind = "通常"


class Employee(Strict):
    emp_id: str
    display_name: str
    employment_type: str | None = None
    job_type: str | None = None
    segments: list[Segment]
    table_no: int | None = None
    grade: int | None = None
    point_grade: int | None = None   # 考課期間の等級。今の等級と違うときだけ入れる。点数はこちらで引く
    step: int | None = None
    adjustment_pay: int = 0
    rank: str | None = None
    attendance_coefficient: str = "1"
    hire_date: date | None = None
    treatment_pay: int = 0                  # 処遇改善。計算の基礎に入る
    position: str | None = None             # 職位
    manager_allowance: int = 0              # 管理職手当。役付財源の基礎
    absence_hundredths: str = "0"           # 欠勤等の率。100分の N
    service_months: int | None = None       # 勤続月数。None は満了扱い
    attendance_loss_rate: str = "0"         # 未出勤率
    special_allowance: int = 0              # 特別加給金
    fixed_amount: int | None = None         # 手入力の支給額。あれば計算の対象外
    fixed_distribution: int = 0             # 手入力の精勤分配金
    distribution_member: bool = False       # 手入力の職員を職場の分配の母数に数えるか


class Period(Strict):
    id: str
    start: date
    end: date


class NormalScope(Strict):
    employment_types: list[str]
    has_transfer: bool = False


class ExcludeSegment(Strict):
    kind: SegmentKind
    max_months: int | None = None


class UndecidedRange(Strict):
    id: str
    kind: SegmentKind
    min_months: int
    judgment: str


class RuleSource(Strict):
    type: Literal["regulation", "judgment"]
    quote: str | None = None
    id: str | None = None
    role: str | None = None


class Effective(Strict):
    from_: str = Field(alias="from")
    to: str | None = None


class RuleWhen(Strict):
    has_transfer: bool | None = None
    has_rotation: bool | None = None
    employment_type: str | None = None
    tenure_months_at_eval_end: str | None = None


class RuleThen(Strict):
    group_basis: Literal["longest_tenure", "eval_end"] | None = None
    grade: str | None = None


class Rule(Strict):
    id: str
    org_id: str
    version: int = 1
    status: Literal["draft", "approved", "retired"]
    effective: Effective
    when: RuleWhen
    exclude_segments: list[ExcludeSegment] = []
    then: RuleThen
    undecided: list[UndecidedRange] = []
    sources: list[RuleSource] = []
    text: str = ""


class Category(StrEnum):
    NORMAL = "通常ルール適用"
    EXCEPTION = "例外適用"
    MISSING = "情報不足"
    UNREGISTERED = "判断未登録"
    CONFLICT = "競合"


class JudgeResult(Strict):
    emp_id: str
    category: Category
    group: str | None = None
    eval_end_group: str | None = None
    rule_ids: list[str] = []
    reason_code: str = ""
    failed_conditions: list[str] = []
    normal_scope_failed: list[str] = []
    missing: list[str] = []
    undecided_id: str | None = None
    judgment_id: str | None = None


class Decision(Strict):
    """個別判断。その期のその人にだけ効く（PLAN §3.7）。"""
    id: str
    emp_id: str
    group: str


class Coefficients(Strict):
    """係数は全部ルールパックに置く。小数は文字列で持つ。"""
    base_pay: dict[int, dict[int, dict[int, int]]]   # 表 No → 等級 → 号俸 → 円
    grade_points: dict[int, dict[str, str]]          # 等級 → 考課ランク → 基本ポイント
    job_coefficients: dict[str, str]                 # 職種 → 係数
    rank_coefficients: dict[str, str]                # 考課ランク → 考課係数
    position_classes: dict[str, int] = {}            # 職位 → 区分
    position_points: dict[str, dict[int, str]] = {}  # 考課ランク → 区分 → 役付ポイント
    full_service_months: int = 6                     # 満了とする勤続月数
    group_share_overrides: dict[str, str] = {}       # 職場 → 1人当たりの分配点（手入力）


class FundingInput(Strict):
    total_months: str   # 総月数。"2.2" や "1234/567" の形
    base_share: str     # 基本給連動へ回す配分率
    position_months: str = "0"   # 役付財源の月数（管理職手当に掛ける）


class Placement(Strict):
    """職員をどの職場で数えるか。判定の結果から作る。"""
    emp_id: str
    group: str | None = None
    held: bool = False
    candidates: list[str] = []
    rule_ids: list[str] = []
    rank: str | None = None   # 考課の上書き。None なら職員の rank を使う


class TraceStep(Strict):
    step: int
    name: str
    value: str
    rule_ids: list[str] = []


class EmployeeCalc(Strict):
    emp_id: str
    group: str | None
    status: Literal["確定", "暫定", "保留"]
    base_pay: int
    points: str
    reduction: str
    addition: str
    addition_exact: str       # 丸める前の分配点（SPEC §9 の確認に使う）
    final_points: str
    base_linked: int
    amount_part: int
    distribution_yen: int     # 足した点数の金額換算
    amount: int | None        # 支給額。保留は None
    treatment_pay: int = 0
    position_pay: int = 0     # 役付加給
    gross: int | None = None  # 計（控除の前）。保留と手入力は None
    deduction_absence: int = 0
    deduction_service: int = 0
    deduction_attendance: int = 0
    distribution_net: int | None = None   # 精勤分配金（控除後）
    main_pay: int | None = None           # 本支給額 = 支給額 − 精勤分配金
    special_allowance: int = 0
    total_pay: int | None = None          # 支給計 = 支給額 + 特別加給金
    fixed: bool = False                   # 手入力の職員
    rule_ids: list[str] = []
    trace: list[TraceStep] = []


class CalcResult(Strict):
    ok: bool
    error: str | None = None
    total_base_pay: int = 0   # 計算の基礎（基本給 + 調整給 + 処遇改善）の合計。手入力の職員は含めない
    base_months: str = "0"
    amount_months: str = "0"
    amount_fund: int = 0
    total_points: str = "0"
    unit_price: int = 0
    unallocated_points: str = "0"
    group_shares: dict[str, str] = {}   # 職場 → 1人当たりの分配点
    total_amount: int = 0     # 手入力でない職員の支給額の合計
    total_pay: int = 0        # 手入力を含む支給計の合計
    position_unit_price: int = 0
    rounding_residual: int | None = None
    employees: list[EmployeeCalc] = []


class GroupEffect(Strict):
    group: str
    peers: int              # 分配金が変わる仲間の人数
    peer_delta_yen: int     # 仲間1人当たりの分配金の変化


class ChoiceEffect(Strict):
    choice: str
    self_distribution_yen: int
    groups: list[GroupEffect] = []


class ExcelDiff(Strict):
    changed: int
    total_yen: int
