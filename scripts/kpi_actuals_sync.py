#!/usr/bin/env python3
"""📊個人別KPI逆算データの「実績」と「残り」を、実データから毎日自動で更新する。

更新する列（目標・予算・転換率・平均単価は触らない。それらは月初予算チェック
scripts/monthly_budget_floor.py が前月までの結果で決める）:
  全員:        今月訪問実績・今月アポ実績（📉実績DBの当月合計）
               今週実績訪問数・今週実績アポ数（水曜始まりの今週。週次実績配信と同じ週定義）
               データ基準日（実行日）
  クローザー:  今月契約実績（商談案件一覧で当月・結果=契約の件数。クーリングオフ除く）
               今月有効商談実績（実績DBの有効商談数）
               今月残り契約数 = max(0, 今月契約目標 − 今月契約実績)
               今月残り有効商談数 = ceil(今月残り契約数 ÷ 契約率%_30日)
  アポインター: 今月有効商談実績（実績DBの有効商談作成数）
               今月残りアポ数 = max(0, 今月アポ目標概算 − 今月アポ実績)
               今月残り訪問数概算 = max(0, ceil(アポ目標 ÷ アポ化率) − 今月訪問実績)
               今週配分_残りアポ数・今週配分_残り訪問数 = 今月の残りを残り週数で割った今週分

使い方:
  python3 scripts/kpi_actuals_sync.py            # 今日（JST）時点で更新
  python3 scripts/kpi_actuals_sync.py --dry-run  # 更新内容の表示のみ
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from collections import defaultdict

from monthly_budget_floor import (DEAL_DB, JST, KPI_DB, NOTION_VERSION_V1, NOTION_VERSION_V2,
                                  PERFORMANCE_DB, load_env, notion, prop_num, prop_text, query_all)


def perf_sums(start: dt.date, end: dt.date) -> dict[str, dict[str, float]]:
    rows = query_all(f"/data_sources/{PERFORMANCE_DB}/query", {"and": [
        {"property": "実績日", "date": {"on_or_after": start.isoformat()}},
        {"property": "実績日", "date": {"on_or_before": end.isoformat()}},
    ]}, NOTION_VERSION_V2)
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        name = prop_text(r, "メンバー")
        for col in ("訪問数", "アポ数", "有効商談数", "有効商談作成数"):
            out[name][col] += prop_num(r, col) or 0
    return out


def contracts_by_closer(start: dt.date, end: dt.date) -> dict[str, int]:
    rows = query_all(f"/data_sources/{DEAL_DB}/query", {"and": [
        {"property": "結果", "status": {"equals": "契約"}},
        {"property": "商談日時", "date": {"on_or_after": start.isoformat()}},
        {"property": "商談日時", "date": {"on_or_before": end.isoformat()}},
    ]}, NOTION_VERSION_V2)
    out: dict[str, int] = defaultdict(int)
    for r in rows:
        out[prop_text(r, "クローザー")] += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()

    today = dt.datetime.now(JST).date()
    month_start = today.replace(day=1)
    month_end = (month_start.replace(year=month_start.year + month_start.month // 12,
                                     month=month_start.month % 12 + 1) - dt.timedelta(days=1))
    week_start = today - dt.timedelta(days=(today.weekday() - 2) % 7)  # 水曜始まり
    weeks_left = max(1, math.ceil(((month_end - today).days + 1) / 7))

    month = perf_sums(month_start, today)
    week = perf_sums(week_start, today)
    contracts = contracts_by_closer(month_start, today)

    for p in query_all(f"/databases/{KPI_DB}/query", None, NOTION_VERSION_V1):
        name = prop_text(p, "メンバー")
        if not name:
            continue
        m, w = month.get(name, {}), week.get(name, {})
        visits, apos = m.get("訪問数", 0), m.get("アポ数", 0)
        props: dict = {
            "今月訪問実績": {"number": visits},
            "今月アポ実績": {"number": apos},
            "今週実績訪問数": {"number": w.get("訪問数", 0)},
            "今週実績アポ数": {"number": w.get("アポ数", 0)},
            "データ基準日": {"date": {"start": today.isoformat()}},
        }
        if prop_text(p, "役割") == "クローザー":
            target = prop_num(p, "今月契約目標") or 0
            done = contracts.get(name, 0)
            remain = max(0, int(target) - done)
            rate = prop_num(p, "契約率%_30日")
            props["今月契約実績"] = {"number": done}
            props["今月有効商談実績"] = {"number": m.get("有効商談数", 0)}
            props["今月残り契約数"] = {"number": remain}
            if rate:
                props["今月残り有効商談数"] = {"number": math.ceil(remain / (rate / 100)) if remain else 0}
            summary = f"契約{done}/{int(target)}"
        else:
            target = prop_num(p, "今月アポ目標概算") or 0
            remain_apo = max(0, int(target) - int(apos))
            rate = prop_num(p, "アポ化率%(訪問→アポ)")
            props["今月有効商談実績"] = {"number": m.get("有効商談作成数", 0)}
            props["今月残りアポ数"] = {"number": remain_apo}
            props["今週配分_残りアポ数"] = {"number": math.ceil(remain_apo / weeks_left)}
            if rate:
                remain_visit = max(0, math.ceil(target / (rate / 100)) - int(visits))
                props["今月残り訪問数概算"] = {"number": remain_visit}
                props["今週配分_残り訪問数"] = {"number": math.ceil(remain_visit / weeks_left)}
            summary = f"アポ{int(apos)}/{int(target)}"
        print(f"{name}: 訪問{int(visits)} {summary}")
        if not args.dry_run:
            notion("PATCH", f"/pages/{p['id']}", {"properties": props}, NOTION_VERSION_V1)


if __name__ == "__main__":
    sys.exit(main())
