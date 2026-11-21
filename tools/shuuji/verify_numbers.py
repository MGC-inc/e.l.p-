#!/usr/bin/env python3
"""週次MT資料の数字を出す前に、Notionの2つのDBが食い違っていないかを突き合わせる。

- 📉実績DB（売上・活動量の正。💰予算と達成率の実績もここから集計される）
- DB 商談分析＆アポ分析（商談1件ずつの記録。結果＝契約／クーリングオフ／審査落ち）

実績DBの売上の付け方（実データで確認した運用ルール）:
  自己アポ（アポインター＝クローザー、またはアポインターが空）の契約 → クローザーの行に全額
    （件数はクローザーの行の「契約」と「契約（アポ）」の両方に1件）
  他アポの契約 → クローザーの行とアポインターの行に半額ずつ
  クーリングオフ・審査落ち → 契約金額はそのまま、同じ配分でキャンセル金額にも入れる
商談DBの1件ごとに上のルールで「実績DBに入っているべき金額」を人別に計算し、実績DBの実際の値と比べる。
人別の 契約金額・キャンセル金額・契約件数（クローザー）・契約（アポ）件数 のどれかがずれていれば不一致。

使い方:
  python3 tools/shuuji/verify_numbers.py                 # 先週（月〜日）と今月（1日〜先週日曜）を確認
  python3 tools/shuuji/verify_numbers.py --notify        # 結果を今川さんにLINEで送る（一致でも送る）
  python3 tools/shuuji/verify_numbers.py --notify-on-error  # 不一致のときだけ送る
終了コード: 一致=0／不一致=1（週次配信のworkflowはこれで配信を止める）
"""
from __future__ import annotations

import datetime
import os
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from notion_to_csv import (DEAL_DB, PERFORMANCE_DB, RESULT_CONTRACT, RESULT_COOLING_OFF,  # noqa: E402
                           RESULT_REJECTED, notion_query_all, prop_date, prop_number, prop_select, prop_status)

FIELDS = ["契約金額（万円）", "キャンセル金額（万円）", "契約", "契約（アポ）"]
TOL = 0.6  # 万円の丸め誤差


def expected_from_deals(start: datetime.date, end: datetime.date) -> tuple[dict, list]:
    deals = notion_query_all(DEAL_DB, {"and": [
        {"property": "商談日時", "date": {"on_or_after": start.isoformat()}},
        {"property": "商談日時", "date": {"on_or_before": end.isoformat()}},
    ]})
    exp: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    detail: dict[str, list[str]] = defaultdict(list)
    for d in deals:
        result = prop_status(d, "結果")
        if result not in (RESULT_CONTRACT, RESULT_COOLING_OFF, RESULT_REJECTED):
            continue
        closer, appointer = prop_select(d, "クローザー"), prop_select(d, "アポインター")
        man = prop_number(d, "売上金額") / 10000
        own = not appointer or appointer == closer
        # (人, 金額, 数える件数列)。自己アポはクローザーの行で「契約」と「契約（アポ）」の両方に1件
        shares = [(closer, man, ("契約", "契約（アポ）"))] if own else \
            [(closer, man / 2, ("契約",)), (appointer, man / 2, ("契約（アポ）",))]
        label = f"{prop_date(d, '商談日時')[5:].replace('-', '/')} {closer}×{appointer or '自己'} {result} {man:g}万"
        for name, amount, count_cols in shares:
            exp[name]["契約金額（万円）"] += amount
            for c in count_cols:
                exp[name][c] += 1
            if result != RESULT_CONTRACT:
                exp[name]["キャンセル金額（万円）"] += amount
            detail[name].append(label)
    return exp, detail


def actual_from_performance(start: datetime.date, end: datetime.date) -> dict:
    rows = notion_query_all(PERFORMANCE_DB, {"and": [
        {"property": "実績日", "date": {"on_or_after": start.isoformat()}},
        {"property": "実績日", "date": {"on_or_before": end.isoformat()}},
    ]})
    act: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        name = prop_select(r, "メンバー")
        for f in FIELDS:
            act[name][f] += prop_number(r, f)
    return act


def compare(start: datetime.date, end: datetime.date) -> list[str]:
    exp, detail = expected_from_deals(start, end)
    act = actual_from_performance(start, end)
    problems = []
    for name in sorted(set(exp) | set(act)):
        diffs = []
        for f in FIELDS:
            e, a = exp[name][f], act[name][f]
            if abs(e - a) > (TOL if "万円" in f else 0.01):
                unit = "万" if "万円" in f else "件"
                diffs.append(f"{f.replace('（万円）', '')} 商談一覧{e:g}{unit}／実績DB{a:g}{unit}")
        if diffs:
            problems.append(f"・{name}: " + "、".join(diffs)
                            + ("\n   対象の商談: " + "／".join(detail[name]) if detail[name] else "\n   （商談一覧に該当する契約なし）"))
    return problems


def periods(today: datetime.date) -> list[tuple[str, datetime.date, datetime.date]]:
    week_start = today - datetime.timedelta(days=today.weekday() + 7)
    week_end = week_start + datetime.timedelta(days=6)
    month_start = week_end.replace(day=1)
    return [("先週", week_start, week_end), ("今月", month_start, week_end)]


def main() -> int:
    today = datetime.datetime.now(ZoneInfo("Asia/Tokyo")).date()
    lines, ok = [], True
    for label, s, e in periods(today):
        problems = compare(s, e)
        head = f"【{label} {s.month}/{s.day}〜{e.month}/{e.day}】"
        if problems:
            ok = False
            lines += [head + f" 不一致 {len(problems)}名"] + problems
        else:
            lines.append(head + " 商談一覧と実績DBは一致")
    title = "✅ 週次MT資料の数字チェック: 一致" if ok else "⚠ 週次MT資料の数字チェック: Notionの入力ずれがあります"
    tail = [] if ok else ["", "実績DB（またはお客様の商談記録）を直してから、weekly-shuujiを手動実行してください。直るまで配信は止めています。"]
    text = "\n".join([title] + lines + tail)
    print(text)
    if "--notify" in sys.argv or ("--notify-on-error" in sys.argv and not ok):
        from monthly_budget_floor import notify_admin
        notify_admin(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
