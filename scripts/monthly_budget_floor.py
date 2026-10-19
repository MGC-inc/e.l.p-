#!/usr/bin/env python3
"""月初の予算チェック: 当月予算が前月の実績（キャンセルを引いた実質売上）を下回らないようにし、
📊個人別KPI逆算データの当月予算・月目標を更新する。

ルール（ユーザー指示 2026-10-18）:
  - 当月予算は前月までの結果だけで決める（当月の途中実績は見ない）
  - 当月予算 < 前月の実質売上（実績DB「実質売上（万円）」= 契約金額 − キャンセル金額）なら、
    当月予算を前月の実質売上まで引き上げる（「💰 予算と達成率」の当月行を更新）
  - 📊KPI逆算データには、当月予算を`今月予算万円`として転記し、月目標を次の式で決める
      クローザー: `平均契約単価万円_30日` = 直近3か月（前月まで）の契約1件あたり売上
                  （本人の契約が無ければクローザー全体平均）
                  `今月契約目標` = round(予算 ÷ 平均単価)（最低1件）
      アポインター: `今月アポ目標概算` = floor(予算 ÷ 7.5)
  - 実績値（今月訪問実績・今月アポ実績・今月契約実績など）はここでは触らない

使い方:
  python3 scripts/monthly_budget_floor.py                 # 当月（JST）を対象に実行
  python3 scripts/monthly_budget_floor.py --month 2026-10 # 対象月を指定
  python3 scripts/monthly_budget_floor.py --dry-run       # 変更内容の表示のみ
  python3 scripts/monthly_budget_floor.py --notify        # 結果を今川さんにLINEで通知
  python3 scripts/monthly_budget_floor.py --check-only --notify  # 予算未設定者がいればアラートのみ
                                                          # （前月に予算があり当月に無いメンバーを未設定とみなす）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(os.path.dirname(HERE), ".env")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION_V2 = "2025-09-03"   # data_sources API（実績DB・予算表・商談DB）
NOTION_VERSION_V1 = "2022-06-28"   # 旧databases API（KPI逆算データ）
PERFORMANCE_DB = "f11afda4-a39d-4139-8e12-817d3f70267b"  # 📉 営業部 実績DB（data_source_id）
DEAL_DB = "8958bbaf-2c24-4e94-97b2-4c801e60cb37"          # 商談案件一覧（data_source_id）
BUDGET_DB = "b1809b1a-e82d-4032-ac86-a32d165d475d"        # 💰 予算と達成率（data_source_id）
KPI_DB = "5f110c1c-b977-4054-9b00-5b3ce27d3493"           # 📊 個人別KPI逆算データ（database_id）
MAN_PER_APO = 7.5
AVG_PRICE_MONTHS = 3
JST = ZoneInfo("Asia/Tokyo")


def load_env() -> None:
    if not os.path.exists(ENV_PATH):
        return
    for line in open(ENV_PATH, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def notion(method: str, path: str, body: dict | None = None, version: str = NOTION_VERSION_V2) -> dict:
    req = urllib.request.Request(
        f"{NOTION_API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
                 "Notion-Version": version, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


READS = 3  # tools/shuuji/notion_to_csv.pyと同じ理由で、1回の読み取りで行が欠けることがあるため複数回読んで合わせる


def query_all(path: str, filter_obj: dict | None, version: str) -> list[dict]:
    rows: dict[str, dict] = {}
    for _ in range(READS):
        for r in _query_once(path, filter_obj, version):
            rows[r["id"]] = r
    return list(rows.values())


def _query_once(path: str, filter_obj: dict | None, version: str) -> list[dict]:
    out, cursor = [], None
    while True:
        body: dict = {"page_size": 100}
        if filter_obj:
            body["filter"] = filter_obj
        if cursor:
            body["start_cursor"] = cursor
        data = notion("POST", path, body, version)
        out.extend(data["results"])
        if not data.get("has_more"):
            return out
        cursor = data["next_cursor"]


def prop_text(page: dict, name: str) -> str:
    p = page["properties"].get(name) or {}
    t = p.get("type")
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in p[t])
    if t in ("select", "status"):
        return (p[t] or {}).get("name", "")
    if t == "formula":
        return str((p["formula"] or {}).get("string") or "")
    return ""


def prop_num(page: dict, name: str) -> float | None:
    p = page["properties"].get(name) or {}
    if p.get("type") == "formula":
        return (p["formula"] or {}).get("number")
    return p.get("number")


def month_range(ym: str) -> tuple[dt.date, dt.date]:
    y, m = map(int, ym.split("-"))
    start = dt.date(y, m, 1)
    end = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
    return start, end


def shift_month(ym: str, delta: int) -> str:
    y, m = map(int, ym.split("-"))
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def prev_month_net_sales(prev_ym: str) -> dict[str, float]:
    start, end = month_range(prev_ym)
    rows = query_all(f"/data_sources/{PERFORMANCE_DB}/query", {"and": [
        {"property": "実績日", "date": {"on_or_after": start.isoformat()}},
        {"property": "実績日", "date": {"on_or_before": end.isoformat()}},
    ]}, NOTION_VERSION_V2)
    net: dict[str, float] = defaultdict(float)
    for r in rows:
        name = prop_text(r, "メンバー")
        if name:
            net[name] += prop_num(r, "実質売上（万円）") or 0
    return net


def avg_contract_price(month: str) -> tuple[dict[str, float], float | None]:
    start, _ = month_range(shift_month(month, -AVG_PRICE_MONTHS))
    _, end = month_range(shift_month(month, -1))
    rows = query_all(f"/data_sources/{DEAL_DB}/query", {"and": [
        {"property": "結果", "status": {"equals": "契約"}},
        {"property": "商談日時", "date": {"on_or_after": start.isoformat()}},
        {"property": "商談日時", "date": {"on_or_before": end.isoformat()}},
    ]}, NOTION_VERSION_V2)
    total: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        yen = prop_num(r, "売上金額")
        closer = prop_text(r, "クローザー")
        if yen and yen > 0 and closer:
            total[closer].append(yen / 10000)
    per = {k: sum(v) / len(v) for k, v in total.items()}
    allv = [x for v in total.values() for x in v]
    return per, (sum(allv) / len(allv) if allv else None)


def notify_admin(text: str) -> None:
    url = os.environ["ELP_SUPABASE_URL"]
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    q = urllib.parse.quote("今川")
    req = urllib.request.Request(f"{url}/rest/v1/closer_line_users?closer_name=eq.{q}&select=line_user_id",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    uid = json.load(urllib.request.urlopen(req, timeout=20))[0]["line_user_id"]
    body = json.dumps({"to": uid, "messages": [{"type": "text", "text": text[:4900]}]}).encode()
    urllib.request.urlopen(urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", data=body, method="POST",
        headers={"Authorization": f"Bearer {os.environ['DEAL_LINE_CHANNEL_ACCESS_TOKEN']}",
                 "Content-Type": "application/json"}), timeout=20)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=dt.datetime.now(JST).strftime("%Y-%m"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--check-only", action="store_true", help="予算の未設定者の確認・通知だけ行う（更新はしない）")
    args = ap.parse_args()
    load_env()

    month, prev = args.month, shift_month(args.month, -1)
    net = prev_month_net_sales(prev)
    per_price, all_price = avg_contract_price(month)

    budget_rows = query_all(f"/data_sources/{BUDGET_DB}/query",
                            {"property": "年月（YYYY-MM）", "rich_text": {"equals": month}}, NOTION_VERSION_V2)
    prev_rows = query_all(f"/data_sources/{BUDGET_DB}/query",
                          {"property": "年月（YYYY-MM）", "rich_text": {"equals": prev}}, NOTION_VERSION_V2)
    set_now = {prop_text(r, "メンバー") for r in budget_rows if prop_num(r, "予算（万円）") is not None}
    missing = sorted({prop_text(r, "メンバー") for r in prev_rows
                      if prop_num(r, "予算（万円）") is not None} - set_now - {""})
    if args.check_only:
        if missing:
            text = (f"⚠ {month}の予算が未設定です（{len(missing)}名）\n" + "・".join(missing)
                    + f"\n「💰 予算と達成率」に{month}の予算を入力してください。")
            print(text)
            if args.notify:
                notify_admin(text)
        else:
            print(f"{month}の予算は全員設定済みです")
        return
    budgets: dict[str, float] = {}
    raised: list[str] = []
    for r in budget_rows:
        name, b = prop_text(r, "メンバー"), prop_num(r, "予算（万円）")
        if not name or b is None:
            continue
        floor = round(net.get(name, 0), 1)
        if b < floor:
            raised.append(f"{name}: {b:g} → {floor:g}万円（{prev}実質売上）")
            if not args.dry_run:
                notion("PATCH", f"/pages/{r['id']}", {"properties": {"予算（万円）": {"number": floor}}})
            b = floor
        budgets[name] = b

    kpi_lines: list[str] = []
    for p in query_all(f"/databases/{KPI_DB}/query", None, NOTION_VERSION_V1):
        name = prop_text(p, "メンバー")
        if name not in budgets:
            continue
        b = budgets[name]
        props: dict = {"今月予算万円": {"number": b}}
        if prop_text(p, "役割") == "クローザー":
            price = per_price.get(name) or all_price
            if not price:
                continue
            target = max(1, round(b / price))
            props["平均契約単価万円_30日"] = {"number": round(price, 1)}
            props["今月契約目標"] = {"number": target}
            kpi_lines.append(f"{name}（クローザー）: 予算{b:g}万円・平均単価{price:.1f}万円 → 契約目標{target}件")
        else:
            target = max(1, math.floor(b / MAN_PER_APO))
            props["今月アポ目標概算"] = {"number": target}
            kpi_lines.append(f"{name}（アポインター）: 予算{b:g}万円 → アポ目標{target}件")
        if not args.dry_run:
            notion("PATCH", f"/pages/{p['id']}", {"properties": props}, NOTION_VERSION_V1)

    report = [f"📅 {month}予算チェック（{prev}の実質売上を下限に設定）"]
    if missing:
        report += [f"⚠ {month}の予算が未設定: " + "・".join(missing)]
    report += (["▼引き上げた予算"] + raised) if raised else ["▼引き上げた予算: なし（全員、前月の結果以上）"]
    report += ["▼KPI DBの月目標"] + kpi_lines
    text = "\n".join(report)
    print(text)
    if args.notify and not args.dry_run:
        notify_admin(text)


if __name__ == "__main__":
    sys.exit(main())
