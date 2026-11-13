#!/usr/bin/env python3
"""録音の分析結果（scripts/deal_analysis.py の出力JSON）を、Notion 商談案件一覧（DB 商談分析＆アポ分析）に登録する。

  python3 scripts/register_analysis.py <分析結果.json>                # 登録して、ページのURLを標準出力へ
  python3 scripts/register_analysis.py <分析結果.json> --dry-run      # 何をするかだけ表示

・同じクローザー・同じお客様名・商談日が前後5日以内の「報告の行」（まだ分析されていないもの）があれば、
  新しい行を作らず、その行に分析を書き足す（二重登録を防ぐ）:
    - 分析済み＝ON、採点、録音分析の本文を追記
    - 商材・給湯器・年齢層などは、報告の行が空のときだけ埋める（報告の値は上書きしない）
    - 結果は変えない（報告の値のまま）
・報告の行がなければ、新しい行を作る（スキル 商談録音分析 の手順4と同じ）
・本文のMarkdownは、見出し（##）・区切り線・段落に変換する（1ブロック2000字以内に分割）

環境変数: NOTION_TOKEN
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import load_env, notion, prop_text, query_all  # noqa: E402

DEAL_DS = "8958bbaf-2c24-4e94-97b2-4c801e60cb37"
V = "2025-09-03"
SELECTS = ["エネルギー", "決裁者", "年齢層（世帯主）", "訪販経験", "築年数"]
MULTIS = ["商材", "給湯器", "課題・関心事", "商談相手"]
TEXTS = ["エリア", "子どもの年齢", "ネック・保留理由", "備考"]
UNKNOWN = {"", "不明", "-", None}


def rt(s: str) -> list[dict]:
    return [{"type": "text", "text": {"content": s[i:i + 1900]}} for i in range(0, len(s), 1900)] or [{"type": "text", "text": {"content": ""}}]


def blocks_of(md: str) -> list[dict]:
    out = []
    for line in md.split("\n"):
        if line.startswith("## "):
            out.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt(line[3:])}})
        elif line.strip() == "---":
            out.append({"object": "block", "type": "divider", "divider": {}})
        elif line.strip():
            out.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(line)}})
    return out


def find_report(customer: str, closer: str, date: str) -> dict | None:
    base = re.sub(r"(邸|様|さん)$", "", customer.strip())
    d = dt.date.fromisoformat(date[:10])
    pages = query_all(f"/data_sources/{DEAL_DS}/query", {"and": [
        {"property": "クローザー", "select": {"equals": closer}},
        {"property": "お客様名", "title": {"contains": base}},
        {"property": "商談日時", "date": {"on_or_after": (d - dt.timedelta(days=5)).isoformat()}},
        {"property": "商談日時", "date": {"on_or_before": (d + dt.timedelta(days=5)).isoformat()}}]}, V)
    pages = [p for p in pages if not (p["properties"].get("分析済み") or {}).get("checkbox")]
    return pages[0] if pages else None


def empty(page: dict, name: str) -> bool:
    p = page["properties"].get(name) or {}
    t = p.get("type")
    v = p.get(t)
    if t == "multi_select":
        return not [x for x in v or [] if x.get("name") not in UNKNOWN]
    if t == "select":
        return (v or {}).get("name") in UNKNOWN
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in v or []).strip() in UNKNOWN
    return v is None


def props_for(pr: dict, only_empty_in: dict | None) -> dict:
    out: dict = {}

    def ok(name: str) -> bool:
        return only_empty_in is None or empty(only_empty_in, name)

    for n in SELECTS:
        v = pr.get(n)
        if v and v not in UNKNOWN and ok(n):
            out[n] = {"select": {"name": v}}
    for n in MULTIS:
        v = [x for x in (pr.get(n) or []) if x not in UNKNOWN]
        if v and ok(n):
            out[n] = {"multi_select": [{"name": x} for x in v]}
    for n in TEXTS:
        v = pr.get(n)
        if v and v not in UNKNOWN and ok(n):
            out[n] = {"rich_text": rt(v)}
    if isinstance(pr.get("採点"), (int, float)):
        out["採点"] = {"number": pr["採点"]}
    out["分析済み"] = {"checkbox": True}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("analysis")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()
    d = json.load(open(args.analysis, encoding="utf-8"))
    pr, md = d["properties"], d["content_markdown"]
    if not md.strip():
        sys.exit("分析本文が空です（登録しません。再実行してください）")
    report = find_report(pr["お客様名"], pr["クローザー"], pr["商談日時"])
    children = blocks_of(md)
    if report:
        props = props_for(pr, report)
        print(f"報告の行に書き足します: {report['url']}（更新する項目: {', '.join(props)}）", file=sys.stderr)
        if args.dry_run:
            return
        notion("PATCH", f"/pages/{report['id']}", {"properties": props}, V)
        page_id, url = report["id"], report["url"]
    else:
        props = props_for(pr, None)
        props["お客様名"] = {"title": rt(pr["お客様名"])}
        props["クローザー"] = {"select": {"name": pr["クローザー"]}}
        if pr.get("アポインター"):
            props["アポインター"] = {"select": {"name": pr["アポインター"]}}
        props["結果"] = {"status": {"name": pr["結果"]}}
        props["商談日時"] = {"date": {"start": pr["商談日時"]}}
        props["初回登録時_保留"] = {"checkbox": pr.get("初回登録時_保留") == "__YES__"}
        print("報告の行がないため、新しい行を作ります", file=sys.stderr)
        if args.dry_run:
            return
        r = notion("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": DEAL_DS}, "properties": props,
                                      "children": children[:100]}, V)
        page_id, url, children = r["id"], r["url"], children[100:]
    while children:
        notion("PATCH", f"/blocks/{page_id}/children", {"children": children[:100]}, V)
        children = children[100:]
    print(url)


if __name__ == "__main__":
    main()
