#!/usr/bin/env python3
"""毎晩21時（JST）に、Notion「🆘 困ったメモ」の未共有分を今川さんのLINEへまとめて送る。

  python3 scripts/trouble_digest.py [--dry-run]

・メモは要約せず、本人が書いた原文のまま送る（投稿者・日時・場面・本人の解決案を添える）
・「回答待ち」（本人が自分で考えても解決に向かわなかったもの）を先に、そのほか（自己解決・考え中）を後に
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
    new = query({"property": "今川共有済み", "checkbox": {"equals": False}})
    waiting = [p for p in new if plain(p, "状態") == "回答待ち"]
    others = [p for p in new if plain(p, "状態") != "回答待ち"]
    carry = [p for p in query({"and": [{"property": "今川共有済み", "checkbox": {"equals": True}},
                                      {"property": "状態", "select": {"equals": "回答待ち"}}]})]
    if not new:
        print("新しい困ったメモはありません。送信しません。")
        return
    summary = [f"🆘 今日の困ったメモ（{len(new)}件）"]
    if others:
        summary += [f"【自己解決・考え中 {len(others)}件】"] + [entry(p) for p in others]
    if carry:
        summary += [f"【持ち越しの回答待ち {len(carry)}件】" + "、".join(f"{plain(p, '投稿者')}({plain(p, '投稿日時')[5:10].replace('-', '/')})" for p in carry)
                    + "\n（返信は「回答 名前：本文」でも送れます）"]
    if waiting:
        summary.append(f"【回答待ち {len(waiting)}件】は、次のメッセージに1件ずつ送ります。各メッセージに返信（引用）で答えると、本人へそのまま届きます。")
    messages: list[tuple[str, str | None]] = [(t, None) for t in split("\n\n".join(summary))]
    for p in waiting:
        messages.append((f"❓回答待ち\n{entry(p)}\n\n↩ このメッセージに返信して回答すると、{plain(p, '投稿者')}さんに届きます。", p["id"]))
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
