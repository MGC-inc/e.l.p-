"""Phase 1検証用のサンプルデータ（Notion接続なしでaggregate.py／PDF生成を確認する）。

数字は「今川のアポ率が明確に赤判定になる」「門田は好調になる」等、判定ロジックを
手計算で検証できるように意図的に作ってある（render_sample.pyのassertで突き合わせる）。
"""
from __future__ import annotations

from datetime import date, timedelta

from .models import DailyRecord, DealRecord, MemberInfo, ROLE_APPOINTER, ROLE_CLOSER

# 対象週: 2026-09-14(月)〜2026-09-20(日)。MTは2026-09-23(水)想定（「先週」を対象週とする）
WEEK_START = "2026-09-14"
WEEK_END = "2026-09-20"
MONTH_START = "2026-09-01"
MONTH_END = "2026-09-23"

MEMBERS = [
    MemberInfo(name="今川", role=ROLE_CLOSER, agency="自社E.L.P"),
    MemberInfo(name="門田", role=ROLE_CLOSER, agency="自社E.L.P"),
    MemberInfo(name="岡野", role=ROLE_APPOINTER, agency="自社E.L.P"),
    MemberInfo(name="柚木", role=ROLE_APPOINTER, agency="TRYGROUP"),
    MemberInfo(name="安達", role=ROLE_APPOINTER, agency="TRYGROUP"),
]


def _weekdays(start_iso: str, n: int = 5) -> list[str]:
    start = date.fromisoformat(start_iso)
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _spread(total: float, n: int) -> list[float]:
    """totalをn日にできるだけ均等に配分する整数リスト（合計は必ずtotalに一致）。"""
    base = total // n
    rem = int(total - base * n)
    out = [base] * n
    for i in range(rem):
        out[i] += 1
    return out


# 週合計（今川はアポ率を意図的に低くし、門田は高くしてある。検証用）
WEEK_TOTALS = {
    "今川": dict(visit=50, home=30, face=20, target=15, talk=10, appt=2),
    "門田": dict(visit=50, home=30, face=20, target=15, talk=10, appt=8),
    "岡野": dict(visit=40, home=25, face=15, target=10, talk=8, appt=5),
    "柚木": dict(visit=30, home=20, face=12, target=9, talk=7, appt=4),
    "安達": dict(visit=35, home=22, face=14, target=10, talk=8, appt=5),
}


def build_daily_records() -> list[DailyRecord]:
    records: list[DailyRecord] = []
    days = _weekdays(WEEK_START, 5)
    for name, totals in WEEK_TOTALS.items():
        per_day = {k: _spread(v, 5) for k, v in totals.items()}
        for i, d in enumerate(days):
            records.append(DailyRecord(
                member=name, date=d, is_working=True,
                visit=per_day["visit"][i], home=per_day["home"][i], face=per_day["face"][i],
                target=per_day["target"][i], talk=per_day["talk"][i], appt=per_day["appt"][i],
            ))
    return records


def build_deal_records() -> list[DealRecord]:
    deals: list[DealRecord] = []

    # 今川（クローザー・自社E.L.P）: 週5件（自アポ3・他アポ2）。契約2（自アポのみ）・保留1・失注1・クーリングオフ1
    deals += [
        DealRecord(closer="今川", appointer="今川", result="契約", deal_date="2026-09-15",
                   agency="自社E.L.P", revenue=65),
        DealRecord(closer="今川", appointer="今川", result="契約", deal_date="2026-09-16",
                   agency="自社E.L.P", revenue=70),
        DealRecord(closer="今川", appointer="今川", result="保留", deal_date="2026-09-17",
                   agency="自社E.L.P", neck_reason="ハウスメーカーの保証と被る"),
        DealRecord(closer="今川", appointer="門田", result="失注", deal_date="2026-09-18",
                   agency="自社E.L.P", neck_reason="予算が合わない"),
        DealRecord(closer="今川", appointer="門田", result="クーリングオフ", deal_date="2026-09-19",
                   agency="自社E.L.P", revenue=68),
    ]

    # 門田（クローザー・自社E.L.P）: 週5件（自アポ2・他アポ3）。契約4・保留1
    deals += [
        DealRecord(closer="門田", appointer="門田", result="契約", deal_date="2026-09-15",
                   agency="自社E.L.P", revenue=62),
        DealRecord(closer="門田", appointer="門田", result="契約", deal_date="2026-09-16",
                   agency="自社E.L.P", revenue=64),
        DealRecord(closer="門田", appointer="今川", result="契約", deal_date="2026-09-17",
                   agency="自社E.L.P", revenue=66),
        DealRecord(closer="門田", appointer="今川", result="契約", deal_date="2026-09-18",
                   agency="自社E.L.P", revenue=63),
        DealRecord(closer="門田", appointer="今川", result="保留", deal_date="2026-09-19",
                   agency="自社E.L.P", neck_reason="セキスイの保証があるから"),
    ]

    # 岡野（アポインター・自社E.L.P）: 本人アポの商談5件。契約3・失注2
    for i, result in enumerate(["契約", "契約", "契約", "失注", "失注"]):
        deals.append(DealRecord(closer="今川" if i % 2 == 0 else "門田", appointer="岡野", result=result,
                                 deal_date=f"2026-09-1{5+i}", agency="自社E.L.P",
                                 revenue=60 if result == "契約" else 0))

    # 柚木（アポインター・TRYGROUP）: 本人アポの商談3件。契約1・保留2
    for i, result in enumerate(["契約", "保留", "保留"]):
        deals.append(DealRecord(closer="安達", appointer="柚木", result=result,
                                 deal_date=f"2026-09-1{6+i}", agency="TRYGROUP",
                                 revenue=55 if result == "契約" else 0))

    # 安達（アポインター・TRYGROUP）: 本人アポの商談4件。契約2・クーリングオフ1・失注1
    for i, result in enumerate(["契約", "契約", "クーリングオフ", "失注"]):
        deals.append(DealRecord(closer="柚木", appointer="安達", result=result,
                                 deal_date=f"2026-09-1{5+i}", agency="TRYGROUP",
                                 revenue=58 if result == "契約" else 0))

    # 月間分（月初〜週の前まで）を少し追加し、月進捗が週進捗と別軸であることも検証する
    deals += [
        DealRecord(closer="今川", appointer="今川", result="契約", deal_date="2026-09-05",
                   agency="自社E.L.P", revenue=67),
        DealRecord(closer="門田", appointer="門田", result="契約", deal_date="2026-09-06",
                   agency="自社E.L.P", revenue=61),
        DealRecord(closer="安達", appointer="岡野", result="契約", deal_date="2026-09-04",
                   agency="自社E.L.P", revenue=59),
    ]

    return deals
