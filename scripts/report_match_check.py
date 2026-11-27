#!/usr/bin/env python3
"""録音の分析結果が、商談報告（Notion 商談案件一覧）のお客様情報と食い違っていないかを、分析のあとで疑って確かめる。

  python3 scripts/report_match_check.py <分析結果.json> --customer 松木邸 --closer 岡野 --date 2026-11-07

使い方（スキル 商談録音分析 の手順3と4の間）:
  scripts/deal_analysis.py の出力JSON（{"content_markdown", "properties"}）と、LINEで報告されたお客様名・クローザー・日付を渡す。
  Notionの商談案件一覧から、同じクローザー・同じお客様名・商談日が前後5日以内の「報告の行」を探し、
  分析からわかる個別具体的な情報（商材・給湯器・エネルギー・世帯主の年齢層・築年数・決裁者・商談相手・子どもの年齢・エリア・金額）を
  報告の内容と1つずつ比べる。どちらかが「不明」「-」「空」の項目は比べない。

判定（標準出力にJSONを1行で出す。終了コードは常に0）:
  {"verdict": "ok" | "suspect" | "no_report", "compared": 比べた項目数, "conflicts": ["商材：報告=… / 録音=…", ...]}
  suspect ＝ 3項目以上を比べて、2項目以上が食い違い、かつ半数以上が食い違った → この録音は指定のお客様ではない疑い
  no_report ＝ 比べる報告の行がない（そのまま分析を登録してよい）
  ok ＝ 食い違いは少ない（そのまま登録してよい）

suspect のときの対応は、スキル 商談録音分析 の手順に書いてある（本人に「録音ファイルが報告内容と一致しません」と返し、今川さんにも知らせて、
Notionへの登録は止める）。AIの読み取りには揺れがあるため、1項目の食い違いだけでは疑わない。

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
from monthly_budget_floor import load_env, query_all  # noqa: E402

DEAL_DS = "8958bbaf-2c24-4e94-97b2-4c801e60cb37"   # ✅ 商談案件一覧（data_source_id）
UNKNOWN = {"", "不明", "-", "－", "ー", "未設定", None}
FIELDS = ["商材", "給湯器", "エネルギー", "年齢層（世帯主）", "築年数", "決裁者", "商談相手", "子どもの年齢", "エリア"]
AMOUNT_TOLERANCE = 0.20   # 金額は2割まで違っても同じ商談とみなす（サービス品の扱いなどで報告と差が出るため）


def values_of(prop: dict | None) -> set[str]:
    """Notionのプロパティ（API形式）から、値の集合を取り出す。不明などは除く。"""
    if not prop:
        return set()
    t = prop.get("type")
    v = prop.get(t)
    if t in ("select", "status"):
        vals = {(v or {}).get("name")}
    elif t == "multi_select":
        vals = {x.get("name") for x in v or []}
    elif t in ("rich_text", "title"):
        vals = {"".join(x.get("plain_text", "") for x in v or []).strip()}
    else:
        vals = set()
    return {x for x in vals if x not in UNKNOWN}


def analysis_values(value) -> set[str]:
    """deal_analysis.py のプロパティ値（文字列または文字列のリスト）を集合にする。"""
    items = value if isinstance(value, list) else [value]
    return {str(x).strip() for x in items if x not in UNKNOWN and str(x).strip() not in UNKNOWN}


def same(report: set[str], heard: set[str], field: str) -> bool:
    if report & heard:
        return True
    if field == "エリア":   # 市区町村名は、一方がもう一方を含めば同じとみなす（「岡崎市」と「愛知県岡崎市」など）
        return any(a in b or b in a for a in report for b in heard)
    return False


def amount_from_markdown(md: str) -> int | None:
    m = re.search(r"提示金額[:：]\s*[^\n]*?([\d,]{5,})\s*円", md or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def judge(report_page: dict, analysis: dict) -> dict:
    props = analysis.get("properties") or {}
    compared, conflicts = 0, []
    for f in FIELDS:
        rep = values_of(report_page["properties"].get(f))
        heard = analysis_values(props.get(f))
        if not rep or not heard:
            continue
        compared += 1
        if not same(rep, heard, f):
            conflicts.append(f"{f}：報告={'・'.join(sorted(rep))} / 録音={'・'.join(sorted(heard))}")
    rep_amount = (report_page["properties"].get("売上金額") or {}).get("number")
    heard_amount = amount_from_markdown(analysis.get("content_markdown", ""))
    if rep_amount and heard_amount:
        compared += 1
        if abs(rep_amount - heard_amount) / max(rep_amount, heard_amount) > AMOUNT_TOLERANCE:
            conflicts.append(f"金額：報告={rep_amount:,}円 / 録音={heard_amount:,}円")
    suspect = compared >= 3 and len(conflicts) >= 2 and len(conflicts) / compared >= 0.5
    return {"verdict": "suspect" if suspect else "ok", "compared": compared, "conflicts": conflicts}


def find_report(customer: str, closer: str, date: str) -> dict | None:
    base = re.sub(r"(邸|様|さん)$", "", customer.strip())
    d = dt.date.fromisoformat(date[:10])
    pages = query_all(f"/data_sources/{DEAL_DS}/query", {"and": [
        {"property": "クローザー", "select": {"equals": closer}},
        {"property": "お客様名", "title": {"contains": base}},
        {"property": "商談日時", "date": {"on_or_after": (d - dt.timedelta(days=5)).isoformat()}},
        {"property": "商談日時", "date": {"on_or_before": (d + dt.timedelta(days=5)).isoformat()}}]}, "2025-09-03")
    pages = [p for p in pages if not (p["properties"].get("分析済み") or {}).get("checkbox")]   # 分析ページ自身は除く
    return pages[0] if pages else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("analysis", help="deal_analysis.py の出力JSON")
    ap.add_argument("--customer", required=True)
    ap.add_argument("--closer", required=True)
    ap.add_argument("--date", required=True, help="商談日 YYYY-MM-DD")
    args = ap.parse_args()
    load_env()
    analysis = json.load(open(args.analysis, encoding="utf-8"))
    report = find_report(args.customer, args.closer, args.date)
    if not report:
        print(json.dumps({"verdict": "no_report", "compared": 0, "conflicts": []}, ensure_ascii=False))
        return
    result = judge(report, analysis)
    result["report_url"] = report.get("url", "")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
