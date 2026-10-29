#!/usr/bin/env python3
"""月次アクションプランのリマインド（🗓 月次アクションプランDB。LINE Botの「今月のアクションプランを登録する」で登録されたもの）。

  python3 scripts/action_plan_reminder.py --when eve       # 18時: 明日が実行日のプラン
  python3 scripts/action_plan_reminder.py --when morning   # 朝: 今日が実行日のプラン
  python3 scripts/action_plan_reminder.py --when eve --dry-run

・相手がいるプランは「再度確認で○○さんに予約のLINE入れて実行にこだわって徹底しましょう！」と添える（2026-10-29 今川さん指示）
・同じ人の同じ日のプランは1通にまとめる。送ったら「前日リマインド済み／当日リマインド済み」に印を付けて二重に送らない
・状態が「予定」のものだけ送る

環境変数: NOTION_TOKEN, ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY, DEAL_LINE_CHANNEL_ACCESS_TOKEN
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import load_env, notion, prop_text, query_all  # noqa: E402

AP_DB = "408da094-9999-8127-bdf7-d765f11a4594"   # 🗓 月次アクションプラン（database_id）
JST = ZoneInfo("Asia/Tokyo")
WEEKDAYS = "月火水木金土日"


def partner_of(who: str) -> str:
    """「リーダーと」→「リーダー」、「安達さん」→「安達さん」、「安達」→「安達さん」。"""
    who = who.strip()
    if who.endswith("と"):
        who = who[:-1]
    if not who or who == "ひとり":
        return ""
    return who if who.endswith(("さん", "リーダー")) else who + "さん"


def push(to: str, text: str) -> int | str:
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": to, "messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code} {e.read().decode()[:200]}"


def line_users() -> dict[str, str]:
    base = os.environ["ELP_SUPABASE_URL"].rstrip("/")
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/rest/v1/closer_line_users?select=closer_name,line_user_id",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return {u["closer_name"]: u["line_user_id"] for u in json.loads(r.read()) if u.get("closer_name")}


def pct(p: dict, name: str) -> str:
    v = (p["properties"].get(name) or {}).get("number")
    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else ""


def message(when: str, day: dt.date, plans: list[dict]) -> str:
    label = f"{day.month}/{day.day}({WEEKDAYS[day.weekday()]})"
    head = (f"📅 明日{label}は、改善のために立てたアクションプランの実行日です。" if when == "eve"
            else f"🔥 今日{label}は、改善のために立てたアクションプランの実行日です。")
    lines, partners = [head], []
    for p in plans:
        lines.append(f"\n【やること】{prop_text(p, 'アクション')}")
        partner = partner_of(prop_text(p, "相手"))
        if partner:
            lines.append(f"【相手】{partner}")
            partners.append(partner)
        lever, now, goal = prop_text(p, "改善ポイント"), pct(p, "今の率"), pct(p, "目標の率")
        if lever and now and goal:
            lines.append(f"【改善したいこと】{lever} {now}→{goal}")
    lines.append("")
    if partners:
        who = "・".join(dict.fromkeys(partners))
        lines.append(f"再度確認で{who}に予約のLINE入れて、実行にこだわって徹底しましょう！" if when == "eve"
                     else f"{who}との約束、もう一度確認して、実行にこだわって徹底しましょう！")
    else:
        lines.append("段取りを確認して、実行にこだわって徹底しましょう！" if when == "eve"
                     else "今日やり切りましょう！")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--when", choices=["eve", "morning"], required=True)
    ap.add_argument("--date", help="基準日（YYYY-MM-DD。省略時は今日）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()
    today = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(JST).date()
    day = today + dt.timedelta(days=1) if args.when == "eve" else today
    flag = "前日リマインド済み" if args.when == "eve" else "当日リマインド済み"
    rows = query_all(f"/databases/{AP_DB}/query", {"and": [
        {"property": "実行日", "date": {"equals": day.isoformat()}},
        {"property": "状態", "select": {"equals": "予定"}},
        {"property": flag, "checkbox": {"equals": False}}]}, "2022-06-28")
    if not rows:
        print(f"{day} が実行日の未送信プランはありません。")
        return
    by = collections.defaultdict(list)
    for r in rows:
        by[prop_text(r, "メンバー")].append(r)
    users = line_users()
    for name, plans in by.items():
        text = message(args.when, day, plans)
        to = users.get(name)
        if args.dry_run or not to:
            print(f"[{'DRY' if args.dry_run else 'LINE未登録'}] {name}\n{text}\n")
            continue
        res = push(to, text)
        print(name, res)
        if res == 200:
            for p in plans:
                notion("PATCH", f"/pages/{p['id']}", {"properties": {flag: {"checkbox": True}}}, "2022-06-28")


if __name__ == "__main__":
    main()
