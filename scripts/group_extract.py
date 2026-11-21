#!/usr/bin/env python3
"""施工グループの発言から、施工案件一覧に反映する「提案」を作る。2026-11-21 今川さん指示。

・発言を読んで、施工案件一覧の未完工の案件（お客様名）に当てはまるものだけを、案件ごとの要点にまとめる。
・反映する中身（進捗・施工日時・機器発送状況・次の確認への追記）は「提案」にとどめる。Notionへの反映は、
  今川さんが夜のまとめで［反映］を押したときだけ（line-webhook/api/webhook.py の handle_group_proposal）。
・住所・電話番号・金額は、要点にも提案にも入れない（プロンプトで禁止＋機械的に伏せる）。
・案件名が一覧にないものは「案件が特定できない発言」として別に数える。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.request

NOTION_VERSION = "2025-09-03"
CONSTRUCTION_DS = "387d697b-920f-4baf-8e68-a20e5ac69d00"
MODEL = "gemini-2.5-flash"
PROGRESS = ["日程調整中", "施工予定", "施工中", "延期・再調整", "完工", "中止"]
SHIP = ["発送先確認中", "発送準備中", "一部発送", "発送済み", "到着確認済み", "要確認", "対象外"]

PROMPT = """あなたは、訪問営業会社の施工案件管理の補助です。施工業者のLINEグループの発言（日付つき）と、いま施工中の案件の一覧を渡します。
発言のうち、(1) 一覧にある案件（お客様名）の施工の進み具合が分かるものは「updates」に、(2) 一覧にないお客様の新しい施工依頼や連絡は「new」に、次のJSONのオブジェクトだけを出力してください（前後に文章やコードブロックは付けない）。

{{
  "updates": [
    {{
      "customer": "一覧にあるお客様名（例: 塚本邸）。一覧にない名前は入れない",
      "source": "その案件の発言があったグループ名（発言の「グループ」欄の文字をそのまま。複数のグループにまたがるときは、いちばん新しい発言のグループ）",
      "summary": "この案件の要点を一文で（40字以内）。例: 12/3 9:00で施工が確定",
      "changes": {{
        "進捗": "{progress} のどれか（発言から明らかなときだけ。迷うなら入れない）",
        "施工日時": "YYYY-MM-DDTHH:MM:00+09:00（日付と時刻が発言で確定しているときだけ。時刻が不明なら入れない）",
        "機器発送状況": "{ship} のどれか（発言から明らかなときだけ）"
      }},
      "note": "「次の確認」に残す一文（60字以内）。決まっていない点・確認が必要な点を書く。なければ空"
    }}
  ],
  "new": [
    {{
      "customer": "一覧にないお客様名。同じ名前が複数あるときは、市区町村で見分けて別々に（例: 石川邸（安城市））",
      "source": "発言のあったグループ名（同上）",
      "summary": "日時と内容の要点を一文で（50字以内）。例: 11/30 9:00〜 浄水器・バブルの施工依頼"
    }}
  ]
}}

守ること:
- 一覧にない案件、雑談、挨拶、案件と関係ない連絡は出さない。
- 発言にないことを足さない。推測で日付や進捗を決めない。「明日」「来週」は、その発言の日付から計算する。
- 同じ案件の発言が複数あるときは、いちばん新しい内容を優先し、1つにまとめる。
- 住所・電話番号・金額・個人の氏名は、書かない（「お客様」「施工店」と書く）。
- changes の項目は、発言で確定しているものだけ。1つもなければ空のオブジェクト {{}} にして、note だけ書く。
- 該当がなければ、updates も new も空の配列 [] にする。
- 完工・業者の入室や挨拶・日程調整のやりとりだけで施工の依頼がないものは、new に入れない。

今日の日付: {today}

【施工中の案件の一覧】
{constructions}

【グループの発言（日時・グループ・発言者・本文）】
{messages}
"""

PII = [(re.compile(r"0\d{1,4}[-ー]?\d{1,4}[-ー]?\d{3,4}"), "（電話番号）"), (re.compile(r"〒?\d{3}-\d{4}"), "（郵便番号）")]


def scrub(t: str) -> str:
    for rx, rep in PII:
        t = rx.sub(rep, t)
    return t


def _notion(method: str, path: str, body=None):
    tok = os.environ["NOTION_TOKEN"]
    req = urllib.request.Request("https://api.notion.com/v1/" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {tok}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _pl(p: dict, name: str) -> str:
    x = (p.get("properties") or {}).get(name) or {}
    t = x.get("type")
    v = x.get(t)
    if t in ("title", "rich_text"):
        return "".join(i.get("plain_text", "") for i in v or [])
    if t == "select":
        return (v or {}).get("name", "")
    if t == "date":
        return (v or {}).get("start") or ""
    if t == "multi_select":
        return "・".join(i.get("name", "") for i in v or [])
    return ""


def open_constructions() -> list[dict]:
    """未完工・中止ではない施工案件。"""
    out, cursor = [], None
    for _ in range(3):
        body = {"page_size": 100, "filter": {"and": [{"property": "進捗", "select": {"does_not_equal": "完工"}},
                                                       {"property": "進捗", "select": {"does_not_equal": "中止"}}]}}
        if cursor:
            body["start_cursor"] = cursor
        res = _notion("POST", f"data_sources/{CONSTRUCTION_DS}/query", body)
        for p in res.get("results", []):
            out.append({"id": p["id"], "name": _pl(p, "案件"), "shop": _pl(p, "施工店"), "progress": _pl(p, "進捗"),
                        "when": _pl(p, "施工日時")[:16], "ship": _pl(p, "機器発送状況"), "content": _pl(p, "施工内容"),
                        "city": _pl(p, "市区町村")})
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    return [c for c in out if c["name"]]


def gemini_json(prompt: str):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY がありません")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}}).encode()
    last = None
    for attempt in range(4):
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read())
            text = d["candidates"][0]["content"]["parts"][0]["text"]
            t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
            return json.loads(t)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 503):
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"Gemini が応答しませんでした: {last}")


def clean(prop: dict, consts: dict[str, dict], sources: set[str]) -> dict | None:
    """Geminiの提案を、使ってよい形に整える（一覧にない案件・許されない値は捨てる）。"""
    name = str(prop.get("customer") or "").strip()
    c = consts.get(name)
    if not c:
        return None
    ch = prop.get("changes") or {}
    changes = {}
    if ch.get("進捗") in PROGRESS and ch.get("進捗") != c["progress"]:
        changes["進捗"] = ch["進捗"]
    w = str(ch.get("施工日時") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00", w) and w[:16] != c["when"]:
        changes["施工日時"] = w
    if ch.get("機器発送状況") in SHIP and ch.get("機器発送状況") != c["ship"]:
        changes["機器発送状況"] = ch["機器発送状況"]
    note = scrub(str(prop.get("note") or ""))[:80]
    summary = scrub(str(prop.get("summary") or ""))[:60]
    if not (changes or note):
        return None
    src = str(prop.get("source") or "").strip()
    return {"cid": c["id"], "name": name, "shop": c["shop"], "src": src if src in sources else "", "summary": summary, "changes": changes, "note": note}


def group_label(gid: str, labels: dict[str, str]) -> str:
    """G.L／AZ。未登録のグループは「未登録…末尾4文字」（どのグループか見分けるため）。"""
    return labels.get(gid) or f"未登録…{gid[-4:]}"


def build_proposals(msgs: list[dict], today: dt.date, labels: dict[str, str]) -> tuple[list[dict], int, list[dict]]:
    """発言（{group,name,text,ts}の配列）→ (提案の配列, 案件に関係しない発言の数の目安, 一覧にない案件の連絡の配列)"""
    consts_list = open_constructions()
    consts = {c["name"]: c for c in consts_list}
    jst = dt.timezone(dt.timedelta(hours=9))
    lines = []
    for m in msgs:
        ts = m.get("ts")
        when = dt.datetime.fromtimestamp(ts / 1000, jst).strftime("%m/%d %H:%M") if ts else ""
        lines.append(f"{when}｜{group_label(m.get('group', ''), labels)}｜{m.get('name') or '不明'}｜{scrub((m.get('text') or '').replace(chr(10), ' '))[:300]}")
    clist = "\n".join(f"・{c['name']}（施工店:{c['shop'] or '未定'}／進捗:{c['progress']}／施工日時:{c['when'] or '未定'}／機器発送:{c['ship'] or '未入力'}）"
                      for c in consts_list)
    raw = gemini_json(PROMPT.format(progress="、".join(PROGRESS), ship="、".join(SHIP), today=today.isoformat(), constructions=clist or "（なし）",
                                    messages="\n".join(lines)))
    obj = raw if isinstance(raw, dict) else {"updates": raw if isinstance(raw, list) else [], "new": []}
    sources = {group_label(m.get("group", ""), labels) for m in msgs}
    props, seen = [], set()
    for p in obj.get("updates") or []:
        q = clean(p, consts, sources)
        if q and q["name"] not in seen:
            seen.add(q["name"])
            props.append(q)
    new = []
    for n in obj.get("new") or []:
        name = scrub(str(n.get("customer") or "")).strip()[:30]
        summ = scrub(str(n.get("summary") or "")).strip()[:70]
        if not name or name in consts:
            continue
        src = str(n.get("source") or "").strip()
        new.append({"name": name, "src": src if src in sources else "", "summary": summ})
    return props, max(0, len(msgs) - len(props)), new
