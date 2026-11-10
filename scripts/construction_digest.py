#!/usr/bin/env python3
"""施工案件の更新を、1日分まとめて夜に今川さんへ確認する（施工担当へは、承認されたときだけ送る）。

  python3 scripts/construction_digest.py --dry-run     # まとめの文面だけ見る（誰にも送らない）
  python3 scripts/construction_digest.py               # 今川さんに「送る／見送り」の確認を送る

流れ（2026-11-10 今川さん指示）:
  1. 営業マンが契約後確認のボタンを押すと、line-webhook/api/webhook.py が更新を deal_recordings
     （storage_path＝construction_event, status＝event_pending）にためる。この時点では誰にも送らない
  2. このスクリプトが、ためた更新を1つのまとめにして、今川さんに確認のLINE（［施工担当へ送る］［見送り］）を送る
  3. 今川さんが［施工担当へ送る］を押したときだけ、bot が施工担当（role＝construction）の個別LINEへ、
     同じ内容を送る（3名とも同じ内容）。LINEグループへは送らない。［見送り］なら送らない

まとめの内容は、お客様名・営業担当・施工店・工事日・更新の種類だけ（住所・電話番号は載せない）。
同じ案件の更新が複数あるときは、いちばん新しいものだけ載せる。

環境変数: ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY, DEAL_LINE_CHANNEL_ACCESS_TOKEN
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import load_env  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
ADMIN_NAME = "今川"
SECTIONS = [("確定", "✅ 確定（契約後の確認が済んだ案件）"), ("中止", "⛔ 中止（クーリングオフ・審査落ち）"),
            ("延期", "📅 延期（新しい工事日が決まった）"), ("調整中", "⏳ 延期・お客様と調整中")]


def sb(method: str, path: str, data=None):
    base, key = os.environ["ELP_SUPABASE_URL"].rstrip("/"), os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/rest/v1/{path}", method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json", "Prefer": "return=representation"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def line_push(to: str, messages: list) -> int | str:
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": to, "messages": messages}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code} {e.read().decode()[:200]}"


def line_of(e: dict) -> str:
    kind = e["kind"]
    work = e.get("work") or "工事日未定"
    shop = f"｜{e['shop']}" if e.get("shop") else ""
    head = f"・{e['name']}｜営業：{e['closer']}{shop}"
    if kind == "確定":
        return f"{head}｜工事日：{work}"
    if kind == "中止":
        return f"{head}｜元の工事日：{work}｜理由：{e.get('detail') or '契約取り消し'}"
    if kind == "延期":
        return f"{head}｜新しい工事日：{e.get('detail') or '未定'}"
    return f"{head}｜元の工事日：{work}｜日程は調整中"


def build_text(events: list[dict], today: dt.date) -> str:
    lines = [f"【施工案件の更新まとめ {today.month}/{today.day}】", "営業マンから、契約後の確認で次の報告がありました。"]
    for kind, title in SECTIONS:
        items = [e for e in events if e["kind"] == kind]
        if items:
            lines += ["", title] + [line_of(e) for e in items]
    lines += ["", "工事日の調整・確定は、botを通して進めます。"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()
    today = dt.datetime.now(JST).date()

    rows = sb("GET", "deal_recordings?storage_path=eq.construction_event&status=eq.event_pending"
                     "&order=received_at.asc&select=id,customer_name") or []
    if not rows:
        print("施工側へ伝える更新はありません。")
        return
    latest: dict[str, dict] = {}   # 同じ案件は新しい更新だけ載せる
    for r in rows:
        e = json.loads(r["customer_name"])
        e["_id"] = r["id"]
        latest[e["deal"]] = e
    events = list(latest.values())
    text = build_text(events, today)
    if args.dry_run:
        print(text)
        print(f"\n（更新{len(rows)}件／案件{len(events)}件。--dry-run のため送っていません）")
        return

    admin = sb("GET", f"closer_line_users?closer_name=eq.{urllib.parse.quote(ADMIN_NAME)}&select=line_user_id") or []
    if not admin:
        sys.exit("今川さんのLINE user IDが見つかりません。")
    all_ids = [r["id"] for r in rows]
    digest = sb("POST", "deal_recordings", {"line_user_id": admin[0]["line_user_id"], "storage_path": "construction_digest",
                                           "status": "awaiting_approval",
                                           "customer_name": json.dumps({"text": text, "events": all_ids}, ensure_ascii=False)})[0]
    sb("PATCH", f"deal_recordings?id=in.({','.join(str(i) for i in all_ids)})", {"status": "event_digested"})
    msg = [{"type": "text", "text": "🏗 施工担当への連絡まとめです。内容を確認して、送ってよければ［施工担当へ送る］を押してください。"
                                    "（押すまでは誰にも送りません）"},
           {"type": "text", "text": text, "quickReply": {"items": [
               {"type": "action", "action": {"type": "message", "label": "施工担当へ送る", "text": f"施工連絡 承認 {digest['id']}"}},
               {"type": "action", "action": {"type": "message", "label": "見送り", "text": f"施工連絡 見送り {digest['id']}"}}]}}]
    res = line_push(admin[0]["line_user_id"], msg)
    print("今川さんへ確認を送りました:", res, f"（案件{len(events)}件）")


if __name__ == "__main__":
    main()
