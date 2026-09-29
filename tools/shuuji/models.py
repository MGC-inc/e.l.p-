"""週次MT用PDF（/shuuji新方式）のデータモデル。

Notionから読んだ生データ（DailyRecord・DealRecord）と、集計後の値
（MemberWeekStats・AgencyWeekStats）を分離する。集計・判定ロジックは
aggregate.py が担い、ここには入出力に使う入れ物の定義だけを置く。
"""
from __future__ import annotations

from dataclasses import dataclass, field

ROLE_APPOINTER = "appointer"
ROLE_CLOSER = "closer"

RESULT_CONTRACT = "契約"
RESULT_COOLING_OFF = "クーリングオフ"
RESULT_REJECTED = "審査落ち"
# 契約以外の確定結果（保留は契約に至らなかったものとして扱う運用: 週次実績配信スキルと同じ前提）
RESULT_NON_CONTRACT = {"保留", "失注", RESULT_COOLING_OFF, RESULT_REJECTED, "キャンセル"}


@dataclass
class MemberInfo:
    """メンバー1人の基本情報（メンバーDB相当。現状は複数DBに分散しているものを1つにまとめた入れ物）。"""

    name: str
    role: str  # ROLE_APPOINTER | ROLE_CLOSER
    agency: str
    is_admin: bool = False
    is_active: bool = True


@dataclass
class DailyRecord:
    """📉営業部実績DBの1行（1人×1日）。活動量のみ。"""

    member: str
    date: str  # YYYY-MM-DD
    visit: float = 0.0      # 訪問数
    home: float = 0.0       # 在宅数
    face: float = 0.0       # 対面数
    target: float = 0.0     # 対象数
    talk: float = 0.0       # 対話数
    appt: float = 0.0       # アポ数
    is_working: bool = True  # 出勤状況が「休み」以外


@dataclass
class DealRecord:
    """DB商談分析＆アポ分析の1行（1商談）。"""

    closer: str
    appointer: str
    result: str
    deal_date: str  # YYYY-MM-DD
    agency: str
    revenue: float = 0.0       # 売上金額（契約のみ意味を持つ）
    neck_reason: str = ""      # ネック・保留理由（振り返りページ用）


@dataclass
class MetricValue:
    """項目1件分の「自分／全体平均／平均比／判定」。"""

    label: str            # 表示名（例: "アポ数"）
    unit: str              # "件" | "%" | "万円"
    self_value: float
    baseline: float | None      # 同役割全員の平均（率は合算率）。算出不能ならNone
    ratio_pct: float | None     # 平均比（%）。判定対象外ならNone
    judgment: str | None        # "大幅に低い"|"やや低い"|"平均"|"好調"|"母数少(参考)"|None
    low_is_better: bool = False  # クーリングオフ率・審査落ち率など、小さいほど良い項目
    comment_eligible: bool = True  # 複合項目（訪問→アポ通算・商談→契約(計)）はFalse
    sample_size: int | None = None  # 通過率の分母件数（3件未満判定用）


@dataclass
class MemberWeekStats:
    """個人×週の集計結果。"""

    member: str
    role: str
    agency: str

    # 活動量（週合計）
    visit: float = 0.0
    home: float = 0.0
    face: float = 0.0
    target: float = 0.0
    talk: float = 0.0
    appt: float = 0.0

    # 商談・契約（クローザーのみ自アポ/他アポ別。アポインターは meet_own=meet, meet_other=0）
    meet_own: int = 0
    meet_other: int = 0
    win_own: int = 0
    win_other: int = 0

    # 結果
    cooling_off: int = 0
    rejected: int = 0
    revenue: float = 0.0  # 万円

    # 月進捗（今月MTD。週進捗と別軸で保持）
    month_meet: int = 0
    month_win: int = 0
    month_revenue: float = 0.0

    is_working_this_week: bool = False  # 稼働人数の判定に使う（週内に1日でも実績入力があるか）

    # 振り返り用（直近の失注・保留の理由。クローザーのみ使う）
    recent_neck_reasons: list[str] = field(default_factory=list)

    @property
    def meet(self) -> int:
        return self.meet_own + self.meet_other

    @property
    def win(self) -> int:
        return self.win_own + self.win_other


@dataclass
class AgencyWeekStats:
    """代理店×週の集計結果（メンバー合算＋1人当たり）。"""

    agency: str
    members: list[MemberWeekStats]

    @property
    def working_count(self) -> int:
        return sum(1 for m in self.members if m.is_working_this_week)
