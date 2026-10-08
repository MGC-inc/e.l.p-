#!/usr/bin/env python3
"""Notion（実績DB・商談分析DB・予算DB）とSupabase（closer_line_users）から、
LINE配信キット（build_all.py / build_meeting.py）が読める members.csv / results.csv / config.csv
を生成する（商談分析運用.md 8章「Notion→CSVブリッジ」）。LLMは一切使わない決定的コード。

「メンバーDB」に相当するものがNotionに存在しないため、以下を突き合わせて作る:
- 代理店・予算（月目標の元）: Notion「💰 予算と達成率」の当月行（代理店・予算（万円）が入っている行のみ）
- 役割・LINEユーザーID: Supabase closer_line_users（role・line_user_id）
- 両方に存在する人だけを対象にする（予算未設定・LINE未登録の行は仮置きの都合で除外。
  実行時ログに「対象外にした人」を出すので、本来含めるべき人がいれば予算DB側に予算行を追加すればよい）

活動量（訪問・在宅・対面・対象・対話・アポ）: 📉営業部実績DBの対象週（月〜日）の合計
商談・契約（自アポ/他アポ・クーリングオフ・審査落ち・売上）: DB商談分析＆アポ分析の対象週の集計
月の目標・実績: 予算DB当月合計（目標）／実績DB当月MTD合計・商談分析DB当月契約数（実績）

使い方:
    python3 notion_to_csv.py <出力フォルダ>
環境変数: NOTION_TOKEN, ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

import jpholiday

# このワークスペースの3DBは複数データソース対応版に移行済みで、旧来の
# `/v1/databases/{database_id}/query` + Notion-Version 2022-06-28 では404になる
# （実データで確認済み。共有設定の問題ではない）。`/v1/data_sources/{data_source_id}/query` +
# 新しいNotion-Versionを使う。IDは database_id ではなく data_source_id（Notion MCPの
# collection://<id> 表記と同じ値）である点に注意
NOTION_VERSION = "2025-09-03"
NOTION_API = "https://api.notion.com/v1"

PERFORMANCE_DB = "f11afda4-a39d-4139-8e12-817d3f70267b"  # 📉 営業部 実績DB（data_source_id）
DEAL_DB = "8958bbaf-2c24-4e94-97b2-4c801e60cb37"          # DB 商談分析＆アポ分析（data_source_id）
BUDGET_DB = "b1809b1a-e82d-4032-ac86-a32d165d475d"        # 💰 予算と達成率（data_source_id）
PDCA_DB = "17f23c69-8a24-429a-8957-6b5490a87680"          # 📝 週次PDCA記録DB（data_source_id）

RESULT_CONTRACT = "契約"
RESULT_COOLING_OFF = "クーリングオフ"
RESULT_REJECTED = "審査落ち"

# 契約単価の仮置き（webhook.pyのcontract_target等、既存の運用と同じ65万円/件を踏襲）
YEN_PER_CONTRACT_MAN = 65


def notion_query_all(data_source_id: str, filter_obj: dict | None = None) -> list[dict]:
    token = os.environ["NOTION_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION,
               "Content-Type": "application/json"}
    results: list[dict] = []
    cursor = None
    while True:
        body: dict = {"page_size": 100}
        if filter_obj:
            body["filter"] = filter_obj
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(f"{NOTION_API}/data_sources/{data_source_id}/query",
                                      data=json.dumps(body).encode(), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        results.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return results


def prop_number(page: dict, name: str) -> float:
    p = page["properties"].get(name, {})
    return p.get("number") or 0.0


def prop_select(page: dict, name: str) -> str:
    p = page["properties"].get(name, {})
    sel = p.get("select")
    return sel["name"] if sel else ""


def prop_status(page: dict, name: str) -> str:
    p = page["properties"].get(name, {})
    st = p.get("status")
    return st["name"] if st else ""


def prop_date(page: dict, name: str) -> str | None:
    p = page["properties"].get(name, {})
    d = p.get("date")
    return d["start"][:10] if d and d.get("start") else None


def prop_checkbox(page: dict, name: str) -> bool:
    return bool(page["properties"].get(name, {}).get("checkbox"))


def sb_get(path: str) -> list[dict]:
    url = os.environ["ELP_SUPABASE_URL"]
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    req = urllib.request.Request(f"{url}/rest/v1/{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def build_members(now: datetime.date) -> dict[str, dict]:
    """予算DB当月行×Supabase closer_line_usersの積集合をメンバーロースターにする。"""
    month = now.strftime("%Y-%m")
    budget_rows = notion_query_all(BUDGET_DB, {
        "and": [
            {"property": "年月（YYYY-MM）", "rich_text": {"equals": month}},
            {"property": "予算（万円）", "number": {"is_not_empty": True}},
        ]
    })
    agency_by_name: dict[str, str] = {}
    budget_by_name: dict[str, float] = {}
    for row in budget_rows:
        name = prop_select(row, "メンバー")
        if not name:
            continue
        agency_by_name[name] = prop_select(row, "代理店")
        budget_by_name[name] = prop_number(row, "予算（万円）")

    sb_rows = sb_get("closer_line_users?select=closer_name,role,line_user_id&closer_name=not.is.null")
    role_by_name = {r["closer_name"]: r["role"] for r in sb_rows if r.get("role")}
    line_id_by_name = {r["closer_name"]: r["line_user_id"] for r in sb_rows}

    members: dict[str, dict] = {}
    skipped: list[str] = []
    for name, agency in agency_by_name.items():
        role = role_by_name.get(name)
        line_id = line_id_by_name.get(name)
        if not role or not line_id or not agency:
            skipped.append(name)
            continue
        members[name] = {
            "name": name, "line_user_id": line_id, "role": role, "agency": agency,
            "is_manager": "no",  # 仮置き: 代理店ごとの管理者フラグに相当するデータが無いため一律no
        }
    if skipped:
        print(f"[notion_to_csv] 予算はあるがLINE未登録／役割未設定のため対象外: {', '.join(skipped)}",
              file=sys.stderr)
    return members, budget_by_name


def build_results(members: dict[str, dict], week_start: datetime.date, week_end: datetime.date) -> dict[str, dict]:
    perf_rows = notion_query_all(PERFORMANCE_DB, {
        "and": [
            {"property": "実績日", "date": {"on_or_after": week_start.isoformat()}},
            {"property": "実績日", "date": {"on_or_before": week_end.isoformat()}},
        ]
    })
    deal_rows = notion_query_all(DEAL_DB, {
        "and": [
            {"property": "商談日時", "date": {"on_or_after": week_start.isoformat()}},
            {"property": "商談日時", "date": {"on_or_before": week_end.isoformat()}},
        ]
    })

    res: dict[str, dict] = {
        name: dict(visits=0.0, home=0.0, face=0.0, target=0.0, talk=0.0, appo=0.0,
                   meet_own=0.0, meet_other=0.0, win_own=0.0, win_other=0.0,
                   cooloff=0.0, screen_fail=0.0, sales=0.0)
        for name in members
    }

    for row in perf_rows:
        name = prop_select(row, "メンバー")
        if name not in res:
            continue
        if prop_select(row, "出勤状況") == "休み":
            continue
        res[name]["visits"] += prop_number(row, "訪問数")
        res[name]["home"] += prop_number(row, "在宅数")
        res[name]["face"] += prop_number(row, "対面数")
        res[name]["target"] += prop_number(row, "対象数")
        res[name]["talk"] += prop_number(row, "対話数")
        res[name]["appo"] += prop_number(row, "アポ数")

    for deal in deal_rows:
        closer = prop_select(deal, "クローザー")
        appointer = prop_select(deal, "アポインター")
        result = prop_status(deal, "結果")
        # DB商談分析＆アポ分析の「売上金額」は円単位（number_format:"yen"）。
        # results.csvのsalesは万円単位（実績DBの契約金額（万円）と単位を揃える）にするため万円へ変換する
        revenue = prop_number(deal, "売上金額") / 10000

        if closer in res and members[closer]["role"] == "closer":
            is_own = appointer == closer
            r = res[closer]
            if is_own:
                r["meet_own"] += 1
            else:
                r["meet_other"] += 1
            if result == RESULT_CONTRACT:
                if is_own:
                    r["win_own"] += 1
                else:
                    r["win_other"] += 1
                r["sales"] += revenue
            if result == RESULT_COOLING_OFF:
                r["cooloff"] += 1
            if result == RESULT_REJECTED:
                r["screen_fail"] += 1

        if appointer in res and members[appointer]["role"] == "appointer":
            r = res[appointer]
            r["meet_own"] += 1
            if result == RESULT_CONTRACT:
                r["win_own"] += 1
                r["sales"] += revenue
            if result == RESULT_COOLING_OFF:
                r["cooloff"] += 1
            if result == RESULT_REJECTED:
                r["screen_fail"] += 1

    return res


def check_week_completeness(week_start: datetime.date, week_end: datetime.date) -> None:
    """対象週の実績DB反映漏れを検知する（ユーザー報告で発覚: 配信日時点で対象週の一部の
    日がまるごと未反映のまま、その不完全なデータでPDCAが配信されてしまった実例があった。
    日次反映に数日のラグがあるのは正常な運用だが、水曜配信時点で対象週（先週）の一部の日が
    丸ごと未反映なのは異常）。対象週の各日の行数を、同じ週の最多日と比べて極端に少ない日が
    あれば、配信全体を中止する（誤った数字でのPDCA配信を未然に防ぐのが目的なので、
    build_all.pyの--strict「週がズレていないか」より手前の、ここで止める）。

    月・火は基本定休日（ユーザー指示）のため、この2日は未反映でも異常とみなさない。
    ただし祝日の場合は稼働するため、月・火が祝日の場合は通常の曜日と同様にチェックする
    （`jpholiday`で判定。内部的に祝日を計算するのみでネットワークアクセスはしない）。
    """
    perf_rows = notion_query_all(PERFORMANCE_DB, {
        "and": [
            {"property": "実績日", "date": {"on_or_after": week_start.isoformat()}},
            {"property": "実績日", "date": {"on_or_before": week_end.isoformat()}},
        ]
    })
    counts: dict[str, int] = {}
    for row in perf_rows:
        d = prop_date(row, "実績日")
        if d:
            counts[d] = counts.get(d, 0) + 1
    all_days = [week_start + datetime.timedelta(days=i) for i in range(7)]
    check_days = [d for d in all_days if d.weekday() not in (0, 1) or jpholiday.is_holiday(d)]
    max_count = max(counts.values(), default=0)
    sparse = [(d.isoformat(), counts.get(d.isoformat(), 0)) for d in check_days
              if max_count and counts.get(d.isoformat(), 0) < max_count * 0.5]
    if sparse:
        detail = ", ".join(f"{d}（{c}件）" for d, c in sparse)
        sys.exit(
            f"中止: 対象週（{week_start}〜{week_end}）の実績DB反映が不完全です。"
            f"反映が薄い日: {detail}（同じ週の最多日は{max_count}件。月・火の定休日は対象外）。"
            f"日次実績の入力漏れを確認・反映してから再実行してください。送信していません。"
        )


def build_config(members: dict[str, dict], budget_by_name: dict[str, float], now: datetime.date,
                  week_start: datetime.date) -> dict[str, str]:
    month_start = now.replace(day=1)
    deal_rows_month = notion_query_all(DEAL_DB, {
        "property": "商談日時", "date": {"on_or_after": month_start.isoformat()},
    })
    actual_contracts = sum(1 for d in deal_rows_month if prop_status(d, "結果") == RESULT_CONTRACT)

    perf_rows_month = notion_query_all(PERFORMANCE_DB, {
        "property": "実績日", "date": {"on_or_after": month_start.isoformat()},
    })
    # 月実績（実質売上）は「登録済みメンバーだけの合計」ではなく会社全体の実績（Notion上の
    # 実質売上合計と一致させる）。予算・LINE未登録の人（例: 催事）の実績もここには含める
    # （ユーザー指摘で発覚。以前はmembersで絞ってしまい、Notion上の値より少なく出ていた）
    actual_sales = sum(
        prop_number(row, "契約金額（万円）") - prop_number(row, "キャンセル金額（万円）")
        for row in perf_rows_month
    )

    target_sales = sum(budget_by_name.get(n, 0.0) for n in members)
    target_contracts = round(target_sales / YEN_PER_CONTRACT_MAN) if target_sales else 0

    return {
        "data_week_start": week_start.isoformat(),
        "target_contracts": str(target_contracts),
        "target_sales": str(target_sales),
        "actual_contracts": str(actual_contracts),
        "actual_sales": str(round(actual_sales, 1)),
        # week_target_* は空欄のままにする → build_all.py側で 月目標×7/当月日数 に自動フォールバック
        # （依頼2章の仕様どおり）
    }


PDCA_TARGET_PROPS = [
    "訪問数目標", "アポ数目標", "商談数目標_自アポ", "商談数目標_他アポ",
    "契約数目標_自アポ", "契約数目標_他アポ",
]


def _rich_text_plain(row: dict, prop_name: str) -> str:
    return "".join(t.get("plain_text", "") for t in row["properties"].get(prop_name, {}).get("rich_text", []))


def build_pdca(members: dict[str, dict], week_start: datetime.date) -> dict[str, dict]:
    """週次PDCA記録DBから、対象週より前の直近1件（＝前回LINE返信で declared された
    アクションプラン・今週の数値目標）を氏名ごとに拾う。build_all.pyのreview_page()
    「先週決めたアクション」に自動転記される。行が無い人は結果から自然に省かれる
    （build_all.py側で「（記録なし）」表示）。
    """
    rows = notion_query_all(PDCA_DB, {
        "property": "週初日", "date": {"before": week_start.isoformat()},
    })
    latest_by_name: dict[str, tuple[str, dict]] = {}  # name -> (週初日, {action, targets...})
    for row in rows:
        name = prop_select(row, "氏名")
        if name not in members:
            continue
        row_week = prop_date(row, "週初日") or ""
        if name in latest_by_name and row_week <= latest_by_name[name][0]:
            continue
        data = {"action": _rich_text_plain(row, "アクションプラン")}
        for prop_name in PDCA_TARGET_PROPS:
            data[prop_name] = row["properties"].get(prop_name, {}).get("number")
        latest_by_name[name] = (row_week, data)
    return {name: data for name, (_, data) in latest_by_name.items() if data["action"]}


def write_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def write_config_csv(path: str, kv: dict[str, str]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["key", "value"])
        for k, v in kv.items():
            w.writerow([k, v])


def main(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.datetime.now(ZoneInfo("Asia/Tokyo")).date()
    # 対象週=直近の月曜始まり日曜終わり（先週）。依頼2章「週の開始日（月曜）」に合わせる
    # （週次実績配信スキルの「水曜始まり火曜終わり」とは別軸。/shuuji系の対象週はNotion側の
    # 週フォーミュラ・LINE配信キット双方とも月曜始まりのため、ここもそれに合わせる）
    last_monday = now - datetime.timedelta(days=now.weekday() + 7)
    week_end = last_monday + datetime.timedelta(days=6)

    members, budget_by_name = build_members(now)
    if not members:
        sys.exit("対象メンバーが0人です（予算DB・Supabase双方に存在する人がいません）。")

    check_week_completeness(last_monday, week_end)
    results = build_results(members, last_monday, week_end)
    config = build_config(members, budget_by_name, now, last_monday)
    pdca = build_pdca(members, last_monday)

    write_csv(os.path.join(out_dir, "members.csv"), list(members.values()),
              ["name", "line_user_id", "role", "agency", "is_manager"])
    write_csv(os.path.join(out_dir, "results.csv"),
              [{"name": name, **vals} for name, vals in results.items()],
              ["name", "visits", "home", "face", "target", "talk", "appo",
               "meet_own", "meet_other", "win_own", "win_other", "cooloff", "screen_fail", "sales"])
    write_config_csv(os.path.join(out_dir, "config.csv"), config)
    write_csv(os.path.join(out_dir, "pdca.csv"),
              [{"name": name, **data} for name, data in pdca.items()],
              ["name", "action", *PDCA_TARGET_PROPS])

    print(f"対象週: {last_monday}〜{week_end} / メンバー{len(members)}名 -> {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "notion_out")
