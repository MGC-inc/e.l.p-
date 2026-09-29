#!/usr/bin/env python3
"""Phase 1検証用CLI: サンプルデータで集計→判定→PDF生成を一気通貫で確認する。

Notionには一切接続しない（sample_data.pyの固定値だけを使う）。
- 集計結果と手計算した平均比が一致するかをassertで検証する
- 生成したPDFのページ数が「人数×2＋共通ページ」になっているかを検証する

使い方:
    python3 -m tools.shuuji.render_sample
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.shuuji.aggregate import aggregate_agency, aggregate_member_week, build_auto_comment, build_metrics
from tools.shuuji.models import ROLE_APPOINTER, ROLE_CLOSER
from tools.shuuji.pdf_agency import build_agency_pdf
from tools.shuuji.pdf_meeting import build_meeting_pdf
from tools.shuuji.sample_data import (
    MEMBERS, MONTH_END, MONTH_START, WEEK_END, WEEK_START, build_daily_records, build_deal_records,
)

OUT_DIR = Path(__file__).resolve().parent / "out"


def main() -> None:
    daily = build_daily_records()
    deals = build_deal_records()

    all_stats = [
        aggregate_member_week(member, daily, deals, WEEK_START, WEEK_END, MONTH_START, MONTH_END)
        for member in MEMBERS
    ]
    by_name = {s.member: s for s in all_stats}

    peers_by_role = {
        ROLE_CLOSER: [s for s in all_stats if s.role == ROLE_CLOSER],
        ROLE_APPOINTER: [s for s in all_stats if s.role == ROLE_APPOINTER],
    }

    # --- 手計算での検証 ---
    # 今川: アポ率=2/10=20%、門田: アポ率=8/10=80%。プール平均=(2+8)/(10+10)=50%
    # 今川の平均比=20/50*100=40% -> 70%未満 -> 大幅に低い
    # 門田の平均比=80/50*100=160% -> 110%超 -> 好調
    imagawa_metrics = {mv.label: mv for mv in build_metrics(by_name["今川"], peers_by_role[ROLE_CLOSER])}
    kadota_metrics = {mv.label: mv for mv in build_metrics(by_name["門田"], peers_by_role[ROLE_CLOSER])}

    apo_rate_imagawa = imagawa_metrics["アポ率(対話→アポ)"]
    apo_rate_kadota = kadota_metrics["アポ率(対話→アポ)"]

    assert abs(apo_rate_imagawa.self_value - 20.0) < 1e-6, apo_rate_imagawa.self_value
    assert abs(apo_rate_imagawa.baseline - 50.0) < 1e-6, apo_rate_imagawa.baseline
    assert abs(apo_rate_imagawa.ratio_pct - 40.0) < 1e-6, apo_rate_imagawa.ratio_pct
    assert apo_rate_imagawa.judgment == "大幅に低い", apo_rate_imagawa.judgment

    assert abs(apo_rate_kadota.self_value - 80.0) < 1e-6, apo_rate_kadota.self_value
    assert abs(apo_rate_kadota.ratio_pct - 160.0) < 1e-6, apo_rate_kadota.ratio_pct
    assert apo_rate_kadota.judgment == "好調", apo_rate_kadota.judgment

    # クーリングオフ率（低いほど良い項目）の手計算検証:
    # 今川: cooling_off=1, win=4（自アポ2＋他アポ2。うち他アポには岡野アポ経由の商談も含む）-> 25.0%
    # 門田: cooling_off=0, win=5 -> 0.0%
    # プール平均 = (1+0)/(4+5) = 1/9 = 11.1%。今川の平均比 = 25.0/11.1*100 = 225% -> 150%超 -> 要注意
    cooling_imagawa = imagawa_metrics["クーリングオフ率"]
    assert abs(cooling_imagawa.self_value - 25.0) < 0.1, cooling_imagawa.self_value
    assert abs(cooling_imagawa.baseline - 11.11) < 0.1, cooling_imagawa.baseline
    assert abs(cooling_imagawa.ratio_pct - 225.0) < 0.5, cooling_imagawa.ratio_pct
    assert cooling_imagawa.judgment == "要注意", cooling_imagawa.judgment

    comments = build_auto_comment(list(imagawa_metrics.values()))
    assert comments, "今川には赤/黄項目があるはずなのにコメントが空"
    assert "最優先の改善項目" in comments[0], comments[0]
    # クーリングオフ率(225%要注意)の方がアポ率(40%大幅に低い)より悪化幅が大きいため先頭に来る
    assert "クーリングオフ率" in comments[0], comments[0]
    print("[OK] 判定ロジックの手計算検証: 今川アポ率=大幅に低い(40%) / 門田アポ率=好調(160%)")
    print("[OK] 判定ロジックの手計算検証: 今川クーリングオフ率=要注意(225%)")
    print("[OK] 自動コメント先頭:", comments[0])

    # --- PDF生成 ---
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    agencies = sorted({m.agency for m in all_stats})

    for agency in agencies:
        agency_stats = aggregate_agency(agency, all_stats).members
        n = len(agency_stats)
        out_path = OUT_DIR / f"weekly_{agency}_{WEEK_START}.pdf"
        pages = build_agency_pdf(
            str(out_path), agency, WEEK_START, WEEK_END,
            agency_members=agency_stats,
            all_members_all_agencies=all_stats,
            peers_by_role=peers_by_role,
            month_target_revenue=400.0,
            week_target_revenue=100.0,
            pdca_rows=[{"member": "今川", "declaration": "契約率:33%→50%", "target": "契約3件",
                        "result": "契約2件", "gap": "-1件"}],
        )
        expected = 4 + n * 2
        assert pages == expected, f"{agency}: pages={pages} expected={expected}"
        print(f"[OK] {agency}: {n}名 -> {pages}ページ（期待値{expected}）: {out_path}")

    meeting_path = OUT_DIR / f"meeting_{WEEK_START}.pdf"
    pages = build_meeting_pdf(str(meeting_path), WEEK_START, WEEK_END, all_stats,
                               month_target_revenue=400.0, week_target_revenue=100.0)
    assert pages == 2, f"meeting pages={pages} expected=2"
    print(f"[OK] 全社MT用: {pages}ページ（期待値2）: {meeting_path}")

    print("\n全チェック成功。")


if __name__ == "__main__":
    main()
