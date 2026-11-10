#!/usr/bin/env python3
"""契約後の確認連絡（お客様への電話）の催促。毎週水曜に、まだの契約案件があるクローザーへLINEで送る。

  python3 scripts/post_contract_check.py --dry-run          # 送らずに、誰に何件出るかだけ見る
  python3 scripts/post_contract_check.py                    # 送る（同じ日に同じ人へは1回だけ）
  python3 scripts/post_contract_check.py --only 岡野 --force

目的（2026-11-10 今川さん指示）: 工事日確定の案件を、クーリングオフや審査落ちの可能性がほとんどなくなってから上げるため、
契約したお客様へ電話で確認連絡をするのを必須の工程にする。営業マンにはこのLINEで催促し、ボタンで進捗を残してもらう。

対象の案件（商談案件一覧）:
  結果＝契約／完工確認済みでない／契約日（なければ商談日）が --since 以降で、--min-days 日以上前
  かつ「契約後確認」が空、または「工事日延期」で工事予定日が今日までに来たもの

カードのボタン（返事は line-webhook/api/webhook.py の handle_postcheck_button が受けて Notion を更新する）:
  確認した → 契約後確認＝確認済み／既にクーリングオフ・審査落ちだった → 結果を変更（確認つき）／工事日延期 → 新しい工事日を聞いて記録

環境変数: NOTION_TOKEN, ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY, DEAL_LINE_CHANNEL_ACCESS_TOKEN
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import load_env, prop_text, query_all  # noqa: E402

DEAL_DS = "8958bbaf-2c24-4e94-97b2-4c801e60cb37"   # ✅ 商談案件一覧（data_source_id）
JST = ZoneInfo("Asia/Tokyo")
BUCKET = "weekly-pdca"
PREFIX = "契約後確認 "                              # line-webhook/api/webhook.py の PC_PREFIX と同じ
MAX_PER_CAROUSEL = 12


def date_of(page: dict, name: str) -> str:
    p = (page["properties"].get(name) or {}).get("date") or {}
    return (p.get("start") or "")[:10]


def contract_day(page: dict) -> str:
    return date_of(page, "契約日") or date_of(page, "商談日時")


def pending_deals(today: dt.date, since: str, min_days: int) -> list[dict]:
    pages = query_all(f"/data_sources/{DEAL_DS}/query", {"and": [
        {"property": "結果", "status": {"equals": "契約"}},
        {"property": "完工確認済み", "checkbox": {"equals": False}},
        {"property": "クローザー", "select": {"is_not_empty": True}}]}, "2025-09-03")
    limit = (today - dt.timedelta(days=min_days)).isoformat()
    out = []
    for p in pages:
        day = contract_day(p)
        if not day or day < since or day > limit:
            continue
        state = prop_text(p, "契約後確認")
        if state == "確認済み":
            continue
        if state == "工事日延期" and (date_of(p, "工事予定日") or "9999") > today.isoformat():
            continue   # 延期後の工事日がまだ先なら、その日が来るまで催促しない
        out.append(p)
    return out


def bubble(p: dict) -> dict:
    name = prop_text(p, "お客様名") or "（名前なし）"
    amount = (p["properties"].get("売上金額") or {}).get("number")
    money = f"{amount / 10000:g}万円" if amount else "金額未入力"
    sub = f"{int(contract_day(p)[5:7])}/{int(contract_day(p)[8:10])} 契約｜{money}"
    if prop_text(p, "契約後確認") == "工事日延期":
        d = date_of(p, "工事予定日")
        sub += f"｜延期後の工事日 {int(d[5:7])}/{int(d[8:10])}" if d else ""

    def btn(label: str, cmd: str, style: str) -> dict:
        return {"type": "button", "style": style, "height": "sm",
                "action": {"type": "message", "label": label, "text": f"{PREFIX}{cmd} {p['id']}"}}

    return {"type": "bubble", "size": "kilo",
            "header": {"type": "box", "layout": "vertical", "backgroundColor": "#1F6FEB", "paddingAll": "12px",
                       "contents": [{"type": "text", "text": "📞 契約後の確認連絡", "size": "sm", "color": "#FFFFFF", "weight": "bold"}]},
            "body": {"type": "box", "layout": "vertical", "spacing": "xs",
                     "contents": [{"type": "text", "text": name, "weight": "bold", "size": "lg", "wrap": True},
                                  {"type": "text", "text": sub, "size": "xs", "color": "#666666", "wrap": True}]},
            "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                btn("確認した", "確認した", "primary"),
                btn("工事日延期", "延期", "secondary"),
                btn("既にクーリングオフ", "クーリングオフ", "secondary"),
                btn("審査落ちだった", "審査落ち", "secondary")]}}


def lead_text(name: str, n: int) -> str:
    return (f"📞 {name}さん、契約後の確認連絡はお済みですか？（{n}件）\n\n"
            "契約したお客様へ電話で確認連絡をして、クーリングオフや審査落ちの心配がなくなったことを確かめてから、"
            "工事日確定の案件にします。\n連絡できたら下のカードの［確認した］を押してください。\n"
            "・もう結果が変わっている案件は、［既にクーリングオフ］［審査落ちだった］を押してください\n"
            "・工事日が延びた案件は、［工事日延期］を押すと新しい日を聞きます")


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


def sb(path: str, data: bytes | None = None, method: str = "GET", headers: dict | None = None):
    base, key = os.environ["ELP_SUPABASE_URL"].rstrip("/"), os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/{path}", data=data, method=method,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def sent_names(day: dt.date) -> set[str]:
    try:
        return set(json.loads(sb(f"storage/v1/object/{BUCKET}/post-contract-check/{day.isoformat()}.json")))
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return set()
        raise


def save_sent(day: dt.date, names: set[str]) -> None:
    sb(f"storage/v1/object/{BUCKET}/post-contract-check/{day.isoformat()}.json",
       json.dumps(sorted(names), ensure_ascii=False).encode(), "POST",
       {"Content-Type": "application/json", "x-upsert": "true"})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="", help="このクローザーだけ")
    ap.add_argument("--force", action="store_true", help="今日すでに送った人にも送る")
    ap.add_argument("--since", default="2026-11-01", help="この日以降の契約だけ対象（古い契約への一斉催促を防ぐ）")
    ap.add_argument("--min-days", type=int, default=1, help="契約からこの日数以上たった案件だけ（既定1＝契約当日は除く）")
    ap.add_argument("--date", help="今日の日付（テスト用 YYYY-MM-DD）")
    args = ap.parse_args()
    load_env()
    today = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(JST).date()

    by_closer: dict[str, list[dict]] = {}
    for p in pending_deals(today, args.since, args.min_days):
        by_closer.setdefault(prop_text(p, "クローザー"), []).append(p)
    if not by_closer:
        print("契約後の確認連絡がまだの案件はありません。")
        return

    users = {u["closer_name"]: u["line_user_id"] for u in json.loads(
        sb("rest/v1/closer_line_users?role=eq.closer&select=closer_name,line_user_id")) if u.get("closer_name")}
    done = set() if (args.dry_run or args.force) else sent_names(today)
    sent = set(done)
    for name, deals in sorted(by_closer.items()):
        if args.only and name != args.only:
            continue
        deals.sort(key=contract_day)
        labels = "、".join(f"{prop_text(p, 'お客様名')}({contract_day(p)[5:]})" for p in deals)
        if name in done:
            print(f"[送信済み] {name}: {len(deals)}件")
            continue
        to = users.get(name)
        if args.dry_run or not to:
            print(f"[{'DRY' if args.dry_run else 'LINE未登録'}] {name}: {len(deals)}件 {labels}")
            continue
        msgs = [{"type": "text", "text": lead_text(name, len(deals))}]
        for i in range(0, len(deals), MAX_PER_CAROUSEL):
            msgs.append({"type": "flex", "altText": "契約後の確認連絡の催促",
                         "contents": {"type": "carousel", "contents": [bubble(p) for p in deals[i:i + MAX_PER_CAROUSEL]]}})
        res = line_push(to, msgs[:5])
        print(name, res, labels)
        if res == 200:
            sent.add(name)
    if not args.dry_run and not args.force and sent != done:
        save_sent(today, sent)


if __name__ == "__main__":
    main()
