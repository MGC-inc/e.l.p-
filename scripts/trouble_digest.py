#!/usr/bin/env python3
"""毎日の終礼（19:30 JST）に、Notion「🆘 困ったメモ」を今川さんのLINEへまとめて送る。
終礼で今川さんが解決策を示す。今川さんが終礼に入れない日は、このLINEへの返信でBotが本人に回答を送る。

  python3 scripts/trouble_digest.py [--dry-run]

・メモは要約せず、本人が書いた原文のまま送る（投稿者・日時・場面・本人の解決案を添える）
・「回答待ち」（本人が自分で考えても解決に向かわなかったもの）を先に、そのほか（自己解決・考え中）を後に
・「自己解決中」のまま1時間以上止まっているメモ（解決案を聞かれて返事が止まったもの）は、回答待ちに移してから送る
・前日以前からの持ち越しの回答待ちも、毎回1件ずつ送り直す（どれにでも返信で答えられるように）
・先頭に回答状況（未回答の件数・一番古いものが何日待っているか）を出す
・終礼でその場で解決したものは、そのメッセージに「終礼で解決」と返信すると、本人には送らず記録だけ残る
・送ったメモは「今川共有済み」を立てる。0件の日は何も送らない（配信数を使わない）
・「回答待ち」は1件ずつ別のメッセージで送る。今川さんがそのメッセージにLINEで返信（引用）して答えると、
  Botが本人へ回答を送り、Notionの「回答」と状態（解決）を更新する（line-webhook/api/webhook.py の handle_admin_answer）。
  返信の対応づけ用に、送ったメッセージIDとNotionページIDをSupabase deal_recordings（storage_path='trouble_q'）に残す
・引用が使えないときは「回答 平井：本文」と送ればよい

環境変数: NOTION_TOKEN, DEAL_LINE_CHANNEL_ACCESS_TOKEN, IMAGAWA_LINE_USER_ID, ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY
"""
import datetime as dt
import json
import os
import sys
import urllib.request

TROUBLE_DB = "406da094-9999-81ca-ab25-ff9f1b7933f5"   # 🆘 困ったメモ
LIMIT = 4800


def notion(method: str, path: str, body=None) -> dict:
    req = urllib.request.Request(
        "https://api.notion.com/v1/" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}", "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def plain(page: dict, name: str) -> str:
    p = page["properties"].get(name) or {}
    t, v = p.get("type"), p.get(p.get("type"))
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v or [])
    if t == "select":
        return (v or {}).get("name", "")
    if t == "date":
        return (v or {}).get("start", "")
    if t == "checkbox":
        return "1" if v else ""
    return ""


def supabase(method: str, path: str, data=None):
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(os.environ["ELP_SUPABASE_URL"].rstrip("/") + f"/rest/v1/{path}", method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
        return json.loads(body) if body else None


def query(filter_: dict) -> list[dict]:
    out, cur = [], None
    while True:
        d = notion("POST", f"databases/{TROUBLE_DB}/query", {"filter": filter_, "page_size": 100,
                   "sorts": [{"property": "投稿日時", "direction": "ascending"}], **({"start_cursor": cur} if cur else {})})
        out += d["results"]
        if not d.get("has_more"):
            return out
        cur = d["next_cursor"]


def entry(p: dict) -> str:
    when = plain(p, "投稿日時")[5:16].replace("T", " ").replace("-", "/")
    scene = f"・場面：{plain(p, '起きる場面')}" if plain(p, "起きる場面") else ""
    s = f"■ {plain(p, '投稿者')}（{when}{scene}）\n{plain(p, '原文')}"
    if plain(p, "本人の解決案"):
        s += f"\n  └ 本人の解決案：{plain(p, '本人の解決案')}"
    return s


def split(text: str) -> list[str]:
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > LIMIT and cur:
            parts.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + block[:LIMIT]
    return parts + ([cur] if cur else [])


def main() -> None:
    dry = "--dry-run" in sys.argv
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    # 解決案を聞かれたまま止まっているメモは、誰も答えないまま残らないよう回答待ちに移す
    for p in query({"property": "状態", "select": {"equals": "自己解決中"}}):
        posted = plain(p, "投稿日時")
        if posted and now - dt.datetime.fromisoformat(posted) > dt.timedelta(hours=1):
            print("回答待ちに移す:", plain(p, "投稿者"), posted[:16])
            if not dry:
                notion("PATCH", f"pages/{p['id']}", {"properties": {"状態": {"select": {"name": "回答待ち"}}}})
                p["properties"]["状態"] = {"type": "select", "select": {"name": "回答待ち"}}
    new = query({"property": "今川共有済み", "checkbox": {"equals": False}})
    new_ids = {p["id"] for p in new}
    waiting_all = query({"property": "状態", "select": {"equals": "回答待ち"}})
    if dry:   # 試し実行では上の書き換えをしていないので、移す予定のものも回答待ちとして数える
        moved = [p for p in new if plain(p, "状態") == "自己解決中"
                 and now - dt.datetime.fromisoformat(plain(p, "投稿日時")) > dt.timedelta(hours=1)]
        waiting_all += [p for p in moved if p["id"] not in {w["id"] for w in waiting_all}]
    waiting_new = [p for p in waiting_all if p["id"] in new_ids]
    carry = [p for p in waiting_all if p["id"] not in new_ids]
    others = [p for p in new if p["id"] not in {w["id"] for w in waiting_all}]
    if not new and not carry:
        print("新しい困ったメモも、未回答のメモもありません。送信しません。")
        return

    def days_waiting(p: dict) -> int:
        return (now.date() - dt.date.fromisoformat(plain(p, "投稿日時")[:10])).days
    summary = [f"🆘 終礼の困ったメモ（{now.month}/{now.day}）"]
    if waiting_all:
        oldest = max(waiting_all, key=days_waiting)
        summary.append(f"📊 回答状況：未回答 {len(waiting_all)}件（今日の新規{len(waiting_new)}件・持ち越し{len(carry)}件）"
                       f"／一番古いもの {days_waiting(oldest)}日待ち（{plain(oldest, '投稿者')} {plain(oldest, '投稿日時')[5:10].replace('-', '/')}）")
    else:
        summary.append("📊 回答状況：未回答 0件")
    if others:
        summary += [f"【自己解決・考え中 {len(others)}件】（回答は不要。様子だけ共有）"] + [entry(p) for p in others]
    if waiting_all:
        summary.append("回答待ちは、次のメッセージに1件ずつ送ります。終礼で解決策を示してください。\n"
                       "終礼に入れないときは、各メッセージに返信（引用）で答えると本人へそのまま届きます。"
                       "終礼で解決したものは「終礼で解決」と返信すると、記録だけ残ります。")
    messages: list[tuple[str, str | None]] = [(t, None) for t in split("\n\n".join(summary))]
    for p in waiting_new + sorted(carry, key=days_waiting, reverse=True):
        tag = "❓回答待ち（今日）" if p["id"] in new_ids else f"❓回答待ち（持ち越し・{days_waiting(p)}日目）"
        messages.append((f"{tag}\n{entry(p)}\n\n↩ このメッセージに返信して回答すると、{plain(p, '投稿者')}さんに届きます。", p["id"]))
    print("\n---\n".join(t for t, _ in messages))
    if dry:
        return
    to = os.environ["IMAGAWA_LINE_USER_ID"]
    for i in range(0, len(messages), 5):   # LINEは1回のpushで5通まで
        chunk = messages[i:i + 5]
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/message/push", method="POST",
            data=json.dumps({"to": to, "messages": [{"type": "text", "text": t} for t, _ in chunk]}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['DEAL_LINE_CHANNEL_ACCESS_TOKEN']}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            sent = json.loads(r.read()).get("sentMessages", [])
        print("LINE: ok", len(sent))
        for (_, page_id), m in zip(chunk, sent):
            if page_id:
                supabase("POST", "deal_recordings", {"line_user_id": to, "storage_path": "trouble_q", "status": "trouble_q",
                                                      "customer_name": m["id"], "notion_url": page_id})
    for p in new:
        notion("PATCH", f"pages/{p['id']}", {"properties": {"今川共有済み": {"checkbox": True}}})


if __name__ == "__main__":
    main()
