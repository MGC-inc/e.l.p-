#!/usr/bin/env python3
"""今週の宣言をまだ登録していない人に、LINEで1:1に催促する。2026-11-21 今川さん指示。

- 「登録済み」は、📝週次PDCA記録DBに、今週ぶん（daily_mission.week_homework と同じキー）のアクションプランがあること。
- 送り先は、営業分析Botに登録済みのクローザー・アポインター本人（closer_line_users）から、
  宣言の対象外（今川・藤江・野間）・週次の対象外（WEEKLY_HIDDEN）を除いた、登録がまだの人。
- 同じ日に同じ人へは1回だけ（deal_recordings に storage_path=declare_remind の印を残す）。深夜（23:30〜6:00）は送らない。
- グループ・管理者・施工担当へは送らない。

使い方:
  python3 scripts/declare_remind.py --dry-run   # 誰に送るかだけ表示
  python3 scripts/declare_remind.py             # 送る
  python3 scripts/declare_remind.py --only 林,戸田
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tools", "shuuji"))
import daily_mission as DM  # noqa: E402
from quiet_hours import in_quiet_hours  # noqa: E402

REGISTER_BUTTON = "宣言を登録する"   # webhook.py の HW_TRIGGER と同じ文言


def sb(method, path, data=None):
    base = os.environ["ELP_SUPABASE_URL"].strip("（）()").rstrip("/")
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"].strip("（）()")
    req = urllib.request.Request(f"{base}/rest/v1/{path}", method=method, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    DM.load_env()
    today = dt.datetime.now(DM.JST).date()
    if in_quiet_hours() and not args.dry_run:
        print("深夜のため送りません。")
        return
    try:
        import notion_to_csv as N2C
        hidden = set(N2C.WEEKLY_HIDDEN)
    except Exception as e:  # noqa: BLE001
        print(f"WEEKLY_HIDDEN を読めませんでした（{e}）。対象外は宣言の対象外のみで判定します")
        hidden = set()
    skip = set(DM.NO_HOMEWORK) | hidden
    registered = set(DM.week_homework(today))
    users = sb("GET", "closer_line_users?role=in.(closer,appointer)&closer_name=not.is.null&select=closer_name,line_user_id,role") or []
    marked = {r["line_user_id"] for r in (sb("GET", f"deal_recordings?storage_path=eq.declare_remind&customer_name=eq.{today.isoformat()}&select=line_user_id&limit=1000") or [])}
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    targets = [u for u in users if u["closer_name"] not in skip and u["closer_name"] not in registered
               and u["line_user_id"] not in marked and (not only or u["closer_name"] in only)]
    print(f"今週の宣言: 登録済み{len(registered)}人 / Bot登録の対象{len([u for u in users if u['closer_name'] not in skip])}人 / 催促する{len(targets)}人")
    monday = today - dt.timedelta(days=today.weekday())
    text = ("📝 今週の宣言が、まだ登録されていません\n\n"
            "週次MTで決めた「宣言」と「数字の目標」を、下のボタンから登録してください。\n"
            "（ボタンが消えたときは、「宣言を登録する」と送っても始められます）")
    for u in targets:
        if args.dry_run:
            print("  [DRY]", u["closer_name"])
            continue
        res = DM.line_push(u["line_user_id"], [{"type": "text", "text": text, "quickReply": {"items": [
            {"type": "action", "action": {"type": "message", "label": REGISTER_BUTTON, "text": REGISTER_BUTTON}}]}}])
        print("  ", u["closer_name"], res)
        if res == 200:
            sb("POST", "deal_recordings", [{"line_user_id": u["line_user_id"], "storage_path": "declare_remind", "status": "declare_remind", "customer_name": today.isoformat()}])


if __name__ == "__main__":
    main()
