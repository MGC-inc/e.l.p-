#!/usr/bin/env python3
"""深夜（23:30〜6:00 JST）に今川さんが答えたぶんの、保留していた返信を、朝6:00以降に送る。

  python3 scripts/flush_deferred.py --dry-run   # 保留中の件数だけ見る（送らない）
  python3 scripts/flush_deferred.py             # 6:00以降なら、保留中の返信を今川さんのLINEへ送る

botは今川さんが深夜に送ったメッセージへの返信を送らず、deal_recordings（storage_path=deferred_reply,
status=deferred_pending）に溜める。この処理が6:00以降に、古い順に1件ずつ送る。
送るのは今川さんだけ。営業マン・施工担当・グループへは送らない。

環境変数: ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY, DEAL_LINE_CHANNEL_ACCESS_TOKEN
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import load_env  # noqa: E402
from quiet_hours import in_quiet_hours  # noqa: E402


def sb(method: str, path: str, data=None):
    base, key = os.environ["ELP_SUPABASE_URL"].rstrip("/"), os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/rest/v1/{path}", method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def push(to: str, messages: list) -> str | int:
    req = urllib.request.Request("https://api.line.me/v2/bot/message/push", method="POST",
                                 data=json.dumps({"to": to, "messages": messages[:5]}).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code} {e.read().decode()[:200]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()
    # select=* で取得（このテーブルの列は line_user_id / customer_name / received_at / status など）
    rows = sb("GET", "deal_recordings?storage_path=eq.deferred_reply&status=eq.deferred_pending&order=received_at.asc&select=*") or []
    if not rows:
        print("保留中の返信はありません。")
        return
    if args.dry_run:
        print(f"保留中の返信 {len(rows)}件（--dry-run のため送っていません）")
        return
    if in_quiet_hours():
        print("まだ6:00前（または23:30以降）のため送りません。")
        return
    # 古い順に、最初の1通の前に「深夜にいただいた返信」であることを1行で伝える
    first = True
    for r in rows:
        messages = json.loads(r["customer_name"] or "[]")
        if not messages:
            sb("PATCH", f"deal_recordings?id=eq.{r['id']}", {"status": "deferred_sent"})
            continue
        if first:
            messages = [{"type": "text", "text": "（深夜にいただいた分の返信を、朝にお届けします）"}] + messages
            first = False
        st = push(r["line_user_id"], messages)
        print(r["id"], st)
        if st == 200:
            sb("PATCH", f"deal_recordings?id=eq.{r['id']}", {"status": "deferred_sent"})


if __name__ == "__main__":
    main()
