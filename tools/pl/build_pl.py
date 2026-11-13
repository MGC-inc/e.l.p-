#!/usr/bin/env python3
"""月次の粗利・報酬（完工月ベース）を、Notionの商談分析DBから計算してMarkdownにする（管理者だけが見るPL用）。

計算式は Notion「報酬計算（商談分析DB・完工月ベース）」と同じ:
  粗利 = 税込売価 − 工事費50,600 − 商品代（浄水器6万／バブル5.5万／両方11.5万）
         − 保証料（契約日が3月まで4万・4月以降0） − サービス品（備考の「サービス品：○○円」）
  報酬 = 粗利 × 料率（役割ごと。アポとクロが同じ人なら「両方」）
対象: 結果＝契約 かつ 完工日がある案件。完工日が今日より後のものは「見込」として分ける。

料率（給与にあたる情報）はこのリポジトリに置かない。--rates で渡すJSON（リポジトリの外）から読む:
  {"deduct": {"<お客様名>": {"amount": 10000, "note": "解約金"}},
   "agency": {"<代理店>": {"appointer": 0.3, "closer": 0.3}},
   "person": {"<名前>": {"appointer": 0.1, "closer": 0.1, "both": 0.1, "fixed": 100000, "fixed_from": "2026-01"}}}
料率が無い役割は「未設定」として、報酬を出さずに一覧する（推測で埋めない）。

使い方: python3 tools/pl/build_pl.py --rates <rates.json> --out <出力.md> [--today YYYY-MM-DD]
出力は管理者だけが見られるNotionページに貼る。リポジトリにはコミットしない。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shuuji"))
import notion_to_csv as N  # noqa: E402

CONSTRUCTION = 50_600
PRODUCT = {"浄水器のみ": 60_000, "バブルのみ": 55_000, "浄水器＋バブル": 115_000}
SERVICE_RE = re.compile(r"サービス品[：:]\s*([\d,]+)\s*円")
# 名簿に無い・退社した人の所属（予算DBにも出てこない人）
FALLBACK_AGENCY = {"催事": "ピタサチ", "鈴木": "ピタサチ", "下川": "ピタサチ", "樺澤": "ピタサチ", "古賀": "wanny"}


def yen(v: float) -> str:
    return f"{v:,.0f}"


def load_deals() -> list[dict]:
    rows = N.notion_query_all(N.DEAL_DB, {"property": "結果", "status": {"equals": "契約"}})
    out = []
    for r in rows:
        p = r["properties"]
        date = lambda n: ((p.get(n) or {}).get("date") or {}).get("start", "")[:10]
        out.append({
            "name": "".join(t["plain_text"] for t in p["お客様名"]["title"]).strip(),
            "price": N.prop_number(r, "売上金額"),
            "items": [x["name"] for x in (p.get("商材") or {}).get("multi_select", [])],
            "contract": date("契約日"), "meeting": date("商談日時"), "done": date("完工日"),
            "closer": N.prop_select(r, "クローザー"), "appo": N.prop_select(r, "アポインター"),
            "memo": "".join(t.get("plain_text", "") for t in (p.get("備考") or {}).get("rich_text", [])),
        })
    return out


def agency_map() -> dict[str, str]:
    m = dict(FALLBACK_AGENCY)
    for r in N.notion_query_all(N.BUDGET_DB, {"property": "予算（万円）", "number": {"is_not_empty": True}}):
        name, ag = N.prop_select(r, "メンバー"), N.prop_select(r, "代理店")
        if name and ag:
            m[name] = ag
    return m


def gross(d: dict, deduct: dict) -> tuple[float, list[str]]:
    """粗利と、計算の前提で気になる点（人が確認するもの）。"""
    notes = []
    items = [i for i in d["items"] if i in PRODUCT]
    if not items:
        notes.append(f"商材が未入力または対象外（{'・'.join(d['items']) or '空'}）→ 商品代を0で計算")
        product = 0
    elif len(items) == 1:
        product = PRODUCT[items[0]]
    else:
        product = PRODUCT["浄水器＋バブル"] if set(items) == {"浄水器のみ", "バブルのみ"} else PRODUCT[items[0]]
    # 契約日が空の案件が多いので、商談日（即決の契約日）→完工日の順で保証料の有無（3月まで4万）を判定する
    basis = d["contract"] or d["meeting"] or d["done"]
    warranty = 40_000 if basis < "2026-04-01" else 0
    if not d["contract"] and not d["meeting"]:
        notes.append("契約日・商談日が空 → 完工日で保証料の有無を判定")
    service = sum(int(x.replace(",", "")) for x in SERVICE_RE.findall(d["memo"]))
    if re.search(r"解約金|控除", d["memo"]):
        notes.append("備考に「解約金／控除」の記載あり（自動では引いていない）")
    manual = deduct.get(d["name"])  # 備考から読めない手入力の控除（解約金・フィルター等）。--ratesのJSONで渡す
    if manual:
        notes.append(f"手入力の控除 {yen(manual['amount'])}円（{manual['note']}）")
    return d["price"] - CONSTRUCTION - product - warranty - service - (manual["amount"] if manual else 0), notes


def payouts(d: dict, g: float, rates: dict, amap: dict) -> tuple[list[tuple[str, str, float | None]], list[str]]:
    """[(支払先, 役割, 金額 or None=未設定)]。同じ人がアポとクロを兼ねたら「両方」。"""
    c, a = d["closer"], d["appo"] or d["closer"]
    people = [(c, "both")] if a == c else [(a, "appointer"), (c, "closer")]
    out, unknown = [], []
    for person, role in people:
        ag = amap.get(person, "")
        po = rates.get("person", {}).get(person, {})
        rt = rates.get("agency", {}).get(ag, {})
        if role == "both":
            rate = po.get("both")
            if rate is None and po.get("appointer") is not None and po.get("closer") is not None:
                rate = po["appointer"] + po["closer"]
            if rate is None and rt.get("appointer") is not None and rt.get("closer") is not None:
                rate = rt["appointer"] + rt["closer"]
        else:
            rate = po.get(role) if po.get(role) is not None else rt.get(role)
        payee = person if person in rates.get("person", {}) else (ag or person)
        label = {"both": "アポ＋クロ", "appointer": "アポ", "closer": "クロ"}[role]
        if rate is None:
            unknown.append(f"{person}（{ag or '所属不明'}・{label}）")
            out.append((payee, label, None))
        else:
            out.append((payee, label, g * rate))
    return out, unknown


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--today", default=datetime.date.today().isoformat())
    args = ap.parse_args()
    rates = json.load(open(args.rates, encoding="utf-8"))
    amap = agency_map()
    deals = [d for d in load_deals() if d["done"]]
    skipped = [d for d in deals if d["price"] <= 0]  # 売上金額が0・空の案件は計算に入れず、要確認として出す
    deals = [d for d in deals if d["price"] > 0]
    deals.sort(key=lambda d: (d["done"], d["name"]))

    months: dict[str, dict] = defaultdict(lambda: {"n": 0, "price": 0.0, "cost": 0.0, "gross": 0.0, "pay": 0.0, "unset": 0,
                                                   "by": defaultdict(float), "future": False, "lines": []})
    all_unknown: dict[str, set] = defaultdict(set)
    for d in deals:
        g, notes = gross(d, rates.get("deduct", {}))
        pays, unknown = payouts(d, g, rates, amap)
        m = months[d["done"][:7]]
        m["n"] += 1; m["price"] += d["price"]; m["cost"] += d["price"] - g; m["gross"] += g
        m["future"] |= d["done"] > args.today
        for payee, _, amt in pays:
            if amt is not None:
                m["pay"] += amt; m["by"][payee] += amt
        if unknown:
            m["unset"] += 1
            for u in unknown:
                all_unknown[u].add(d["name"])
        pay_txt = " ／ ".join(f"{p}({r}) {yen(a) if a is not None else '**未設定**'}" for p, r, a in pays)
        flag = "（見込）" if d["done"] > args.today else ""
        extra = ("　⚠ " + "、".join(notes)) if notes else ""
        m["lines"].append(f"- **{d['name']}** 完工{d['done']}{flag} 売価{yen(d['price'])} 粗利{yen(g)} ｜ {pay_txt}{extra}")

    fixed = rates.get("person", {})
    L = ["# 月次PL（粗利・報酬｜完工月ベース）", "",
         f"作成: {args.today}　対象: 結果＝契約 かつ 完工日あり（{len(deals)}件）。完工日が{args.today}より後の月は「見込」。",
         "販管費は含まない（粗利→報酬までの管理者用）。固定給は下の表で別に足す。", "",
         "## 月次サマリー（円）", "",
         "| 完工月 | 件数 | 税込売上 | 原価（工事費・商品代・保証料・サービス品） | 粗利 | 報酬（料率が分かる分） | 報酬後の粗利 | 料率未設定の案件 |",
         "|---|---|---|---|---|---|---|---|"]
    tot = defaultdict(float)
    for k in sorted(months):
        m = months[k]
        L.append(f"| {k}{'（見込）' if m['future'] else ''} | {m['n']} | {yen(m['price'])} | {yen(m['cost'])} | {yen(m['gross'])} | "
                 f"{yen(m['pay'])} | {yen(m['gross'] - m['pay'])} | {m['unset']}件 |")
        for key in ("n", "price", "cost", "gross", "pay"):
            tot[key] += m[key]
    L += [f"| **合計** | {int(tot['n'])} | {yen(tot['price'])} | {yen(tot['cost'])} | {yen(tot['gross'])} | {yen(tot['pay'])} | "
          f"{yen(tot['gross'] - tot['pay'])} | |", "",
          "※「報酬後の粗利」は、料率が未設定の案件の報酬を引いていないため、未設定の件数がある月は実際より大きい。", ""]

    payees = sorted({p for m in months.values() for p in m["by"]})
    L += ["## 支払先別の報酬（固定給を除く・円）", "", "| 完工月 | " + " | ".join(payees) + " |", "|---" * (len(payees) + 1) + "|"]
    for k in sorted(months):
        L.append(f"| {k} | " + " | ".join(yen(months[k]["by"].get(p, 0)) for p in payees) + " |")
    fx = [(p, v["fixed"], v.get("fixed_from", "")) for p, v in fixed.items() if v.get("fixed")]
    if fx:
        L += ["", "**固定給（上の表に含まない）**: " + "、".join(f"{p} 月{yen(a)}（{f or '開始月未確認'}〜）" for p, a, f in fx)]
    L += ["", "## 料率が未設定で、報酬を出していない役割", ""]
    if all_unknown:
        for u, names in sorted(all_unknown.items()):
            L.append(f"- {u}: {len(names)}件（{'、'.join(sorted(names)[:6])}{'…' if len(names) > 6 else ''}）")
    else:
        L.append("- なし")
    L += ["", "## 売上金額が0・空のため計算に入れていない案件（要確認）", ""]
    L += [f"- {d['name']}（完工日{d['done']}・クローザー{d['closer']}）" for d in skipped] or ["- なし"]
    L += ["", "## 案件別明細（直近の完工月から）", ""]
    for k in sorted(months, reverse=True):
        L += [f"### 完工 {k}{'（見込）' if months[k]['future'] else ''}", ""] + months[k]["lines"] + [""]
    open(args.out, "w", encoding="utf-8").write("\n".join(L))
    print(f"{len(deals)}件 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
