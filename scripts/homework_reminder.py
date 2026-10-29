#!/usr/bin/env python3
"""今週の宿題（📝週次PDCA記録DB）の実行日に合わせたLINE。宿題＝改善のためのアクションプラン（2026-10-29 今川さん指示）。

  python3 scripts/homework_reminder.py --when eve     # 夕方: 明日が実行日の人へ「明日は宿題の実行日です」
  python3 scripts/homework_reminder.py --when check   # 夜:   今日が実行日の人へ「できましたか？［宿題やった！］［宿題できなかった］」
  python3 scripts/homework_reminder.py --when eve --date 2026-11-05 --dry-run

・実行日は宿題の「いつ」から決める（木曜／金曜／木・金／土日／毎日、または文中の曜日）。宿題の週は、水曜のMTの翌日（木）
  から翌週火曜まで。会社の稼働日（水〜日と、祝日の月火）だけを数える
・夕方のリマインドは、相手がいれば「再度確認で○○さんに予約のLINE入れて、実行にこだわって徹底しましょう！」と添える。
  「毎日」の宿題は毎晩の確認だけにして、前日のリマインドは最初の日だけ送る（毎日同じ連絡が続かないように）
・朝は、毎朝のカード（scripts/daily_mission.py）が今週の宿題を出し、実行日には「今日は宿題の実行日です」と目立たせる
・返事は LINE Bot（line-webhook/api/webhook.py の handle_homework_check）が「実行チェック」列に記録する
・同じ日に同じ人へ2回送らないよう、送った人をSupabase Storageに記録する

環境変数: NOTION_TOKEN, ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY, DEAL_LINE_CHANNEL_ACCESS_TOKEN
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import load_env, prop_text, query_all  # noqa: E402

try:
    import jpholiday
except ImportError:
    jpholiday = None

PDCA_DB = "17f23c69-8a24-429a-8957-6b5490a87680"   # 📝 週次PDCA記録DB（data_source_id）
NO_HOMEWORK = {"今川", "藤江"}                       # 宿題の対象外（scripts/daily_mission.py と同じ）
DONE, NOT_DONE = "宿題やった！", "宿題できなかった"  # line-webhook/api/webhook.py の HW_DONE / HW_NOT_DONE と同じ文言
JST = ZoneInfo("Asia/Tokyo")
WEEKDAYS = "月火水木金土日"
BUCKET = "weekly-pdca"


def week_key(day: dt.date) -> dt.date:
    """その日にやっている宿題の週初日（daily_mission.week_homework と同じ）。"""
    monday = day - dt.timedelta(days=day.weekday())
    return monday - dt.timedelta(days=7 if day.weekday() >= 2 else 14)


def is_workday(d: dt.date) -> bool:
    return d.weekday() >= 2 or bool(jpholiday and jpholiday.is_holiday(d))


def when_who(row: dict) -> tuple[str, str, str]:
    """（宿題, いつ, 誰と）。Botで登録した行は元メッセージに「宿題：／いつ：／誰と：」がある。"""
    src = prop_text(row, "元メッセージ")
    plan = prop_text(row, "アクションプラン").strip()
    m = re.search(r"いつ：(.+?)／誰と：(.+)", src)
    what = re.search(r"宿題：(.+)", src)
    if m:
        return (what.group(1).strip() if what else plan), m.group(1).strip(), m.group(2).strip()
    m = re.search(r"（([^（）／]+)／([^（）]+)）\s*$", plan)   # 「宿題（いつ／誰と）」の形
    if m:
        return plan[:m.start()].strip(), m.group(1).strip(), m.group(2).strip()
    return plan, plan, ""   # 議事録から入った行など。文中の曜日・「毎日」だけ拾う


def action_days(when: str, key: dt.date) -> list[dt.date]:
    """宿題の実行日。宿題の週＝MT（週初日+9日の水曜）の翌日から翌週火曜まで。"""
    window = [key + dt.timedelta(days=k) for k in range(10, 16)]
    window = [d for d in window if is_workday(d)]
    if "毎日" in when:
        return window
    picked = {i for i, ch in enumerate(WEEKDAYS) if ch in when.replace("毎日", "")}
    dates = {(int(a), int(b)) for a, b in re.findall(r"(\d{1,2})\s*/\s*(\d{1,2})", when)}
    return [d for d in window if d.weekday() in picked or (d.month, d.day) in dates]


def partner_of(who: str) -> str:
    who = who.strip()
    if not who or who.startswith("ひとり"):
        return ""
    who = who[:-1] if who.endswith("と") else who
    if "お客" in who:
        return ""   # お客様との約束は「予約のLINE」の相手ではない
    if who == "同じ代理店の仲間":
        return "一緒にやる仲間"
    if who.endswith(("さん", "リーダー", "仲間")):
        return who
    return who + "さん"


def label(d: dt.date) -> str:
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def homework_rows(day: dt.date) -> list[dict]:
    key = week_key(day)
    rows = query_all(f"/data_sources/{PDCA_DB}/query", {"property": "週初日", "date": {"equals": key.isoformat()}}, "2025-09-03")
    out = []
    for r in rows:
        name = prop_text(r, "氏名")
        if not name or name in NO_HOMEWORK or not prop_text(r, "アクションプラン").strip():
            continue
        what, when, who = when_who(r)
        days = action_days(when, key)
        out.append({"name": name, "what": what, "when": when, "partner": partner_of(who), "days": days, "row": r,
                    "daily": "毎日" in when})
    return out


def is_action_day_for(name: str, day: dt.date, rows: list[dict] | None = None) -> bool:
    """毎朝のカードで「今日は宿題の実行日です」を出すかどうか（scripts/daily_mission.py から使う）。"""
    return any(h["name"] == name and day in h["days"] for h in (rows if rows is not None else homework_rows(day)))


# ---------------------------------------------------------------- 送信
def sb_get(path: str):
    base, key = os.environ["ELP_SUPABASE_URL"].rstrip("/"), os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/{path}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def sent_names(day: dt.date, kind: str) -> set[str]:
    try:
        return set(json.loads(sb_get(f"storage/v1/object/{BUCKET}/homework-reminder/{day.isoformat()}-{kind}.json")))
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return set()
        raise


def save_sent(day: dt.date, kind: str, names: set[str]) -> None:
    base, key = os.environ["ELP_SUPABASE_URL"].rstrip("/"), os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/storage/v1/object/{BUCKET}/homework-reminder/{day.isoformat()}-{kind}.json",
                                 data=json.dumps(sorted(names), ensure_ascii=False).encode(), method="POST",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json", "x-upsert": "true"})
    urllib.request.urlopen(req, timeout=20).close()


def push(to: str, text: str, buttons: list[str] | None = None) -> int | str:
    msg = {"type": "text", "text": text}
    if buttons:
        msg["quickReply"] = {"items": [{"type": "action", "action": {"type": "message", "label": b, "text": b}} for b in buttons]}
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": to, "messages": [msg]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code} {e.read().decode()[:200]}"


def eve_text(h: dict, day: dt.date) -> str:
    lines = [f"📅 明日{label(day)}は、今週の宿題の実行日です。", f"\n【宿題】{h['what']}"]
    if h["partner"]:
        lines += [f"【相手】{h['partner']}", "",
                  f"再度確認で{h['partner']}に予約のLINE入れて、実行にこだわって徹底しましょう！"]
    else:
        lines += ["", "段取りを決めておいて、実行にこだわって徹底しましょう！"]
    return "\n".join(lines)


def check_text(h: dict, day: dt.date) -> str:
    return (f"📝 今日{label(day)}は、今週の宿題の実行日でした。\n\n【宿題】{h['what']}\n\n"
            "できましたか？ 下のボタンで教えてください（来週のMTの答え合わせに使います）。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--when", choices=["eve", "check"], required=True)
    ap.add_argument("--date", help="基準日（YYYY-MM-DD。省略時は今日）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()
    today = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(JST).date()
    day = today + dt.timedelta(days=1) if args.when == "eve" else today
    rows = homework_rows(day)
    targets = []
    for h in rows:
        if day not in h["days"]:
            continue
        if args.when == "eve" and h["daily"] and day != h["days"][0]:
            continue   # 毎日の宿題は、前日のリマインドを最初の日だけにする
        if args.when == "check" and f"{day.month}/{day.day} " in prop_text(h["row"], "実行チェック"):
            continue   # 今日の分はもう返事がある
        targets.append(h)
    if not targets:
        print(f"{day} が実行日の宿題はありません（{args.when}）。")
        return
    base = os.environ["ELP_SUPABASE_URL"].rstrip("/")
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/rest/v1/closer_line_users?select=closer_name,line_user_id",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    users = {u["closer_name"]: u["line_user_id"] for u in json.loads(urllib.request.urlopen(req, timeout=20).read())
             if u.get("closer_name")}
    done = set() if args.dry_run else sent_names(day, args.when)
    for h in targets:
        if h["name"] in done:
            continue
        text = eve_text(h, day) if args.when == "eve" else check_text(h, day)
        buttons = [DONE, NOT_DONE] if args.when == "check" else None
        to = users.get(h["name"])
        if args.dry_run or not to:
            print(f"[{'DRY' if args.dry_run else 'LINE未登録'}] {h['name']}（いつ:{h['when']}／実行日:"
                  f"{'・'.join(label(d) for d in h['days'])}）\n{text}\n")
            continue
        res = push(to, text, buttons)
        print(h["name"], res)
        if res == 200:
            done.add(h["name"])
            save_sent(day, args.when, done)


if __name__ == "__main__":
    main()
