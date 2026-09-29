"""週次MT用PDFの集計・判定ロジック（決定的コード。LLMに計算させない）。

依頼の3章「集計・判定ロジック」をそのまま実装する:
- 個人×週の値: 活動量=日次実績の合計、商談・契約系=商談分析DBの合計
- meet=meet_own+meet_other、win=win_own+win_other
- 月進捗=当月分を商談分析DBから自動集計、週進捗=先週分
- 代理店別集計=メンバー合算。稼働人数=先週に実績がある人数。ランキングは稼働人数で割った1人当たり
- 比較対象=同じ役割全員の平均。通過率は分子・分母を全員分合算して計算
- 判定(平均比): 70%未満=大幅に低い／90%未満=やや低い／110%超=好調／それ以外=平均
- クーリングオフ率・審査落ち率(対契約): 平均の1.5倍超=要注意／1.2倍超=やや多い
- 分母3件未満の率は判定しない（母数少・参考）。平均が0の項目も判定しない
- 自動コメント: 極端に低い項目を平均比の低い順に最大3件（先頭=最優先の改善項目）。
  該当なしならやや低い項目を列挙。複合項目（訪問→アポ通算・商談→契約(計)）は候補にしない
"""
from __future__ import annotations

from collections import defaultdict

from .models import (
    RESULT_CONTRACT,
    RESULT_COOLING_OFF,
    RESULT_REJECTED,
    ROLE_CLOSER,
    AgencyWeekStats,
    DailyRecord,
    DealRecord,
    MemberInfo,
    MemberWeekStats,
    MetricValue,
)

JUDGE_LOW = "大幅に低い"
JUDGE_SLIGHTLY_LOW = "やや低い"
JUDGE_AVERAGE = "平均"
JUDGE_GOOD = "好調"
JUDGE_LOW_SAMPLE = "母数少(参考)"

NEGATIVE_LOW = "要注意"
NEGATIVE_SLIGHTLY_HIGH = "やや多い"
NEGATIVE_NORMAL = "通常"

MIN_SAMPLE_SIZE = 3


def _in_range(date: str, start: str, end_inclusive: str) -> bool:
    return start <= date <= end_inclusive


def aggregate_member_week(
    member: MemberInfo,
    daily: list[DailyRecord],
    deals: list[DealRecord],
    week_start: str,
    week_end: str,
    month_start: str,
    month_end: str,
) -> MemberWeekStats:
    """1人分の個人×週の集計を行う。week_end・month_endは当日を含む（inclusive）。"""
    stats = MemberWeekStats(member=member.name, role=member.role, agency=member.agency)

    week_daily = [d for d in daily if d.member == member.name and _in_range(d.date, week_start, week_end)]
    for d in week_daily:
        stats.visit += d.visit
        stats.home += d.home
        stats.face += d.face
        stats.target += d.target
        stats.talk += d.talk
        stats.appt += d.appt
        if d.is_working:
            stats.is_working_this_week = True

    if member.role == ROLE_CLOSER:
        week_deals = [
            deal for deal in deals
            if deal.closer == member.name and _in_range(deal.deal_date, week_start, week_end)
        ]
        for deal in week_deals:
            is_own = deal.appointer == member.name
            if is_own:
                stats.meet_own += 1
            else:
                stats.meet_other += 1
            if deal.result == RESULT_CONTRACT:
                if is_own:
                    stats.win_own += 1
                else:
                    stats.win_other += 1
            if deal.result == RESULT_COOLING_OFF:
                stats.cooling_off += 1
            if deal.result == RESULT_REJECTED:
                stats.rejected += 1
            if deal.result == RESULT_CONTRACT:
                stats.revenue += deal.revenue
            if deal.result in ("保留", "失注") and deal.neck_reason:
                stats.recent_neck_reasons.append(deal.neck_reason)

        month_deals = [
            deal for deal in deals
            if deal.closer == member.name and _in_range(deal.deal_date, month_start, month_end)
        ]
    else:
        # アポインター: 本人のアポが商談・契約になった数（9章の仮置き）
        week_deals = [
            deal for deal in deals
            if deal.appointer == member.name and _in_range(deal.deal_date, week_start, week_end)
        ]
        for deal in week_deals:
            stats.meet_own += 1
            if deal.result == RESULT_CONTRACT:
                stats.win_own += 1
            if deal.result == RESULT_COOLING_OFF:
                stats.cooling_off += 1
            if deal.result == RESULT_REJECTED:
                stats.rejected += 1
            if deal.result == RESULT_CONTRACT:
                stats.revenue += deal.revenue

        month_deals = [
            deal for deal in deals
            if deal.appointer == member.name and _in_range(deal.deal_date, month_start, month_end)
        ]

    stats.month_meet = len(month_deals)
    stats.month_win = sum(1 for deal in month_deals if deal.result == RESULT_CONTRACT)
    stats.month_revenue = sum(deal.revenue for deal in month_deals if deal.result == RESULT_CONTRACT)

    return stats


def aggregate_agency(agency: str, members_stats: list[MemberWeekStats]) -> AgencyWeekStats:
    return AgencyWeekStats(agency=agency, members=[m for m in members_stats if m.agency == agency])


def _pooled_rate_baseline(peers: list[MemberWeekStats], numerator_fn, denominator_fn) -> tuple[float | None, int]:
    """通過率の全体平均＝分子・分母を全員分合算して計算（個々の率の単純平均ではない）。
    戻り値: (baseline率(%)またはNone, 分母合算件数)"""
    num = sum(numerator_fn(m) for m in peers)
    den = sum(denominator_fn(m) for m in peers)
    if den <= 0:
        return None, 0
    return 100.0 * num / den, int(den)


def _count_baseline(peers: list[MemberWeekStats], value_fn) -> float | None:
    """実数系の全体平均＝個々の値の単純平均。"""
    if not peers:
        return None
    values = [value_fn(m) for m in peers]
    mean = sum(values) / len(values)
    return mean


def _judge_high_is_good(ratio_pct: float | None, sample_size: int | None) -> str | None:
    if ratio_pct is None:
        return None
    if sample_size is not None and sample_size < MIN_SAMPLE_SIZE:
        return JUDGE_LOW_SAMPLE
    if ratio_pct < 70:
        return JUDGE_LOW
    if ratio_pct < 90:
        return JUDGE_SLIGHTLY_LOW
    if ratio_pct > 110:
        return JUDGE_GOOD
    return JUDGE_AVERAGE


def _judge_low_is_good(ratio_pct: float | None, sample_size: int | None) -> str | None:
    """クーリングオフ率・審査落ち率用。ratio_pct = 本人率 ÷ 平均率 × 100。"""
    if ratio_pct is None:
        return None
    if sample_size is not None and sample_size < MIN_SAMPLE_SIZE:
        return JUDGE_LOW_SAMPLE
    if ratio_pct > 150:
        return NEGATIVE_LOW
    if ratio_pct > 120:
        return NEGATIVE_SLIGHTLY_HIGH
    return NEGATIVE_NORMAL


def _metric_count(label: str, unit: str, self_value: float, peers: list[MemberWeekStats], value_fn,
                   comment_eligible: bool = True, judged: bool = True) -> MetricValue:
    """judged=False: クーリングオフ数・審査落ち数の実数など「参考表示のみ」の項目
    （良し悪しはレート側=クーリングオフ率・審査落ち率でのみ判定する。仕様どおり）。
    平均比の数字自体は参考として出すが、判定ラベルは付けない。"""
    baseline = _count_baseline(peers, value_fn)
    if baseline is None or baseline == 0:
        return MetricValue(label, unit, self_value, baseline, None, None, comment_eligible=comment_eligible)
    ratio = 100.0 * self_value / baseline
    judgment = _judge_high_is_good(ratio, None) if judged else None
    return MetricValue(label, unit, self_value, baseline, ratio, judgment, comment_eligible=comment_eligible)


def _metric_rate(label: str, self_num: float, self_den: float, peers: list[MemberWeekStats],
                  num_fn, den_fn, comment_eligible: bool = True) -> MetricValue:
    self_value = (100.0 * self_num / self_den) if self_den > 0 else 0.0
    baseline, pooled_den = _pooled_rate_baseline(peers, num_fn, den_fn)
    sample_size = int(self_den)
    if baseline is None or baseline == 0:
        return MetricValue(label, "%", self_value, baseline, None, None,
                            comment_eligible=comment_eligible, sample_size=sample_size)
    ratio = 100.0 * self_value / baseline
    judgment = _judge_high_is_good(ratio, sample_size)
    return MetricValue(label, "%", self_value, baseline, ratio, judgment,
                        comment_eligible=comment_eligible, sample_size=sample_size)


def _metric_negative_rate(label: str, self_num: float, self_den: float, peers: list[MemberWeekStats],
                           num_fn, den_fn) -> MetricValue:
    self_value = (100.0 * self_num / self_den) if self_den > 0 else 0.0
    baseline, pooled_den = _pooled_rate_baseline(peers, num_fn, den_fn)
    sample_size = int(self_den)
    if baseline is None or baseline == 0:
        return MetricValue(label, "%", self_value, baseline, None, None,
                            low_is_better=True, sample_size=sample_size)
    ratio = 100.0 * self_value / baseline
    judgment = _judge_low_is_good(ratio, sample_size)
    return MetricValue(label, "%", self_value, baseline, ratio, judgment,
                        low_is_better=True, sample_size=sample_size)


def build_metrics(member: MemberWeekStats, peers: list[MemberWeekStats]) -> list[MetricValue]:
    """1人分の全項目（活動量・商談契約・結果・通過率）をMetricValueのリストで返す。
    peersは自分を含む「同じ役割の全員」のMemberWeekStatsリスト。"""
    others = peers  # 比較対象=同じ役割全員（自分を含めて平均を取る。抜くと母数1人だけの週でNoneになりやすいため）
    m = member
    metrics: list[MetricValue] = []

    # 活動量
    metrics.append(_metric_count("訪問数", "件", m.visit, others, lambda x: x.visit))
    metrics.append(_metric_count("在宅数", "件", m.home, others, lambda x: x.home))
    metrics.append(_metric_count("対面数", "件", m.face, others, lambda x: x.face))
    metrics.append(_metric_count("対象数", "件", m.target, others, lambda x: x.target))
    metrics.append(_metric_count("対話数", "件", m.talk, others, lambda x: x.talk))
    metrics.append(_metric_count("アポ数", "件", m.appt, others, lambda x: x.appt))

    # 商談・契約
    metrics.append(_metric_count("商談(計)", "件", m.meet, others, lambda x: x.meet))
    metrics.append(_metric_count("契約(計)", "件", m.win, others, lambda x: x.win))
    if m.role == ROLE_CLOSER:
        metrics.append(_metric_count("商談(自アポ)", "件", m.meet_own, others, lambda x: x.meet_own))
        metrics.append(_metric_count("商談(他アポ)", "件", m.meet_other, others, lambda x: x.meet_other))
        metrics.append(_metric_count("契約(自アポ)", "件", m.win_own, others, lambda x: x.win_own))
        metrics.append(_metric_count("契約(他アポ)", "件", m.win_other, others, lambda x: x.win_other))

    # 結果
    metrics.append(_metric_count("クーリングオフ数", "件", m.cooling_off, others, lambda x: x.cooling_off,
                                  comment_eligible=False, judged=False))
    metrics.append(_metric_count("審査落ち数", "件", m.rejected, others, lambda x: x.rejected,
                                  comment_eligible=False, judged=False))
    metrics.append(_metric_count("実質売上", "万円", m.revenue, others, lambda x: x.revenue))

    # 通過率（訪問→在宅→対面→対象→対話→アポの各段階）
    metrics.append(_metric_rate("在宅率(訪問→在宅)", m.home, m.visit, others,
                                 lambda x: x.home, lambda x: x.visit))
    metrics.append(_metric_rate("対面率(在宅→対面)", m.face, m.home, others,
                                 lambda x: x.face, lambda x: x.home))
    metrics.append(_metric_rate("対象率(対面→対象)", m.target, m.face, others,
                                 lambda x: x.target, lambda x: x.face))
    metrics.append(_metric_rate("対話率(対象→対話)", m.talk, m.target, others,
                                 lambda x: x.talk, lambda x: x.target))
    metrics.append(_metric_rate("アポ率(対話→アポ)", m.appt, m.talk, others,
                                 lambda x: x.appt, lambda x: x.talk))
    # 複合項目（候補外）
    metrics.append(_metric_rate("訪問→アポ通算", m.appt, m.visit, others,
                                 lambda x: x.appt, lambda x: x.visit, comment_eligible=False))
    metrics.append(_metric_rate("商談→契約率(計)", m.win, m.meet, others,
                                 lambda x: x.win, lambda x: x.meet, comment_eligible=False))

    # クーリングオフ率・審査落ち率（対契約。少ない方が良い）
    metrics.append(_metric_negative_rate("クーリングオフ率", m.cooling_off, m.win, others,
                                          lambda x: x.cooling_off, lambda x: x.win))
    metrics.append(_metric_negative_rate("審査落ち率", m.rejected, m.win, others,
                                          lambda x: x.rejected, lambda x: x.win))

    return metrics


def build_auto_comment(metrics: list[MetricValue], max_items: int = 3) -> list[str]:
    """極端に低い項目を平均比の低い順に最大3件。該当なしならやや低い項目を列挙。
    複合項目（comment_eligible=False）は候補にしない。クーリングオフ率・審査落ち率は
    low_is_better=Trueなので「高いほど悪い」判定（要注意・やや多い）をそのまま拾う。"""

    def is_bad(mv: MetricValue, level: str) -> bool:
        if not mv.comment_eligible or mv.judgment is None:
            return False
        if mv.low_is_better:
            return mv.judgment == (NEGATIVE_LOW if level == "low" else NEGATIVE_SLIGHTLY_HIGH)
        return mv.judgment == (JUDGE_LOW if level == "low" else JUDGE_SLIGHTLY_LOW)

    def sort_key(mv: MetricValue) -> float:
        # low_is_better項目は比率が高いほど悪い→そのまま降順にしたいので符号反転
        return -mv.ratio_pct if mv.low_is_better else mv.ratio_pct

    severe = sorted([mv for mv in metrics if is_bad(mv, "low")], key=sort_key)
    candidates = severe
    if not candidates:
        candidates = sorted([mv for mv in metrics if is_bad(mv, "slight")], key=sort_key)

    comments = []
    for i, mv in enumerate(candidates[:max_items]):
        prefix = "【最優先の改善項目】" if i == 0 else "・"
        if mv.low_is_better:
            comments.append(
                f"{prefix}{mv.label} {mv.self_value:.1f}{mv.unit}"
                f"（平均{mv.baseline:.1f}{mv.unit}の{mv.ratio_pct:.0f}%・{mv.judgment}）"
            )
        else:
            comments.append(
                f"{prefix}{mv.label} {mv.self_value:.1f}{mv.unit}"
                f"（平均{mv.baseline:.1f}{mv.unit}比{mv.ratio_pct:.0f}%・{mv.judgment}）"
            )
    return comments


def build_ranking(all_members: list[MemberWeekStats], metric: str) -> list[tuple[int, str, float]]:
    """代理店別ランキング用: 稼働人数で割った1人当たりの値で、代理店を順位付けする。
    metricは MemberWeekStats の合算対象フィールド名（"appt"|"meet"|"win"|"visit"|"revenue"）。
    戻り値: [(順位, 代理店名, 1人当たり値), ...]（同率は同順位）。"""
    by_agency: dict[str, list[MemberWeekStats]] = defaultdict(list)
    for m in all_members:
        by_agency[m.agency].append(m)

    per_capita = []
    for agency, members in by_agency.items():
        working = sum(1 for m in members if m.is_working_this_week)
        if working <= 0:
            continue
        total = sum(getattr(m, metric) for m in members)
        per_capita.append((agency, total / working))

    per_capita.sort(key=lambda x: x[1], reverse=True)

    ranking: list[tuple[int, str, float]] = []
    rank = 0
    prev_value = None
    for i, (agency, value) in enumerate(per_capita):
        if prev_value is None or value != prev_value:
            rank = i + 1
        ranking.append((rank, agency, value))
        prev_value = value
    return ranking
