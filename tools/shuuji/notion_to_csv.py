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

import calendar
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


# Notionのクエリは、同じ時刻にまとめて作った行のうち1件を返さないことがある（2026-10-20・21に実データで確認。
# 例: 村田旭の10/14と10/17の行が、読むたびにどちらか一方だけ欠け、123行中122行しか返らない。並び順を
# 指定しても直らず、行そのものはNotionに残っている）。その行のタイトルを同じ値で書き直すと、以後は毎回返る。
# そこでREADS回読み、読み取りによって出たり出なかったりする行があれば、そのタイトルを書き直して読み直す。
READS = 3


def notion_query_all(data_source_id: str, filter_obj: dict | None = None) -> list[dict]:
    rows, flaky = _read_union(data_source_id, filter_obj)
    if flaky:
        print(f"[notion_to_csv] Notionの読み取りで出たり欠けたりする行が{len(flaky)}件 → タイトルを書き直して読み直す",
              file=sys.stderr)
        for pid in flaky:
            _reindex_page(rows[pid])
        rows, flaky = _read_union(data_source_id, filter_obj)
        if flaky:
            print(f"[notion_to_csv] 書き直し後も{len(flaky)}件が出たり欠けたりする（読めた分はすべて使う）", file=sys.stderr)
    return list(rows.values())


def _read_union(data_source_id: str, filter_obj: dict | None) -> tuple[dict[str, dict], set[str]]:
    """READS回読んだ和集合と、一部の読み取りにしか出なかった行IDを返す。"""
    rows: dict[str, dict] = {}
    seen: list[set[str]] = []
    for _ in range(READS):
        once = _notion_query_once(data_source_id, filter_obj)
        seen.append({r["id"] for r in once})
        for r in once:
            rows[r["id"]] = r
    return rows, set(rows) - set.intersection(*seen)


def _reindex_page(page: dict) -> None:
    """タイトルを同じ値で書き直す（値は変えない。Notion側の検索の索引を作り直させるため）。"""
    name, prop = next((k, v) for k, v in page["properties"].items() if v["type"] == "title")
    title = [{"text": {"content": t.get("plain_text", "")}} for t in prop["title"]]
    req = urllib.request.Request(f"{NOTION_API}/pages/{page['id']}", method="PATCH",
                                 data=json.dumps({"properties": {name: {"title": title}}}).encode(),
                                 headers={"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
                                          "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30):
        pass


def _notion_query_once(data_source_id: str, filter_obj: dict | None = None) -> list[dict]:
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


# 仮置き: 代理店ごとの管理者フラグに相当するデータがNotion/Supabaseに無いため、
# ここにハードコードする（ユーザー指示: 今川・岡野・門田・柚木は管理メインで訪問・アポに
# 回る数字が恒常的に0に近くなるため、個人シートを商談以降の数字のみに絞り、代理店ランキングの
# 平均算出からも外す）。正式な管理者フラグが予算DB等に追加されたら、ここは不要になる。
MANAGER_NAMES = {"今川", "岡野", "門田", "柚木"}

# 週次MT資料・週次LINE配信に出さない人（個人シート・スライド・ランキング・配信の対象外）。
# 実績DBの行は会社全体の合計には含める（Notionの全社の数字と一致させるため）。代理店の合計からは外し、
# 「週次対象外」という別枠に入れる（代理店の1人当たりの数字が、表示しない人の分でずれないように）
WEEKLY_HIDDEN = {
    "藤江": "催事で稼働しており、週次の集計・配信は一緒にしていない（今川さん指示 2026-10-20）",
}
HIDDEN_BUCKET = "週次対象外"


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

    sb_rows = sb_get("closer_line_users?select=closer_name,role,line_user_id,pdca_pending_week&closer_name=not.is.null")
    pending_by_name = {r["closer_name"]: r.get("pdca_pending_week") for r in sb_rows}
    role_by_name = {r["closer_name"]: r["role"] for r in sb_rows if r.get("role")}
    line_id_by_name = {r["closer_name"]: r["line_user_id"] for r in sb_rows}

    members: dict[str, dict] = {}
    skipped: list[str] = []
    for name, agency in agency_by_name.items():
        if name in WEEKLY_HIDDEN:
            print(f"[notion_to_csv] 週次の対象外: {name}（{WEEKLY_HIDDEN[name]}）", file=sys.stderr)
            continue
        role = role_by_name.get(name)
        line_id = line_id_by_name.get(name)
        if not role or not line_id or not agency:
            skipped.append(name)
            continue
        members[name] = {
            "name": name, "line_user_id": line_id, "role": role, "agency": agency,
            "is_manager": "yes" if name in MANAGER_NAMES else "no",
            "pdca_pending_week": pending_by_name.get(name),
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
                   cooloff=0.0, screen_fail=0.0, sales=0.0, perf_days=0.0)
        for name in members
    }

    for row in perf_rows:
        name = prop_select(row, "メンバー")
        if name not in res:
            continue
        # 売上は実績DBの実質売上（契約金額−キャンセル金額。他アポは半額ずつの本人取り分）。
        # 💰予算と達成率の実績と同じ数字になる。休みの日に計上された契約も含める
        res[name]["sales"] += prop_number(row, "契約金額（万円）") - prop_number(row, "キャンセル金額（万円）")
        res[name]["perf_days"] += 1
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
            if result == RESULT_COOLING_OFF:
                r["cooloff"] += 1
            if result == RESULT_REJECTED:
                r["screen_fail"] += 1

        if appointer in res and members[appointer]["role"] == "appointer":
            r = res[appointer]
            r["meet_own"] += 1
            if result == RESULT_CONTRACT:
                r["win_own"] += 1
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


def prop_formula_str(page: dict, name: str) -> str:
    p = page["properties"].get(name, {})
    return (p.get("formula") or {}).get("string") or ""


def build_totals(start: datetime.date, end: datetime.date, members: dict[str, dict]) -> dict[str, list[float]]:
    """期間の 代理店→[訪問, アポ, 商談, 契約, 売上] と "全社" の合計。個人の合計を足すと
    商談・契約がクローザーとアポインターで二重になるため、個人とは別にここで1回ずつ数える。
      訪問・アポ: 実績DBの全行（休みの日を除く。個人シートと同じ）
      売上: 実績DBの実質売上の全行（他アポは半額ずつ入っているので全行の合計＝会社の売上）
      商談: 商談一覧の件数、契約: 商談一覧で結果＝契約の件数（クーリングオフになった分は含まない）
      代理店: 実績DBの「所属」。商談はクローザーの所属で数える
    """
    perf = notion_query_all(PERFORMANCE_DB, {"and": [
        {"property": "実績日", "date": {"on_or_after": start.isoformat()}},
        {"property": "実績日", "date": {"on_or_before": end.isoformat()}},
    ]})
    deals = notion_query_all(DEAL_DB, {"and": [
        {"property": "商談日時", "date": {"on_or_after": start.isoformat()}},
        {"property": "商談日時", "date": {"on_or_before": end.isoformat()}},
    ]})
    agency_of = {n: m["agency"] for n, m in members.items()}
    tot: dict[str, list[float]] = {}

    def add(agency: str, i: int, v: float) -> None:
        for key in (agency or "（所属なし）", "全社"):
            tot.setdefault(key, [0.0] * 5)[i] += v

    for r in perf:
        name = prop_select(r, "メンバー")
        agency = HIDDEN_BUCKET if name in WEEKLY_HIDDEN else (agency_of.get(name) or prop_formula_str(r, "所属"))
        add(agency, 4, prop_number(r, "契約金額（万円）") - prop_number(r, "キャンセル金額（万円）"))
        if prop_select(r, "出勤状況") != "休み":
            add(agency, 0, prop_number(r, "訪問数"))
            add(agency, 1, prop_number(r, "アポ数"))
    perf_agency = {prop_select(r, "メンバー"): prop_formula_str(r, "所属") for r in perf}
    for d in deals:
        closer = prop_select(d, "クローザー")
        agency = HIDDEN_BUCKET if closer in WEEKLY_HIDDEN else (agency_of.get(closer) or perf_agency.get(closer, ""))
        add(agency, 2, 1)
        if prop_status(d, "結果") == RESULT_CONTRACT:
            add(agency, 3, 1)
    for v in tot.values():
        v[4] = round(v[4], 2)
    return tot


HISTORY_WEEKS = 4  # 先週の売上を比べる、その前の週数


def build_weekly_sales(last_monday: datetime.date, members: dict[str, dict]) -> list[dict]:
    """先週と、その前のHISTORY_WEEKS週の週ごとの売上（実績DBの実質売上＝契約金額−キャンセル金額）。
    kind=agency（代理店と"全社"）／person（週次の対象メンバー）。days＝その週に実績DBの行がある日数
    （0の週は稼働前・未入力として平均から外す）。代理店の分け方はbuild_totalsと同じ。"""
    start = last_monday - datetime.timedelta(weeks=HISTORY_WEEKS)
    end = last_monday + datetime.timedelta(days=6)
    weeks = [start + datetime.timedelta(weeks=i) for i in range(HISTORY_WEEKS + 1)]
    perf = notion_query_all(PERFORMANCE_DB, {"and": [
        {"property": "実績日", "date": {"on_or_after": start.isoformat()}},
        {"property": "実績日", "date": {"on_or_before": end.isoformat()}},
    ]})
    agency_of = {n: m["agency"] for n, m in members.items()}
    keys = [("agency", "全社")] + [("agency", a) for a in dict.fromkeys(agency_of.values())] + \
           [("person", n) for n in members]
    acc = {(k, w): [0.0, 0] for k in keys for w in weeks}
    for r in perf:
        d = datetime.date.fromisoformat(prop_date(r, "実績日")[:10])
        w = d - datetime.timedelta(days=d.weekday())
        name = prop_select(r, "メンバー")
        agency = HIDDEN_BUCKET if name in WEEKLY_HIDDEN else (agency_of.get(name) or prop_formula_str(r, "所属"))
        sales = prop_number(r, "契約金額（万円）") - prop_number(r, "キャンセル金額（万円）")
        for k in (("agency", "全社"), ("agency", agency), ("person", name)):
            if (k, w) in acc:
                acc[(k, w)][0] += sales
                acc[(k, w)][1] += 1
    return [{"kind": k[0], "key": k[1], "week_start": w.isoformat(), "sales": round(v[0], 2), "days": v[1]}
            for (k, w), v in acc.items()]


def build_config(members: dict[str, dict], budget_by_name: dict[str, float], now: datetime.date,
                  week_start: datetime.date) -> dict[str, str]:
    """月の数字は「対象週の日曜が属する月の1日〜その日曜」で数える（週の数字と同じ締め日にそろえる）。"""
    week_end = week_start + datetime.timedelta(days=6)
    month_start = week_end.replace(day=1)
    ndays = calendar.monthrange(week_end.year, week_end.month)[1]
    month = month_start.strftime("%Y-%m")
    deal_rows_month = notion_query_all(DEAL_DB, {"and": [
        {"property": "商談日時", "date": {"on_or_after": month_start.isoformat()}},
        {"property": "商談日時", "date": {"on_or_before": week_end.isoformat()}},
    ]})
    actual_contracts = sum(1 for d in deal_rows_month if prop_status(d, "結果") == RESULT_CONTRACT)
    perf_rows_month = notion_query_all(PERFORMANCE_DB, {"and": [
        {"property": "実績日", "date": {"on_or_after": month_start.isoformat()}},
        {"property": "実績日", "date": {"on_or_before": week_end.isoformat()}},
    ]})
    actual_sales = sum(prop_number(r, "契約金額（万円）") - prop_number(r, "キャンセル金額（万円）")
                       for r in perf_rows_month)
    # 売上目標は💰予算と達成率の当月の全行（LINE未登録の人も含む。実績も全行で数えるため）
    budget_rows = notion_query_all(BUDGET_DB, {"property": "年月（YYYY-MM）", "rich_text": {"equals": month}})
    target_sales = sum(prop_number(r, "予算（万円）") for r in budget_rows)
    # 契約目標＝売上目標÷全社の平均契約単価（前月までの3か月。月初予算チェック scripts/monthly_budget_floor.py
    # と同じ計算）。単価が取れない時だけ65万円/件で代用
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
    from monthly_budget_floor import avg_contract_price
    _, avg_price = avg_contract_price(month)
    target_contracts = round(target_sales / (avg_price or YEN_PER_CONTRACT_MAN))
    days_in_week = sum(1 for i in range(7) if (week_start + datetime.timedelta(days=i)).month == week_end.month)
    return {
        "data_week_start": week_start.isoformat(),
        "month_label": f"{week_end.month}月（{month_start.month}/{month_start.day}〜{week_end.month}/{week_end.day}）",
        "month_elapsed": str(round(week_end.day / ndays, 4)),
        "target_contracts": str(target_contracts),
        "avg_contract_price": str(round(avg_price or YEN_PER_CONTRACT_MAN, 1)),
        "target_sales": str(round(target_sales, 1)),
        "actual_contracts": str(actual_contracts),
        "actual_sales": str(round(actual_sales, 1)),
        # 週の目安＝月目標を日数で割り、先週のうち当月に入る日数分
        "week_target_contracts": str(round(target_contracts * days_in_week / ndays, 2)),
        "week_target_sales": str(round(target_sales * days_in_week / ndays, 2)),
    }


PDCA_TARGET_PROPS = [
    "訪問数目標", "アポ数目標", "商談数目標_自アポ", "商談数目標_他アポ",
    "契約数目標_自アポ", "契約数目標_他アポ",
]


def _rich_text_plain(row: dict, prop_name: str) -> str:
    return "".join(t.get("plain_text", "") for t in row["properties"].get(prop_name, {}).get("rich_text", []))


# LINEの記入テンプレの見出し（新旧）。webhook.pyの解析は①②の行の残りも本文として拾うため、
# 見出しをコピーしたまま返信されると本文の先頭に混ざる。スライドに出す前に取り除く
PDCA_PROMPTS = [
    "先週の振り返り・学び（うまくいったこと／いかなかったことと、その理由）", "先週の振り返り・学び",
    "今週の宿題を1つ（いつ・誰と・何を）", "なぜ低いと思いますか？（原因）",
    "良くするために、今週何をしますか？（いつ・誰と・何を）",
]


def _strip_prompts(text: str) -> str:
    for p in PDCA_PROMPTS:
        text = text.replace(p, "")
    return text.strip()


def pdca_missing_reason(pending_week: str | None, key: str) -> str:
    """先週の宿題（週初日=key）の記録が無い理由。Supabase closer_line_users.pdca_pending_week は
    deliver.pyが配信時にキーをセットし、LINEで返信を受け取るとNULLに戻る（webhook.py handle_pdca_reply）。"""
    if pending_week == key:
        return "先週のMT後、LINEの記入テンプレに返信がありません（未送信）"
    if pending_week and pending_week > key:
        return "記録の週がずれています（今川さんに確認）"
    if pending_week:
        return "先週は記入テンプレが届いていません（それ以前の週から未返信のまま）"
    return "先週は記入テンプレの配信対象に入っていません（予算DB・LINE登録を確認）"


def build_pdca(members: dict[str, dict], week_start: datetime.date) -> dict[str, dict]:
    """週次PDCA記録DBから、対象週より前の直近1件（＝前回LINE返信で declared された
    アクションプラン・今週の数値目標）を氏名ごとに拾う。build_all.pyのreview_page()
    「先週決めたアクション」に自動転記される。行が無い人は結果から自然に省かれる
    （build_all.py側で「（記録なし）」表示）。
    """
    # 先週のMTで決めた宿題＝週初日が「対象週の前週の月曜」の行だけを使う（deliver.pyがその週のキーで
    # 配信し、LINE返信・議事録反映もそのキーで保存する）。それより古い記録を「先週の宿題」として
    # 出さない（以前は直近の記録を拾っていたため、2週以上前の宿題が先週分として表示されていた）
    key = (week_start - datetime.timedelta(days=7)).isoformat()
    rows = notion_query_all(PDCA_DB, {
        "property": "週初日", "date": {"before": week_start.isoformat()},
    })
    latest_by_name: dict[str, tuple[str, dict]] = {}  # name -> (週初日, {action, targets...})
    # 直近でMT評価（◎○×。スキル週次MT議事録反映が議事録から書く）が付いた記録の評価。
    # スライド①に「前回の評価」として出し、2週続けて○・×に気づけるようにする
    last_eval: dict[str, tuple[str, str]] = {}
    for row in rows:
        name = prop_select(row, "氏名")
        if name not in members:
            continue
        row_week = prop_date(row, "週初日") or ""
        ev = prop_select(row, "MT評価")
        if ev and (name not in last_eval or row_week > last_eval[name][0]):
            last_eval[name] = (row_week, ev)
        if row_week != key:
            continue
        if name in latest_by_name and row_week <= latest_by_name[name][0]:
            continue
        data = {"action": _strip_prompts(_rich_text_plain(row, "アクションプラン")),
                "cause": _strip_prompts(_rich_text_plain(row, "原因"))}  # ①先週の振り返り・学び（列名は旧称の「原因」のまま）
        for prop_name in PDCA_TARGET_PROPS:
            data[prop_name] = row["properties"].get(prop_name, {}).get("number")
        latest_by_name[name] = (row_week, data)
    out = {name: data for name, (_, data) in latest_by_name.items() if data["action"]}
    empty = {"action": "", "cause": "", **{p: None for p in PDCA_TARGET_PROPS}}
    for name, (_, ev) in last_eval.items():
        out.setdefault(name, dict(empty))["last_eval"] = ev
    for name, m in members.items():
        if name in latest_by_name and latest_by_name[name][1]["action"]:
            continue
        reason = ("先週のLINE返信に宿題（②）が書かれていません" if name in latest_by_name
                  else pdca_missing_reason(m.get("pdca_pending_week"), key))
        out.setdefault(name, dict(empty))["missing_reason"] = reason
    return out


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
    totals = build_totals(last_monday, week_end, members)
    # 商談一覧と実績DBの突き合わせ（verify_numbers.py）の結果も資料に載せる
    import verify_numbers
    problems = sum(len(verify_numbers.compare(s, e)) for _, s, e in verify_numbers.periods(now))
    config["data_as_of"] = datetime.datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%-m/%-d %H:%M")
    config["check_status"] = "一致" if problems == 0 else f"不一致{problems}件"
    pdca = build_pdca(members, last_monday)
    weekly_sales = build_weekly_sales(last_monday, members)

    write_csv(os.path.join(out_dir, "members.csv"),
              [{k: v for k, v in m.items() if k != "pdca_pending_week"} for m in members.values()],
              ["name", "line_user_id", "role", "agency", "is_manager"])
    write_csv(os.path.join(out_dir, "results.csv"),
              [{"name": name, **vals} for name, vals in results.items()],
              ["name", "visits", "home", "face", "target", "talk", "appo",
               "meet_own", "meet_other", "win_own", "win_other", "cooloff", "screen_fail", "sales", "perf_days"])
    write_config_csv(os.path.join(out_dir, "config.csv"), config)
    write_csv(os.path.join(out_dir, "totals.csv"),
              [{"agency": a, "visits": v[0], "appo": v[1], "meet": v[2], "win": v[3], "sales": v[4]} for a, v in totals.items()],
              ["agency", "visits", "appo", "meet", "win", "sales"])
    write_csv(os.path.join(out_dir, "weekly_sales.csv"), weekly_sales, ["kind", "key", "week_start", "sales", "days"])
    write_csv(os.path.join(out_dir, "pdca.csv"),
              [{"name": name, **data} for name, data in pdca.items()],
              ["name", "action", "cause", "last_eval", "missing_reason", *PDCA_TARGET_PROPS])

    print(f"対象週: {last_monday}〜{week_end} / メンバー{len(members)}名 -> {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "notion_out")
