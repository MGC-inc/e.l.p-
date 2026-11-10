#!/usr/bin/env python3
"""契約後の確認連絡（お客様への電話）の催促。工事日の週の1つ前の水曜の午前中に、その案件の営業マンへLINEで送る。

  python3 scripts/post_contract_check.py --dry-run          # 送らずに、誰に何件出るかだけ見る
  python3 scripts/post_contract_check.py                    # 送る（水曜のみ。同じ日に同じ人へは1回だけ）
  python3 scripts/post_contract_check.py --only 岡野 --force

目的（2026-11-10 今川さん指示）: 工事日確定の案件を、クーリングオフや審査落ちの可能性がほとんどなくなってから施工側に上げるため、
契約したお客様へ電話で確認連絡をするのを必須の工程にする。営業マンにはこのLINEで催促し、ボタンで進捗を残してもらう。

いつ送るか: 工事日（Notionの施工案件管理ページ「施工案件一覧」の施工日時）が入っている週（月〜日）の、1つ前の水曜。
  例）工事日が 11/17(火) → その週の月曜は 11/16 → 11/11(水)に送る。
  その水曜を過ぎていても、工事日の前日までは、次の水曜に出る（直前に日程が決まった案件の取りこぼし防止）。
  「工事日延期」と答えた案件は、新しい工事予定日を基準にもう一度出る。
対象の案件: 商談案件一覧で 結果＝契約／完工確認済みでない／「契約後確認」が「確認済み」でない、かつ施工案件（商談案件に紐づく）の
  進捗が「完工」「中止」でないもの。すでに結果が変わった案件（クーリングオフ等）は結果＝契約でないので送らない。
  施工案件に紐づいていない契約・施工日時が空の契約は送れないので、実行結果の末尾に一覧で出す。

カードのボタン（返事は line-webhook/api/webhook.py の handle_postcheck_button が受けて Notion を更新する）:
  確認した → 契約後確認＝確認済み（施工側へ送る連絡文も返す）／既にクーリングオフ・審査落ちだった → 結果を変更（確認つき）／
  工事日延期 → 新しい工事日を聞いて記録（施工案件も「延期・再調整」に）
完工日はここでは書かない（完工報告が入ったときだけ記録する）。

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
CONSTRUCTION_DS = "387d697b-920f-4baf-8e68-a20e5ac69d00"   # 施工案件一覧（施工案件管理ページ。商談案件に紐づく）
DONE_PROGRESS = {"完工", "中止"}
JST = ZoneInfo("Asia/Tokyo")
BUCKET = "weekly-pdca"
PREFIX = "契約後確認 "                              # line-webhook/api/webhook.py の PC_PREFIX と同じ
MAX_PER_CAROUSEL = 12


def date_of(page: dict, name: str) -> str:
    p = (page["properties"].get(name) or {}).get("date") or {}
    return (p.get("start") or "")[:10]


def contract_day(page: dict) -> str:
    return date_of(page, "契約日") or date_of(page, "商談日時")


def notice_wednesday(work_day: dt.date) -> dt.date:
    """工事日の週（月〜日）の、1つ前の水曜。"""
    monday = work_day - dt.timedelta(days=work_day.weekday())
    return monday - dt.timedelta(days=5)


def relation_ids(page: dict, name: str) -> list[str]:
    return [r["id"] for r in ((page["properties"].get(name) or {}).get("relation") or [])]


def work_info(deal: dict, construction: dict | None) -> tuple[str, str]:
    """（工事日 YYYY-MM-DD, 表示用）。延期と答えた案件は工事予定日、それ以外は施工案件の施工日時。"""
    if prop_text(deal, "契約後確認") == "工事日延期" and date_of(deal, "工事予定日"):
        return date_of(deal, "工事予定日"), "延期後"
    if construction:
        return date_of(construction, "施工日時"), "施工"
    return "", ""


def pending_deals(today: dt.date, since: str) -> tuple[list[dict], list[str]]:
    """戻り値: (催促する案件のリスト, 送れなかった契約の説明)。案件には _work（工事日）と _cons（施工案件）を付ける。"""
    deals = query_all(f"/data_sources/{DEAL_DS}/query", {"and": [
        {"property": "結果", "status": {"equals": "契約"}},
        {"property": "完工確認済み", "checkbox": {"equals": False}},
        {"property": "クローザー", "select": {"is_not_empty": True}}]}, "2025-09-03")
    cons_by_deal: dict[str, dict] = {}
    for c in query_all(f"/data_sources/{CONSTRUCTION_DS}/query", None, "2025-09-03"):
        for did in relation_ids(c, "商談案件"):
            prev = cons_by_deal.get(did)
            if not prev or date_of(c, "施工日時") > date_of(prev, "施工日時"):   # 再工事など複数あるときは新しい方
                cons_by_deal[did] = c
    out, unsent = [], []
    for p in deals:
        day = contract_day(p)
        if not day or day < since or prop_text(p, "契約後確認") == "確認済み":
            continue
        cons = cons_by_deal.get(p["id"])
        if cons and prop_text(cons, "進捗") in DONE_PROGRESS:
            continue
        work, _ = work_info(p, cons)
        label = f"{prop_text(p, 'クローザー')}／{prop_text(p, 'お客様名')}（{day[5:]}契約）"
        if not cons and not work:
            unsent.append(f"{label}: 施工案件に紐づいていません")
            continue
        if not work:
            unsent.append(f"{label}: 施工日時が未入力です")
            continue
        wd = dt.date.fromisoformat(work)
        if wd <= today:
            continue   # 工事当日・工事日を過ぎた案件は、この催促の対象外（完工の確認へ）
        if today < notice_wednesday(wd):
            continue   # まだ、送る水曜になっていない
        p["_work"], p["_cons"] = work, cons
        out.append(p)
    return out, unsent


def bubble(p: dict) -> dict:
    name = prop_text(p, "お客様名") or "（名前なし）"
    amount = (p["properties"].get("売上金額") or {}).get("number")
    money = f"{amount / 10000:g}万円" if amount else "金額未入力"
    w = p["_work"]
    sub = f"{int(contract_day(p)[5:7])}/{int(contract_day(p)[8:10])} 契約｜{money}\n工事日 {int(w[5:7])}/{int(w[8:10])}"
    if prop_text(p, "契約後確認") == "工事日延期":
        sub += "（延期後）"

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
            "工事日が来週以降の案件です。契約したお客様へ電話で確認連絡をして、クーリングオフや審査落ちの心配がなくなったことを確かめてから、"
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
    ap.add_argument("--since", default="2026-10-01", help="この日以降の契約だけ対象（古い契約への催促を防ぐ）")
    ap.add_argument("--date", help="今日の日付（テスト用 YYYY-MM-DD）")
    ap.add_argument("--any-day", action="store_true", help="水曜以外でも送る（通常は水曜だけ。--dry-run は曜日を問わない）")
    args = ap.parse_args()
    load_env()
    today = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(JST).date()

    if today.weekday() != 2 and not (args.dry_run or args.any_day):
        print(f"{today} は水曜ではないので送りません（水曜の午前中に送る運用）。")
        return
    pending, unsent = pending_deals(today, args.since)
    by_closer: dict[str, list[dict]] = {}
    for p in pending:
        by_closer.setdefault(prop_text(p, "クローザー"), []).append(p)
    if unsent:
        print("【送れなかった契約（施工案件の入力待ち）】\n" + "\n".join("・" + u for u in unsent))
    if not by_closer:
        print("今日、契約後の確認連絡を送る案件はありません。")
        return

    users = {u["closer_name"]: u["line_user_id"] for u in json.loads(
        sb("rest/v1/closer_line_users?role=eq.closer&select=closer_name,line_user_id")) if u.get("closer_name")}
    done = set() if (args.dry_run or args.force) else sent_names(today)
    sent = set(done)
    for name, deals in sorted(by_closer.items()):
        if args.only and name != args.only:
            continue
        deals.sort(key=lambda p: p['_work'])
        labels = "、".join(f"{prop_text(p, 'お客様名')}(工事{p['_work'][5:]})" for p in deals)
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
