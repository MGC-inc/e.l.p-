#!/usr/bin/env python3
"""毎週木曜: アポインターに「今週の録音を送ろう（ロープレでもOK）」と、LINEで1:1に促す。
2026-11-18 今川さん指示（ベスト録音ライブラリ＋チケット）。

使い方:
  python3 scripts/apo_recording_prompt.py            # 今日（JST）が木曜なら送る。木曜でなければ何もしない
  python3 scripts/apo_recording_prompt.py --dry-run  # 送らずに、誰に送るかだけ表示
  python3 scripts/apo_recording_prompt.py --force    # 木曜でなくても送る（手動のとき）

・送り先は、役割がアポインターの本人だけ（1:1）。グループ・クローザー・管理者・施工担当には送らない。
・今週（月曜から今日まで）にすでにアポ録音を登録した人には送らない。
・同じ日に同じ人へは1回だけ（送った印を deal_recordings に残す。storage_path=apo_prompt）。
・深夜（23:30〜6:00）は送らない。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from quiet_hours import in_quiet_hours  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
EXCLUDE = {"野間", "藤江"}   # 週次の配信・催促の対象外にしている人（野間＝NoMax代表、藤江＝催事）

TEXT = (
    "🎙 木曜は「録音を送る日」です\n"
    "今週いちばん良かったアポの録音を、このトークにそのまま送ってください。ロープレの録音でもOKです。\n\n"
    "・30分以内の録音なら、ボタンで答えるだけで登録できます\n"
    "・登録すると🎫チケットが1枚もらえて、みんなのベスト録音を1本聞けます\n"
    "・あなたにだけ、分析のフィードバックが届きます\n"
    "・お客様のお名前は、他のメンバーには見えません\n\n"
    "使い方は「チケット」と送ると確認できます。"
)


def sb(method, path, data=None):
    base = os.environ["ELP_SUPABASE_URL"].strip("（）()").rstrip("/")
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"].strip("（）()")
    req = urllib.request.Request(f"{base}/rest/v1/{path}", method=method, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def push(to: str, text: str) -> None:
    tok = os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]
    msg = {"type": "text", "text": text, "quickReply": {"items": [{"type": "action", "action": {"type": "message", "label": "チケット", "text": "チケット"}}]}}
    req = urllib.request.Request("https://api.line.me/v2/bot/message/push", method="POST", data=json.dumps({"to": to, "messages": [msg]}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    urllib.request.urlopen(req, timeout=20).read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    now = dt.datetime.now(JST)
    today = now.date()
    if today.weekday() != 3 and not args.force:
        print(f"今日（{today}）は木曜ではないので、何も送りません。")
        return
    if in_quiet_hours(now) and not args.dry_run:
        print("深夜のため送りません。")
        return
    monday = today - dt.timedelta(days=today.weekday())
    users = sb("GET", "closer_line_users?role=eq.appointer&closer_name=not.is.null&select=line_user_id,closer_name") or []
    since = urllib.parse.quote(dt.datetime.combine(monday, dt.time(0, 0), JST).astimezone(dt.timezone.utc).isoformat())
    sent_week = {r["line_user_id"] for r in (sb("GET", f"deal_recordings?status=eq.apo_done&received_at=gte.{since}&select=line_user_id&limit=1000") or [])}
    marked = {r["line_user_id"] for r in (sb("GET", f"deal_recordings?storage_path=eq.apo_prompt&customer_name=eq.{today.isoformat()}&select=line_user_id&limit=1000") or [])}
    users = [u for u in users if u["closer_name"] not in EXCLUDE]
    to_send = [u for u in users if u["line_user_id"] not in sent_week and u["line_user_id"] not in marked]
    print(f"アポインター{len(users)}人 / 今週すでに登録済み{len(sent_week & {u['line_user_id'] for u in users})}人 / 送る{len(to_send)}人")
    for u in to_send:
        if args.dry_run:
            print("  [DRY]", u["closer_name"])
            continue
        try:
            push(u["line_user_id"], TEXT)
            sb("POST", "deal_recordings", [{"line_user_id": u["line_user_id"], "storage_path": "apo_prompt", "status": "apo_prompt", "customer_name": today.isoformat()}])
            print("  送信:", u["closer_name"])
        except Exception as e:
            print(f"  失敗 {u['closer_name']}: {e}")


if __name__ == "__main__":
    main()
