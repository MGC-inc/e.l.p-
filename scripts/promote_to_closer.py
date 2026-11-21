#!/usr/bin/env python3
"""アポインターを、クローザー（アポ兼クローザー）に切り替える。2026-11-20 今川さん指示（安達・巻田を12月から）。

アポインターとしての登録（KNOWN_APPOINTERS・Notion・Supabase）はそのまま残し、クローザーとしても扱う。
実績DBの「役割」「集計用：クローザー行」「所属」の数式は、すでに2026-12-01以降で切り替わるようにしてある（2026-11-20に反映済み）。
このスクリプトが切り替えるのは、日付で切れない次の3つ。

  1. line-webhook/api/webhook.py の KNOWN_CLOSERS に追加（新規登録・クローザー選択の候補に出るようになる）
  2. Supabase closer_line_users の role を closer に変更
  3. LINEのクローザー用リッチメニューを個別にリンク

Notionの名簿DB（役割にクローザーを足す・クロ集計行をON）と、個人別KPIの行（役割をクローザー）は、
Notion MCPで行う（このスクリプトの範囲外。商談分析運用.mdの「12月からのクローザー切り替え」を見ること）。

使い方:
  python3 scripts/promote_to_closer.py 安達 巻田            # 2026-12-01より前は何もしない
  python3 scripts/promote_to_closer.py 安達 巻田 --dry-run  # 何をするかだけ表示
  python3 scripts/promote_to_closer.py 安達 巻田 --force    # 日付に関係なく実行
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEBHOOK = os.path.join(ROOT, "line-webhook", "api", "webhook.py")
CLOSER_MENU_NAME = "営業分析BOT クローザー用"
EFFECTIVE = dt.date(2026, 12, 1)
JST = ZoneInfo("Asia/Tokyo")


def sb(method, path, data=None):
    base = os.environ["ELP_SUPABASE_URL"].strip("（）()").rstrip("/")
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"].strip("（）()")
    req = urllib.request.Request(f"{base}/rest/v1/{path}", method=method, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def line(method, path, body=None):
    tok = os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]
    req = urllib.request.Request("https://api.line.me/v2/bot/" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def add_to_known_closers(name: str, dry: bool) -> bool:
    text = open(WEBHOOK, encoding="utf-8").read()
    m = re.search(r"KNOWN_CLOSERS = \{\n(.*?)\n\}", text, re.S)
    if not m:
        sys.exit("KNOWN_CLOSERS が見つかりません")
    body = m.group(1)
    if re.search(rf'"{re.escape(name)}":', body):
        print(f"  KNOWN_CLOSERS に「{name}」はすでにあります")
        return False
    new_body = body + f'\n    "{name}": "{name}",'
    print(f"  KNOWN_CLOSERS に「{name}」を追加")
    if not dry:
        open(WEBHOOK, "w", encoding="utf-8").write(text.replace(body, new_body, 1))
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+", help="苗字（例: 安達 巻田）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    today = dt.datetime.now(JST).date()
    if today < EFFECTIVE and not args.force:
        print(f"今日（{today}）は {EFFECTIVE} より前なので、何もしません（--force で実行できます）。")
        return
    menus = [m for m in line("GET", "richmenu/list").get("richmenus", []) if m.get("name") == CLOSER_MENU_NAME]
    menu_id = menus[0]["richMenuId"] if menus else None
    if not menu_id:
        print("⚠ クローザー用リッチメニューが見つかりません（scripts/deal_bot_richmenu.py を先に実行）")
    for name in args.names:
        print(f"■ {name}")
        add_to_known_closers(name, args.dry_run)
        rows = sb("GET", f"closer_line_users?closer_name=eq.{urllib.parse.quote(name)}&select=id,line_user_id,role") or []
        if not rows:
            print("  Supabaseに行がありません（本人がBotに最初のメッセージを送ると、KNOWN_CLOSERSで直接クローザーになります）")
            continue
        for r in rows:
            print(f"  Supabase role: {r['role']} → closer")
            if args.dry_run:
                continue
            sb("PATCH", f"closer_line_users?id=eq.{r['id']}", {"role": "closer"})
            if menu_id:
                line("POST", f"user/{r['line_user_id']}/richmenu/{menu_id}")
                print("  クローザー用メニューをリンクしました")


if __name__ == "__main__":
    main()
