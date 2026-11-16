#!/usr/bin/env python3
"""録音が見つからなかった「保留→契約／失注」の商談を、あとから録音が届いたときに分析し直す（今川さん指示 2026-11-16）。

保留転換検知Routine（商談分析運用.md 6-5）は、検知した時点で録音が見つからないと、経過日数だけ記録して「分析済み」にする。
そのままだと、あとから録音が届いても分析されない。そこで、録音が無かった案件を「録音待ち」として残し、
毎日このスクリプトが、録音が届いていれば scripts/pending_transition_analysis.py で分析してNotionに追記する。

  python3 scripts/hold_transition_retry.py add --customer 初谷邸 --closer 門田 --final 契約 \\
      --pending-date 2026-11-11 --transition-date 2026-11-11 --url https://app.notion.com/...   # 録音待ちに登録
  python3 scripts/hold_transition_retry.py              # 録音待ちを確認し、届いていれば分析して通知（毎日のRoutine）
  python3 scripts/hold_transition_retry.py --dry-run    # 何をするかだけ表示

・録音待ちは deal_recordings（storage_path='hold_wait', status='hold_wait'）に持つ。30日たっても録音が来なければ打ち切る
・録音は「お客様名とクローザーが一致する、いちばん新しい録音」。届いたら、分析結果をNotionページの末尾に追記し、
  担当クローザー本人と今川さんにLINEで知らせる
環境変数: ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY, NOTION_TOKEN, GEMINI_API_KEY, DEAL_LINE_CHANNEL_ACCESS_TOKEN
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import load_env, notion  # noqa: E402
from register_analysis import blocks_of  # noqa: E402

KIND = "hold_wait"
EXPIRE_DAYS = 30


def sb(method: str, path: str, data=None):
    base, key = os.environ["ELP_SUPABASE_URL"].rstrip("/"), os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/rest/v1/{path}", method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "Prefer": "return=representation"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def line_push(to: str, text: str) -> int | str:
    req = urllib.request.Request("https://api.line.me/v2/bot/message/push", method="POST",
                                 data=json.dumps({"to": to, "messages": [{"type": "text", "text": text[:4800]}]}).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code} {e.read().decode()[:200]}"


def line_id(name: str) -> str | None:
    rows = sb("GET", "closer_line_users?closer_name=eq." + urllib.parse.quote(name) + "&select=line_user_id") or []
    return rows[0]["line_user_id"] if rows else None


def find_recording(customer: str, closer: str) -> dict | None:
    rows = sb("GET", "deal_recordings?customer_name=eq." + urllib.parse.quote(customer) + "&closer_name=eq."
                     + urllib.parse.quote(closer) + "&select=id,storage_path,status,received_at&order=received_at.desc&limit=20") or []
    for r in rows:   # 録音ファイルの行（"2026/…mp3" のような保存先を持つもの）だけ
        p = r.get("storage_path") or ""
        if "/" in p and re.search(r"\.\w{2,4}$", p):
            return r
    return None


def download(storage_path: str, dest_dir: str) -> str:
    base, key = os.environ["ELP_SUPABASE_URL"].rstrip("/"), os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/storage/v1/object/deal-recordings/{urllib.parse.quote(storage_path)}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    out = os.path.join(dest_dir, "audio" + (os.path.splitext(storage_path)[1] or ".mp3"))
    with urllib.request.urlopen(req, timeout=900) as r, open(out, "wb") as f:
        f.write(r.read())
    return out


def page_id_of(url: str) -> str:
    m = re.search(r"([0-9a-f]{32})", url.replace("-", ""))
    if not m:
        raise ValueError(f"NotionページのIDを読み取れません: {url}")
    return m.group(1)


def cmd_add(a) -> None:
    info = {"customer": a.customer, "closer": a.closer, "final": a.final, "pending_date": a.pending_date,
            "transition_date": a.transition_date, "url": a.url}
    existing = sb("GET", f"deal_recordings?storage_path=eq.{KIND}&status=eq.{KIND}&select=id,customer_name") or []
    if any(json.loads(r["customer_name"]).get("url") == a.url for r in existing):
        print("すでに録音待ちに登録済みです:", a.customer)
        return
    sb("POST", "deal_recordings", {"line_user_id": line_id("今川") or "hold_wait", "storage_path": KIND, "status": KIND,
                                   "customer_name": json.dumps(info, ensure_ascii=False)})
    print("録音待ちに登録しました:", a.customer, a.closer, a.final)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    add = sub.add_parser("add")
    add.add_argument("--customer", required=True)
    add.add_argument("--closer", required=True)
    add.add_argument("--final", required=True, choices=["契約", "失注"])
    add.add_argument("--pending-date", required=True)
    add.add_argument("--transition-date", required=True)
    add.add_argument("--url", required=True, help="商談案件一覧の該当ページのURL")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    load_env()
    if a.cmd == "add":
        return cmd_add(a)

    rows = sb("GET", f"deal_recordings?storage_path=eq.{KIND}&status=eq.{KIND}&order=received_at.asc&select=*") or []
    if not rows:
        print("録音待ちの案件はありません。")
        return
    now = dt.datetime.now(dt.timezone.utc)
    done = 0
    for r in rows:
        info = json.loads(r["customer_name"])
        age = (now - dt.datetime.fromisoformat(r["received_at"].replace("Z", "+00:00"))).days
        rec = find_recording(info["customer"], info["closer"])
        if not rec:
            if age > EXPIRE_DAYS and not a.dry_run:
                sb("PATCH", f"deal_recordings?id=eq.{r['id']}", {"status": "hold_expired"})
                print(f"{info['customer']}: {EXPIRE_DAYS}日たっても録音が届かないため打ち切り")
            else:
                print(f"{info['customer']}（{info['closer']}）: まだ録音が届いていません（{age}日待ち）")
            continue
        print(f"{info['customer']}（{info['closer']}）: 録音が届いています（{rec['storage_path']}）")
        if a.dry_run:
            continue
        with tempfile.TemporaryDirectory() as td:
            audio = download(rec["storage_path"], td)
            res = subprocess.run([sys.executable, os.path.join(HERE, "pending_transition_analysis.py"), audio,
                                  "--closer", info["closer"], "--customer", info["customer"], "--final-result", info["final"],
                                  "--pending-date", info["pending_date"], "--transition-date", info["transition_date"]],
                                 capture_output=True, text=True, timeout=1500)
        if res.returncode != 0 or not res.stdout.strip():
            print("分析に失敗しました（次回やり直します）:", (res.stderr or "")[-300:])
            continue
        body = "---\n\n■保留転換分析（録音が届いたあとの再分析・自動生成）\n\n" + res.stdout.strip()
        blocks = blocks_of(body)
        pid = page_id_of(info["url"])
        for i in range(0, len(blocks), 90):   # Notionは1回100ブロックまで
            notion("PATCH", f"blocks/{pid}/children", {"children": blocks[i:i + 90]}, version="2022-06-28")
        sb("PATCH", f"deal_recordings?id=eq.{r['id']}", {"status": "hold_done"})
        mine = line_id(info["closer"])
        if mine:
            print("クローザーへ:", line_push(mine, f"{info['customer']}の商談が保留→{info['final']}になったため、商談を分析し直しました。\n{info['url']}"))
        admin = line_id("今川")
        if admin:
            print("今川さんへ:", line_push(admin, f"{info['customer']}（クローザー: {info['closer']}）の録音が届いたので、保留→{info['final']}の分析を追記しました（詳細はNotionページ参照）。\n{info['url']}"))
        done += 1
    print(f"分析した案件: {done}件")


if __name__ == "__main__":
    main()
