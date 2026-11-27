#!/usr/bin/env python3
"""施工案件の「浮いている・足りない」案件を探して、あれば今川さんだけに知らせる（毎晩）。

  python3 scripts/construction_watch.py --dry-run     # 文面だけ見る（誰にも送らない）
  python3 scripts/construction_watch.py               # 該当があれば今川さんへLINE（0件なら何も送らない）

見るもの（Notion 施工案件一覧のうち、完工・中止ではないもの）:
  1. 施工店が未設定（G.LにもAZにも振られていない）
  2. 日程調整中のまま施工日時が無く、登録から3日以上たっている（未確定のまま浮いている）
  3. 施工日を過ぎているのに、完工になっていない
  4. AZの案件が、AZのグループで共有された形跡がない（botが読んだAZグループの発言に、お客様名が出てこない）
     ※ AZグループが未登録・発言なしのときは、確認できないことだけを1行伝える
  5. AZの案件で、施工内容に浄水器・バブルがあるのに、機器の発送が済んでいない
     （施工日が7日以内で、機器発送状況が「発送済み・到着確認済み・対象外」ではない。14日以内で未入力のときも出す）
  6. 「要確認」が立ったまま、施工日が3日以内に迫っている

G.L→AZに振る運用（G.Lが全案件を見て、対応できない案件だけAZへ。G.L優先）。グループへは何も送らない。

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
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import load_env, query_all  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
CONSTRUCTION_DS = "387d697b-920f-4baf-8e68-a20e5ac69d00"
ADMIN_NAME = "今川"
SHIPPED = {"発送済み", "到着確認済み", "対象外"}
EQUIP = {"浄水器", "バブル"}


def plain(page: dict, name: str):
    p = page["properties"].get(name) or {}
    t, v = p.get("type"), p.get(p.get("type"))
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v or [])
    if t == "select":
        return (v or {}).get("name", "")
    if t == "multi_select":
        return [x["name"] for x in v or []]
    if t == "date":
        return (v or {}).get("start", "") or ""
    if t == "checkbox":
        return bool(v)
    return ""


def sb(path: str):
    base, key = os.environ["ELP_SUPABASE_URL"].rstrip("/"), os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/rest/v1/{path}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"[]")


def line_push(to: str, text: str):
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": to, "messages": [{"type": "text", "text": text[:4800]}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code} {e.read().decode()[:200]}"


def base_name(title: str) -> str:
    t = re.sub(r"[（(].*?[）)]", "", title)
    return re.sub(r"(邸|様|さん)$", "", t.strip())


def az_group_texts() -> tuple[str, list[str]]:
    """(状況, AZグループの直近14日の発言) 状況: ok / no_label / no_messages"""
    labels = {r["line_user_id"]: json.loads(r["customer_name"]).get("shop", "")
              for r in sb("deal_recordings?storage_path=eq.group_label&select=line_user_id,customer_name")}
    az = {g for g, s in labels.items() if s == "AZ"}
    if not az:
        return "no_label", []
    since = (dt.datetime.now(JST) - dt.timedelta(days=14)).isoformat()
    rows = sb(f"deal_recordings?storage_path=eq.group_message&received_at=gte.{urllib.parse.quote(since)}&select=customer_name&limit=1000")
    texts = []
    for r in rows:
        m = json.loads(r["customer_name"])
        if m.get("group") in az:
            texts.append(m.get("text") or "")
    return ("ok" if texts else "no_messages"), texts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dry_run:
        from quiet_hours import in_quiet_hours
        if in_quiet_hours():   # 質問・確認は0:00にまとめて送る（0:30〜6:00は送らない。今川さん指示 2026-11-13）
            print("0:30〜6:00は送らない設定のため、今回は送信を見送ります（次の0:00に送ります）。")
            return
    load_env()
    from quiet_hours import business_date
    today = business_date()   # 0:00に動くときは、いま終わった日を「その日」にする
    pages = query_all(f"/data_sources/{CONSTRUCTION_DS}/query",
                      {"and": [{"property": "進捗", "select": {"does_not_equal": "完工"}},
                               {"property": "進捗", "select": {"does_not_equal": "中止"}}]}, "2025-09-03")
    status, az_texts = az_group_texts()
    out = {"施工店が未設定": [], "日程が未確定のまま": [], "施工日を過ぎても未完工": [], "AZのグループで共有された形跡がない": [],
           "浄水器・バブルの発送が未完了（AZ）": [], "要確認のまま施工日が近い": []}
    for p in pages:
        title, shop = plain(p, "案件"), plain(p, "施工店")
        work = plain(p, "施工日時")[:10]
        wd = dt.date.fromisoformat(work) if work else None
        days = (wd - today).days if wd else None
        created = dt.date.fromisoformat(p["created_time"][:10])
        prog = plain(p, "進捗")
        label = f"{title}" + (f"（{wd.month}/{wd.day}）" if wd else "")
        if not shop:
            out["施工店が未設定"].append(label)
        if prog == "日程調整中" and not wd and (today - created).days >= 3:
            out["日程が未確定のまま"].append(f"{title}（登録から{(today - created).days}日）")
        if wd and days < 0:
            out["施工日を過ぎても未完工"].append(f"{title}（{wd.month}/{wd.day}・{prog}）")
        if plain(p, "要確認") and wd and 0 <= days <= 3:
            out["要確認のまま施工日が近い"].append(f"{label}")
        if shop == "AZ":
            if status == "ok" and base_name(title) and not any(base_name(title) in t for t in az_texts):
                out["AZのグループで共有された形跡がない"].append(label)
            eq = [x for x in plain(p, "施工内容") if x in EQUIP]
            ship = plain(p, "機器発送状況")
            if eq and wd and days >= 0 and ship not in SHIPPED and (days <= 7 or (not ship and days <= 14)):
                out["浄水器・バブルの発送が未完了（AZ）"].append(f"{title}｜{'・'.join(eq)}｜{wd.month}/{wd.day}｜発送：{ship or '未入力'}")

    lines = [f"【施工案件の確認 {today.month}/{today.day}】"]
    total = 0
    for k, v in out.items():
        if v:
            total += len(v)
            lines += ["", f"■ {k}（{len(v)}件）"] + [f"・{x}" for x in v]
    if status == "no_label":
        lines += ["", "※ AZのグループがまだ登録されていないため、「AZのグループで共有されたか」は確認できていません。"]
    elif status == "no_messages":
        lines += ["", "※ 直近14日のAZグループの発言をbotがまだ読めていないため、共有の確認はできていません。"]
    if total == 0 and status == "ok":
        print("見つかった問題はありません。")
        return
    if total == 0:
        lines.insert(1, "足りない案件は見つかりませんでした。")
    text = "\n".join(lines)
    if args.dry_run:
        print(text)
        print(f"\n（{total}件。--dry-run のため送っていません）")
        return
    admin = sb(f"closer_line_users?closer_name=eq.{urllib.parse.quote(ADMIN_NAME)}&select=line_user_id")
    if not admin:
        sys.exit("今川さんのLINE user IDが見つかりません。")
    if total == 0:
        print("問題なし（AZグループ未確認の注記のみ）。送りません。")
        return
    print("今川さんへ送りました:", line_push(admin[0]["line_user_id"], text), f"（{total}件）")


if __name__ == "__main__":
    main()
