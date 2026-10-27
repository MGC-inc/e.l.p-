#!/usr/bin/env python3
"""商談分析ページの「■5 刺さったトーク」のうち、うまくいった表現（効果的だった表現と理由）を
Notion「🗣 うまくいったトーク集」に取り込む。LINE Botのクローザー用メニュー
「全員の商談のうまくいったトーク」が、このDBを読んで誰のどの商談分析から引用したかまで表示する。

  python3 scripts/talk_share_sync.py [--days 21] [--dry-run]

・対象: 「DB 商談分析＆アポ分析」で 分析済み=✔ かつ 商談日時が直近N日の商談（採点があるもの）
・取込済みの商談（取込キー=商談ページID）は飛ばす。同じ商談を2回取り込まない
・引用は分析ページの本文そのまま（要約しない）
・場面（フェーズ）はGemini（GEMINI_API_KEY）が引用と理由から7つのどれかに分類する。使えないときはキーワードで推定
・--reclassify で、取り込み済みの全トークの場面を分類し直す
・商談分析（商談録音分析スキル）がNotionページを作った直後にも実行する（--days 3 で十分）

環境変数: NOTION_TOKEN, GEMINI_API_KEY（無ければキーワード推定）
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEAL_DB = "aa496718-e62f-4a70-818d-953492cad435"
TALK_DB = "406da094-9999-81e7-8615-d19651c1f165"  # 🗣 うまくいったトーク集
NOTION_VERSION = "2022-06-28"
MAX_PER_DEAL = 4
MIN_SCORE = 60  # 採点がこれ未満の商談は「うまくいったトーク」として載せない
PHASES = [  # 先に当てはまったものを採用（目安の推定）
    ("価格提示", ("価格", "金額", "月額", "ローン", "総額")),
    ("クロージング", ("クロージング", "決断", "沈黙", "契約の", "背中")),
    ("反論処理", ("切り返し", "反論", "ネック", "懸念", "不安", "保留", "断り")),
    ("ヒアリング", ("ヒアリング", "質問", "聞き出", "深掘り", "自己開示")),
    ("提案", ("提案", "保証", "設備", "デモ", "浄水器", "バブル", "メリット")),
    ("入り・信頼構築", ("アイスブレイク", "雑談", "共感", "ラポ", "信頼", "挨拶", "入り")),
]
TIME_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?(?:\s*[〜~\-ー]\s*\d{1,2}:\d{2}(?::\d{2})?)?\](?:\s*[〜~\-ー]\s*\[\d{1,2}:\d{2}(?::\d{2})?\])?")


def call(method: str, path: str, body=None) -> dict:
    req = urllib.request.Request(
        "https://api.notion.com/v1/" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}", "Notion-Version": NOTION_VERSION,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def children(block_id: str) -> list[dict]:
    out, cur = [], None
    while True:
        d = call("GET", f"blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cur}" if cur else ""))
        out += d["results"]
        if not d.get("has_more"):
            return out
        cur = d["next_cursor"]


def block_text(b: dict) -> str:
    inner = b.get(b["type"], {})
    return "".join(t.get("plain_text", "") for t in inner.get("rich_text", []))


def flatten(block: dict, out: list[str]) -> None:
    out.append(block_text(block))
    if block.get("has_children"):
        for ch in children(block["id"]):
            flatten(ch, out)


def section5_lines(page_id: str) -> list[str]:
    """■5の見出しから■6の手前までの本文（入れ子も含めて上から順に）。"""
    lines, inside = [], False
    for b in children(page_id):
        t = block_text(b).strip()
        if t.startswith("■5"):
            inside = True
            continue
        if inside and t.startswith("■6"):
            break
        if inside:
            flatten(b, lines)
    return lines


def parse_effective(lines: list[str]) -> list[dict]:
    """「効果的だった表現と理由」から（引用, 時刻, 理由）を取り出す。"""
    start = next((i for i, s in enumerate(lines) if "効果的だった表現" in s), None)
    if start is None:
        return []
    items: list[dict] = []
    section: list[str] = []
    for s in lines[start + 1:]:
        s = s.strip()
        if "反応が鈍化" in s:
            break
        s = re.sub(r"^[-・●*\s]+", "", s)
        section.append(s)
        if not s or s.startswith(("要点", "具体的な評価")):
            continue
        if s.startswith("理由"):
            if items and not items[-1]["reason"]:
                items[-1]["reason"] = re.sub(r"^理由[:：]\s*", "", s)
            continue
        m = TIME_RE.search(s)
        quote = TIME_RE.sub("", s).strip(" 　:：")
        if quote and len(quote) <= 140:  # 長い地の文は引用ではない（下のフォールバックで「」の中だけ拾う）
            items.append({"quote": quote, "time": m.group(0) if m else "", "reason": ""})
    items = [i for i in items if len(i["quote"]) >= 8 and re.match(r"^(営業|お客様)?「", i["quote"])]  # 発言の引用だけ（状況の説明文は除く）
    if items:
        return items
    # 旧形式（引用が地の文の中に「」で入っている）: 「」の中をそのまま引用、その行を理由として付ける
    for s in section:
        if s.startswith("要点") or "なし" in s[:8]:
            continue
        reason = re.sub(r"^具体的な評価[:：]\s*", "", s)
        for q in re.findall(r"「([^」]{12,})」", s):
            m = TIME_RE.search(s)
            items.append({"quote": f"「{q}」", "time": m.group(0) if m else "", "reason": reason})
    return items


PHASE_NAMES = ["入り・信頼構築", "ヒアリング", "課題の言語化", "提案", "価格提示", "反論処理", "クロージング"]


def gemini_phase(quote: str, reason: str) -> str | None:
    """商談のどの場面のトークかをGeminiに1語で答えさせる。失敗したらNone。"""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    prompt = ("訪問販売（浄水器・住宅設備保証）の商談で使われた営業トークです。商談のどの場面のトークか、次の7つから1つだけ選び、"
              "その言葉だけを答えてください。\n" + "／".join(PHASE_NAMES) +
              f"\n\nトーク: {quote}\nこのトークが効いた理由: {reason}")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0, "maxOutputTokens": 64, "thinkingConfig": {"thinkingBudget": 0}}}).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
        data=body, method="POST", headers={"Content-Type": "application/json"})
    out = ""
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"].strip()
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:   # 回数制限。少し待ってやり直す
                time.sleep(20 * (attempt + 1))
                continue
            print(f"  Gemini分類に失敗（キーワード推定にします）: {e}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001
            print(f"  Gemini分類に失敗（キーワード推定にします）: {e}", file=sys.stderr)
            return None
    return next((n for n in PHASE_NAMES if n in out), None)


def phase_of(text: str) -> str:
    for name, words in PHASES:
        if any(w in text for w in words):
            return name
    return "全般"


def rich(text: str) -> list[dict]:
    return [{"text": {"content": text[i:i + 1900]}} for i in range(0, len(text), 1900)][:100] or [{"text": {"content": ""}}]


def prop_plain(page: dict, name: str) -> str:
    p = page["properties"].get(name) or {}
    t = p.get("type")
    v = p.get(t)
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v or [])
    if t in ("select", "status"):
        return (v or {}).get("name", "")
    return ""


def existing_keys() -> set[str]:
    keys, cur = set(), None
    while True:
        d = call("POST", f"databases/{TALK_DB}/query", {"page_size": 100, **({"start_cursor": cur} if cur else {})})
        for p in d["results"]:
            keys.add(prop_plain(p, "取込キー").split("#")[0])
        if not d.get("has_more"):
            return keys
        cur = d["next_cursor"]


def reclassify() -> None:
    cur = None
    while True:
        d = call("POST", f"databases/{TALK_DB}/query", {"page_size": 100, **({"start_cursor": cur} if cur else {})})
        for p in d["results"]:
            quote, reason = prop_plain(p, "トーク"), prop_plain(p, "理由")
            if quote == "（引用なし）":
                continue
            phase = gemini_phase(quote, reason) or phase_of(quote + reason)
            print(f"{phase}\t{quote[:50]}")
            call("PATCH", f"pages/{p['id']}", {"properties": {"フェーズ": {"select": {"name": phase}}}})
        if not d.get("has_more"):
            return
        cur = d["next_cursor"]


def main() -> None:
    if "--reclassify" in sys.argv:
        reclassify()
        return
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 21
    dry = "--dry-run" in sys.argv
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    deals = call("POST", f"databases/{DEAL_DB}/query", {"filter": {"and": [
        {"property": "分析済み", "checkbox": {"equals": True}},
        {"property": "商談日時", "date": {"on_or_after": since}}]},
        "sorts": [{"property": "商談日時", "direction": "descending"}], "page_size": 100})["results"]
    done = existing_keys()
    added = 0
    for p in deals:
        pid = p["id"]
        score = p["properties"].get("採点", {}).get("number")
        if pid in done or score is None or score < MIN_SCORE:
            continue
        items = parse_effective(section5_lines(pid))[:MAX_PER_DEAL]
        customer = prop_plain(p, "お客様名")
        closer = prop_plain(p, "クローザー")
        date = ((p["properties"].get("商談日時") or {}).get("date") or {}).get("start", "")[:10]
        print(f"{date} {customer}（{closer}・{score}点）: {len(items)}件")
        for n, it in enumerate(items):
            if dry:
                print("   ", it["quote"][:60], "|", it["time"], "|", it["reason"][:30])
                continue
            props = {
                "トーク": {"title": rich(it["quote"])},
                "理由": {"rich_text": rich(it["reason"])},
                "フェーズ": {"select": {"name": gemini_phase(it["quote"], it["reason"]) or phase_of(it["quote"] + it["reason"])}},
                "時刻": {"rich_text": rich(it["time"])},
                "お客様名": {"rich_text": rich(customer)},
                "商談日": {"date": {"start": date}} if date else {"date": None},
                "採点": {"number": score},
                "分析ページ": {"url": p.get("url")},
                "取込キー": {"rich_text": rich(f"{pid}#{n}")},
            }
            if closer:
                props["クローザー"] = {"select": {"name": closer}}
            call("POST", "pages", {"parent": {"database_id": TALK_DB}, "properties": props})
            added += 1
        if not items and not dry:  # 取り込める引用が無い商談も、次回また調べ直さないよう目印だけ残す
            call("POST", "pages", {"parent": {"database_id": TALK_DB}, "properties": {
                "トーク": {"title": rich("（引用なし）")}, "取込キー": {"rich_text": rich(f"{pid}#none")},
                "商談日": {"date": {"start": date}} if date else {"date": None}}})
    print(f"取り込み: {added}件")


if __name__ == "__main__":
    main()
