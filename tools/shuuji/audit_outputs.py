#!/usr/bin/env python3
"""週次MT資料の「再確認」: 作り終えた資料の元データ（notion_to_csv.pyの出力CSV）の数字を、
Notionからもう一度読み直して、別の計算方法で出し直した値と1つずつ照合する。
1つでも合わなければ配信しない（週次配信workflowの、資料生成の後・配信の前に実行する）。

notion_to_csv.pyとは計算方法を変えてある（同じ書き間違いで両方ずれるのを防ぐため）:
  - 売上: notion_to_csv.pyは「契約金額−キャンセル金額」を自分で計算 → ここはNotionの数式列「実質売上（万円）」を読む
  - 全社の契約数: notion_to_csv.pyは商談一覧の件数 → ここは実績DBの「契約−クーリン−審査落」
  - 月の売上目標: 💰予算と達成率を読み直す
  - 先週の宿題: 📝週次PDCA記録DBを週初日の完全一致で読み直す
照合する数字:
  個人: 訪問・アポ・売上・日報の日数（実績DB）、商談・契約（商談一覧）
  代理店: 代理店の合計の和＝全社の合計
  全社: 訪問・アポ・商談・契約・売上、月の契約・売上の実績と売上目標
  宿題: スライドに出す宿題の文が、正しい週の記録と同じか

使い方: python3 tools/shuuji/audit_outputs.py <notion_to_csv.pyの出力フォルダ> [--notify-on-error | --notify]
"""
from __future__ import annotations

import csv
import datetime
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from monthly_budget_floor import (BUDGET_DB, DEAL_DB, NOTION_VERSION_V2, PERFORMANCE_DB, load_env,  # noqa: E402
                                  notify_admin, prop_num, prop_text, query_all)

PDCA_DB = "17f23c69-8a24-429a-8957-6b5490a87680"
TOL = 0.05


def read_csv(path: str) -> list[dict]:
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


def perf(start: datetime.date, end: datetime.date) -> list[dict]:
    return query_all(f"/data_sources/{PERFORMANCE_DB}/query", {"and": [
        {"property": "実績日", "date": {"on_or_after": start.isoformat()}},
        {"property": "実績日", "date": {"on_or_before": end.isoformat()}},
    ]}, NOTION_VERSION_V2)


def deals(start: datetime.date, end: datetime.date) -> list[dict]:
    return query_all(f"/data_sources/{DEAL_DB}/query", {"and": [
        {"property": "商談日時", "date": {"on_or_after": start.isoformat()}},
        {"property": "商談日時", "date": {"on_or_before": end.isoformat()}},
    ]}, NOTION_VERSION_V2)


def net_contracts(rows: list[dict]) -> float:
    return sum((prop_num(r, "契約") or 0) - (prop_num(r, "クーリン") or 0) - (prop_num(r, "審査落") or 0) for r in rows)


def main() -> int:
    load_env()
    inp = sys.argv[1]
    cfg = {r["key"]: r["value"] for r in read_csv(f"{inp}/config.csv")}
    members = {r["name"]: r for r in read_csv(f"{inp}/members.csv")}
    results = {r["name"]: {k: float(v or 0) for k, v in r.items() if k != "name"} for r in read_csv(f"{inp}/results.csv")}
    totals = {r["agency"]: {k: float(v or 0) for k, v in r.items() if k != "agency"} for r in read_csv(f"{inp}/totals.csv")}
    pdca = {r["name"]: r for r in read_csv(f"{inp}/pdca.csv")} if os.path.exists(f"{inp}/pdca.csv") else {}

    ws = datetime.date.fromisoformat(cfg["data_week_start"])
    we = ws + datetime.timedelta(days=6)
    ms = we.replace(day=1)
    errors: list[str] = []

    def check(label: str, shown: float, truth: float) -> None:
        if abs(shown - truth) > TOL:
            errors.append(f"・{label}: 資料{shown:g}／Notion{truth:g}")

    # ---- 個人 ----
    wrows = perf(ws, we)
    by = defaultdict(lambda: defaultdict(float))
    for r in wrows:
        n = prop_text(r, "メンバー")
        by[n]["perf_days"] += 1
        by[n]["sales"] += prop_num(r, "実質売上（万円）") or 0  # Notionの数式列
        if prop_text(r, "出勤状況") != "休み":
            by[n]["visits"] += prop_num(r, "訪問数") or 0
            by[n]["appo"] += prop_num(r, "アポ数") or 0
    wdeals = deals(ws, we)
    for name, res in results.items():
        role = members[name]["role"]
        for k, lab in (("visits", "訪問"), ("appo", "アポ"), ("sales", "売上"), ("perf_days", "日報の日数")):
            check(f"{name} {lab}", res[k], by[name][k])
        col = "クローザー" if role == "closer" else "アポインター"
        mine = [d for d in wdeals if prop_text(d, col) == name]
        check(f"{name} 商談", res["meet_own"] + res["meet_other"], len(mine))
        check(f"{name} 契約", res["win_own"] + res["win_other"], sum(1 for d in mine if prop_text(d, "結果") == "契約"))

    # ---- 全社・代理店 ----
    t = totals["全社"]
    check("全社 訪問", t["visits"], sum(prop_num(r, "訪問数") or 0 for r in wrows if prop_text(r, "出勤状況") != "休み"))
    check("全社 アポ", t["appo"], sum(prop_num(r, "アポ数") or 0 for r in wrows if prop_text(r, "出勤状況") != "休み"))
    check("全社 売上", t["sales"], sum(prop_num(r, "実質売上（万円）") or 0 for r in wrows))
    check("全社 商談", t["meet"], len(wdeals))
    check("全社 契約（商談一覧）", t["win"], sum(1 for d in wdeals if prop_text(d, "結果") == "契約"))
    check("全社 契約（実績DBの契約−クーリン−審査落）", t["win"], net_contracts(wrows))
    for k, lab in (("visits", "訪問"), ("appo", "アポ"), ("meet", "商談"), ("win", "契約"), ("sales", "売上")):
        check(f"代理店の合計＝全社 {lab}", sum(v[k] for a, v in totals.items() if a != "全社"), t[k])

    # ---- 月 ----
    mrows = perf(ms, we)
    mdeals = deals(ms, we)
    check(f"月 契約（{ms.month}/{ms.day}〜{we.month}/{we.day}・商談一覧）", float(cfg["actual_contracts"]),
          sum(1 for d in mdeals if prop_text(d, "結果") == "契約"))
    check("月 契約（実績DBの契約−クーリン−審査落）", float(cfg["actual_contracts"]), net_contracts(mrows))
    check("月 売上", float(cfg["actual_sales"]), round(sum(prop_num(r, "実質売上（万円）") or 0 for r in mrows), 1))
    budget = query_all(f"/data_sources/{BUDGET_DB}/query",
                       {"property": "年月（YYYY-MM）", "rich_text": {"equals": ms.strftime("%Y-%m")}}, NOTION_VERSION_V2)
    check("月 売上目標", float(cfg["target_sales"]), round(sum(prop_num(r, "予算（万円）") or 0 for r in budget), 1))

    # ---- 先週の宿題 ----
    key = (ws - datetime.timedelta(days=7)).isoformat()
    recs = query_all(f"/data_sources/{PDCA_DB}/query", {"property": "週初日", "date": {"equals": key}}, NOTION_VERSION_V2)
    truth = {prop_text(r, "氏名"): "".join(x.get("plain_text", "") for x in r["properties"]["アクションプラン"]["rich_text"])
             for r in recs}
    for name in members:
        shown = (pdca.get(name) or {}).get("action", "")
        if bool(shown) != bool(truth.get(name, "").strip()):
            errors.append(f"・{name} 先週の宿題: 資料{'あり' if shown else 'なし'}／Notion（週初日{key}）{'あり' if truth.get(name) else 'なし'}")

    head = f"週次MT資料の再確認（{ws.month}/{ws.day}〜{we.month}/{we.day}）"
    if errors:
        text = "\n".join([f"⚠ {head}: 資料とNotionが{len(errors)}か所合いません。配信を止めました。"] + errors[:40])
        print(text)
        if "--notify-on-error" in sys.argv or "--notify" in sys.argv:
            notify_admin(text)
        return 1
    text = f"✅ {head}: 個人{len(results)}名・代理店・全社・月・宿題の数字がすべてNotionと一致"
    print(text)
    if "--notify" in sys.argv:
        notify_admin(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
