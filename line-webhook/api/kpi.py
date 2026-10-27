"""個人のKPIダッシュボード（Webページ）— LINE Bot「KPIを見る」から開く（フェーズ1+2）。

既存の「今日の目標を見る」（webhook.py handle_goal_request。テキスト1通）とは別物として
あえて分離した（ユーザー指示: 見れる内容を切り分けたい）。こちらは参考にしたKPIトラッカー
（訪問→在宅/対面→対象→対話→アポのファネルを「行動量（実績/残り）」「転換率（本日/基準/差）」
の表で見せる形）に寄せたビジュアルなWebページ。単日ビュー（フェーズ1）・週間ビュー
（フェーズ2、`?view=week`）・月間ビュー（`?view=month`）・全社ランキング
（`?view=ranking`）・個人の直近4週の推移（`?view=trend`）を実装済み。週次MTG記録・転換率計算機はまだ未実装（商談分析運用.md参照）。

月間ビューの目標値（訪問・アポ）は、週間/単日ビューのような実績DBからの逆算ではなく
webhook.pyの「今日の目標を見る」テキスト版と同じ、KPI DB側の手動メンテ値（`今月訪問実績`+
`今月残り訪問数概算`、`今月アポ目標概算`）を使う（月次の目標はKPI DB側に逆算formulaが
まだ無いため）。実績（アクチュアル）側はこのページの他ビュー同様、実績DBのライブ集計を使う。

ランキングは全社共通・週次（ユーザー決定: 代理店内に絞らず全社、頻度は週次のまま）。
役割（クローザー/アポインター）でアポの個数的なノルマが違うため、絶対数ではなく
「今週の必要アポ数」に対する達成率%で順位付けする（`compute_ranking`）。

アクセス制御: webhook.py側でHMAC署名付きトークン（member名+有効期限48時間）を発行し、
LINEのReply APIでURLとして本人に返す（週次PDFの署名付きURL配信と同じ考え方）。
署名鍵はDEAL_LINE_CHANNEL_SECRETを流用する（Webhook署名検証専用の値だが、クライアントに
渡らないサーバー専用シークレットという性質は同じなので転用して問題ない。新規シークレットの
追加・Vercel環境変数への反映待ちを避けるための判断）。

データソース:
- 📉 営業部 実績DB（行動量の実績。単日はその日、週間は対象週7日分の合計）
- 📊 個人別KPI逆算データ（参照用）（基準となる転換率%、および「今日やるべき訪問数」
  「今日やるべきアポ数」「今週の必要訪問数」「今週の必要アポ数」の逆算済み目標）
行動量の「残り」は、訪問・アポは上記の逆算済み目標をそのまま使い、在宅・対面・対象・対話は
「訪問の目標 × 基準%」の連鎖で算出する（KPI逆算データ側に各工程の目標そのものはまだ無い
ための簡易換算。詳細は商談分析運用.md参照）。

週間ビューの対象週は**水曜始まり火曜終わり**の直近の完了週（`last_wed_tue_week`。
`.claude/skills/週次実績配信/SKILL.md`と同じ週定義。tools/shuuji側の月曜始まりとは別軸、
ユーザー指示）。

ストリーク（連続稼働日数）の「休みの日」判定は、曜日や祝日カレンダーではなく**会社全体で
その日に誰か1人でも稼働報告があったか**で決める（`company_active_days`。ユーザー指示:
月火が祝日なら稼働になることが多い、という以前の暫定ルールより、実データに基づくこちらの
方が正確で汎用的なため採用。カレンダーライブラリ非依存に戻った）。

webhook.py本体と同じ方針で、標準ライブラリのみで実装している（cross-fileインポートは
Vercelのデプロイ単位の不確実性を避けるためあえて行わず、必要な関数はこのファイル内に複製する）。
"""
import hashlib
import hmac
import html
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

LINE_CHANNEL_SECRET = os.environ["DEAL_LINE_CHANNEL_SECRET"]
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_VERSION = "2022-06-28"
NOTION_VERSION_V2 = "2025-09-03"
PERFORMANCE_DATABASE_ID = "f11afda4-a39d-4139-8e12-817d3f70267b"  # 📉 営業部 実績DB（data_source_id）
KPI_DATABASE_ID = "5f110c1c-b977-4054-9b00-5b3ce27d3493"          # 📊 個人別KPI逆算データ（参照用）
DEAL_DATA_SOURCE_ID = "8958bbaf-2c24-4e94-97b2-4c801e60cb37"      # DB 商談分析＆アポ分析（data_source_id）
PDCA_DATA_SOURCE_ID = "17f23c69-8a24-429a-8957-6b5490a87680"      # 📝 週次PDCA記録DB（data_source_id）
JST = timezone(timedelta(hours=9))
TOKEN_TTL_SECONDS = 48 * 3600

FUNNEL = [
    ("訪問", "visits", "訪問数"),
    ("在宅", "home", "在宅数"),
    ("対面", "face", "対面数"),
    ("対象", "target", "対象数"),
    ("対話", "talk", "対話数"),
    ("アポ", "appo", "アポ数"),
]


def verify_token(token: str) -> str | None:
    try:
        name_enc, exp_s, sig = token.split(".", 2)
        exp = int(exp_s)
    except ValueError:
        return None
    if time.time() > exp:
        return None
    member_name = urllib.parse.unquote(name_enc)
    expected = hmac.new(LINE_CHANNEL_SECRET.encode(), f"kpi:{member_name}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, sig):
        return None
    return member_name


def notion_query_kpi_member(member_name: str) -> dict | None:
    if not NOTION_TOKEN:
        return None
    body = json.dumps({
        "filter": {"property": "メンバー", "title": {"equals": member_name}},
        "page_size": 1,
    }).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{KPI_DATABASE_ID}/query", data=body, method="POST",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION,
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        results = json.loads(r.read()).get("results", [])
    return results[0] if results else None


def notion_query_today_performance(member_name: str, day_iso: str) -> dict | None:
    if not NOTION_TOKEN:
        return None
    body = json.dumps({
        "filter": {"and": [
            {"property": "メンバー", "select": {"equals": member_name}},
            {"property": "実績日", "date": {"equals": day_iso}},
        ]},
        "page_size": 1,
    }).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/data_sources/{PERFORMANCE_DATABASE_ID}/query", data=body, method="POST",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION_V2,
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        results = json.loads(r.read()).get("results", [])
    return results[0] if results else None


def notion_query_performance_range(member_name: str, start_iso: str, end_iso: str) -> list[dict]:
    """ストリーク計算用。指定メンバーの指定期間（両端含む）の実績DB行を全件取得する。"""
    if not NOTION_TOKEN:
        return []
    body = json.dumps({
        "filter": {"and": [
            {"property": "メンバー", "select": {"equals": member_name}},
            {"property": "実績日", "date": {"on_or_after": start_iso}},
            {"property": "実績日", "date": {"on_or_before": end_iso}},
        ]},
        "page_size": 100,
    }).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/data_sources/{PERFORMANCE_DATABASE_ID}/query", data=body, method="POST",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION_V2,
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("results", [])


def notion_query_all_performance_range(start_iso: str, end_iso: str) -> list[dict]:
    """会社全体（全メンバー）の実績DB行を期間指定で取得する（休配信日判定用）。
    複数メンバー分なのでnotion_query_performance_range（1人分）と違いページングが必要。
    """
    if not NOTION_TOKEN:
        return []
    results: list[dict] = []
    cursor = None
    while True:
        body: dict = {
            "filter": {"and": [
                {"property": "実績日", "date": {"on_or_after": start_iso}},
                {"property": "実績日", "date": {"on_or_before": end_iso}},
            ]},
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"https://api.notion.com/v1/data_sources/{PERFORMANCE_DATABASE_ID}/query",
            data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION_V2,
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results


def notion_query_all_kpi_members() -> list[dict]:
    """全メンバーの📊個人別KPI逆算データ行を取得する（ランキング用）。"""
    if not NOTION_TOKEN:
        return []
    results: list[dict] = []
    cursor = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{KPI_DATABASE_ID}/query",
            data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION,
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results


def title_text(page: dict, prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name) or {}
    return "".join(t.get("plain_text", "") for t in prop.get("title") or [])


def select_name(page: dict, prop_name: str) -> str | None:
    prop = page.get("properties", {}).get(prop_name) or {}
    select = prop.get("select")
    return select.get("name") if select else None


def row_date_iso(row: dict) -> str | None:
    date_prop = (row.get("properties", {}).get("実績日") or {}).get("date") or {}
    start = date_prop.get("start")
    return start[:10] if start else None


def company_active_days(start: date, end: date) -> set[str]:
    """期間内（両端含む）で、会社全体として誰か1人でも稼働報告があった日付の集合。
    ストリークの「休みの日」判定に使う（ユーザー指示。会社の定休日だけでなく、実データで
    「その日は本当に誰も稼働していなかったか」を直接見る方が、曜日や祝日カレンダーに
    頼るより正確）。
    """
    try:
        rows = notion_query_all_performance_range(start.isoformat(), end.isoformat())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return set()
    active: set[str] = set()
    for row in rows:
        d = row_date_iso(row)
        if d and any(raw_number(row, col) for _, _, col in FUNNEL):
            active.add(d)
    return active


def compute_streak(member_name: str, as_of: date) -> int:
    """簡易版ストリーク（連続稼働日数）。「目標達成」ではなく「その日に実績の報告があったか」
    だけで判定する（ユーザー指示: 簡易版でよい）。「休みの日」（カウントもスキップもしない
    日）は、会社全体で誰も稼働していなかった日だけとする（`company_active_days`。曜日や
    祝日カレンダーではなく実データで判定。ユーザー指示）。誰か1人でも稼働していた日は、
    本人が稼働していなければストリークが途切れる。
    「今日」はまだ日報が入っていないことが多いため含めず、**前日から**遡って数える。
    直近36日分だけ見る（それ以上遡っても意味が薄く、クエリも重くなるため）。
    """
    since = as_of - timedelta(days=36)
    until = as_of - timedelta(days=1)
    try:
        rows = notion_query_performance_range(member_name, since.isoformat(), until.isoformat())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return 0

    personal_active: set[str] = set()
    for row in rows:
        d = row_date_iso(row)
        if d and any(raw_number(row, col) for _, _, col in FUNNEL):
            personal_active.add(d)

    company_days = company_active_days(since, until)

    streak = 0
    d = until
    while d >= since:
        if d.isoformat() not in company_days:  # 会社全体の休配信日はスキップ（途切れさせない）
            d -= timedelta(days=1)
            continue
        if d.isoformat() in personal_active:
            streak += 1
            d -= timedelta(days=1)
        else:
            break
    return streak


def raw_number(page: dict, prop_name: str) -> float:
    prop = page.get("properties", {}).get(prop_name) or {}
    return prop.get("number") or 0


def formula_number(page: dict, prop_name: str) -> float:
    prop = page.get("properties", {}).get(prop_name) or {}
    formula = prop.get("formula") or {}
    return formula.get("number") or 0


def pct(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return 100 * numerator / denominator


def last_wed_tue_week(today: date) -> tuple[date, date]:
    """直近の完了週（水曜始まり・火曜終わり）の(開始日, 終了日)を返す。
    `.claude/skills/週次実績配信/SKILL.md`の対象週定義と同じ（tools/shuuji側の
    月曜始まりとは別軸。ユーザー指示で「先週水曜から火曜まで」）。
    例: 今日が木曜なら、直近の水曜は昨日なので、それより前の完了週（先々週水曜〜先週火曜）
    ではなく、その1つ前＝先週水曜〜火曜を返す。
    """
    days_since_wed = (today.weekday() - 2) % 7  # Wed=2
    this_wed = today - timedelta(days=days_since_wed)
    last_wed = this_wed - timedelta(days=7)
    last_tue = this_wed - timedelta(days=1)
    return last_wed, last_tue


def view_tabs_html(token: str, active: str) -> str:
    def tab(view: str, label: str) -> str:
        cls = "tab active" if view == active else "tab"
        href = f"?token={urllib.parse.quote(token)}&view={view}" if view != "day" else f"?token={urllib.parse.quote(token)}"
        return f'<a class="{cls}" href="{href}">{label}</a>'
    return (
        '<div class="tabs">'
        f'{tab("day", "単日")}{tab("goal", "目標")}{tab("week", "週間")}{tab("trend", "推移")}{tab("month", "月間")}{tab("ranking", "順位")}'
        "</div>"
    )


# アポを回らない人（管理・商談中心）は、週間目標の順位に出さない（2026-10-27 今川さん指示）
RANKING_HIDDEN = {"今川"}


def compute_ranking(week_start: date, week_end: date) -> list[dict]:
    """全社ランキング（ユーザー決定: 代理店内に絞らず全社、頻度は週次のまま）。
    役割（クローザー/アポインター）でアポのノルマの絶対数が違うため、公平に比べるには
    絶対数ではなく「今週の必要アポ数」に対する達成率%を使う。
    """
    rows = notion_query_all_performance_range(week_start.isoformat(), week_end.isoformat())
    appo_by_member: dict[str, float] = {}
    for row in rows:
        name = select_name(row, "メンバー")
        if name:
            appo_by_member[name] = appo_by_member.get(name, 0) + raw_number(row, "アポ数")

    entries = []
    for page in notion_query_all_kpi_members():
        name = title_text(page, "メンバー")
        if not name or name in RANKING_HIDDEN:
            continue
        target = formula_number(page, "今週の必要アポ数")
        actual = appo_by_member.get(name, 0)
        entries.append({"name": name, "actual": actual, "target": target, "rate": pct(actual, target)})

    entries.sort(key=lambda e: (e["rate"] if e["rate"] is not None else -1, e["actual"]), reverse=True)
    return entries


def funnel_row_html(label: str, actual: float, target: float | None,
                     today_rate: float | None, base_rate: float | None, is_base_step: bool) -> str:
    if target is None:
        remain_html = "<span class=\"muted\">—</span>"
    elif actual >= target and target > 0:
        remain_html = "<span class=\"ok\">達成</span>"
    else:
        remain_html = f"<span class=\"warn\">{round(max(0, target - actual))}</span>"

    if is_base_step or today_rate is None or base_rate is None:
        today_cell, base_cell, diff_cell = "—", "—", "—"
    else:
        diff = today_rate - base_rate
        diff_cls = "ok" if diff >= 0 else "warn"
        today_cell = f"{today_rate:.1f}%"
        base_cell = f"{base_rate:.1f}%"
        diff_cell = f"<span class=\"{diff_cls}\">{'+' if diff >= 0 else ''}{diff:.1f}pt</span>"

    return (
        "<tr>"
        f"<td class=\"label\">{html.escape(label)}</td>"
        f"<td class=\"num\">{int(actual)}</td>"
        f"<td class=\"num\">{remain_html}</td>"
        f"<td class=\"num muted-cell\">{today_cell}</td>"
        f"<td class=\"num muted-cell\">{base_cell}</td>"
        f"<td class=\"num muted-cell\">{diff_cell}</td>"
        "</tr>"
    )


def render_page(member_name: str, token: str) -> tuple[int, str]:
    if not NOTION_TOKEN:
        return 503, error_page("設定未完了", "NOTION_TOKENが未設定のため表示できません。今川さんに確認してください。")

    now = datetime.now(JST)
    today_iso = now.date().isoformat()

    try:
        kpi_page = notion_query_kpi_member(member_name)
        perf_page = notion_query_today_performance(member_name, today_iso)
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"kpi.py render_page: Notion取得失敗: {e}")
        return 502, error_page("一時エラー", "データの取得に失敗しました。少し待ってからもう一度開いてください。")

    if kpi_page is None:
        return 404, error_page("データ未登録", f"「{member_name}」のKPIデータが見つかりませんでした。今川さんに確認してください。")

    actuals = {key: (raw_number(perf_page, col) if perf_page else 0) for _, key, col in FUNNEL}

    base_rate = {
        "home": raw_number(kpi_page, "在宅率%"),
        "face": raw_number(kpi_page, "対面率%"),
        "target": raw_number(kpi_page, "対象率%"),
        "talk": raw_number(kpi_page, "対話率%"),
        "appo": raw_number(kpi_page, "アポ率%"),
    }
    # ファネルは厳密に直列（訪問→在宅→対面→対象→対話→アポ）で、各率は「直前の工程→この工程」
    # の転換率（対面率%なら 対面÷在宅）。参考にしたKPIトラッカーの実数値と突き合わせて確認済み
    # （例: 訪問41→在宅29→対面18の場合、対面率は 18÷29≈62.1%。訪問に対する比率ではない）。
    today_rate = {
        "home": pct(actuals["home"], actuals["visits"]),
        "face": pct(actuals["face"], actuals["home"]),
        "target": pct(actuals["target"], actuals["face"]),
        "talk": pct(actuals["talk"], actuals["target"]),
        "appo": pct(actuals["appo"], actuals["talk"]),
    }

    visit_target = formula_number(kpi_page, "今日やるべき訪問数")
    appo_target = formula_number(kpi_page, "今日やるべきアポ数")
    home_target = visit_target * base_rate["home"] / 100
    face_target = home_target * base_rate["face"] / 100
    target_target = face_target * base_rate["target"] / 100
    talk_target = target_target * base_rate["talk"] / 100
    targets = {
        "visits": visit_target, "home": home_target, "face": face_target,
        "target": target_target, "talk": talk_target, "appo": appo_target,
    }

    rows_html = "".join(
        funnel_row_html(
            label, actuals[key], targets.get(key),
            today_rate.get(key), base_rate.get(key), is_base_step=(key == "visits"),
        )
        for label, key, _ in FUNNEL
    )

    data_note = "" if perf_page else "<p class=\"note\">⚠ 本日分の実績DB反映がまだです（反映後に自動で最新化されます）</p>"
    date_label = f"{now.month}/{now.day}（{'月火水木金土日'[now.weekday()]}）"

    streak = compute_streak(member_name, now.date())
    streak_html = f'<div class="streak">🔥 {streak}日連続稼働中</div>' if streak >= 2 else ""

    body = f"""
<div class="card">
  {view_tabs_html(token, "day")}
  <div class="header">
    <div class="name">{html.escape(member_name)}</div>
    <div class="date">{date_label} 単日実績</div>
  </div>
  {streak_html}
  {data_note}
  <table>
    <thead>
      <tr><th class="label"></th><th colspan="2">行動量</th><th colspan="3">転換率（前工程から）</th></tr>
      <tr><th class="label">工程</th><th>実績</th><th>残り</th><th>本日</th><th>基準</th><th>差</th></tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p class="legend">残り＝本日の行動KPIまでの不足数｜基準＝今月の目標設定で使用した転換率</p>
</div>
"""
    return 200, page_shell(body)


def render_week_page(member_name: str, token: str) -> tuple[int, str]:
    if not NOTION_TOKEN:
        return 503, error_page("設定未完了", "NOTION_TOKENが未設定のため表示できません。今川さんに確認してください。")

    now = datetime.now(JST)
    week_start, week_end = last_wed_tue_week(now.date())

    try:
        kpi_page = notion_query_kpi_member(member_name)
        week_rows = notion_query_performance_range(member_name, week_start.isoformat(), week_end.isoformat())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"kpi.py render_week_page: Notion取得失敗: {e}")
        return 502, error_page("一時エラー", "データの取得に失敗しました。少し待ってからもう一度開いてください。")

    if kpi_page is None:
        return 404, error_page("データ未登録", f"「{member_name}」のKPIデータが見つかりませんでした。今川さんに確認してください。")

    actuals = {key: sum(raw_number(row, col) for row in week_rows) for _, key, col in FUNNEL}
    reported_days = {row_date_iso(row) for row in week_rows if row_date_iso(row)}

    base_rate = {
        "home": raw_number(kpi_page, "在宅率%"),
        "face": raw_number(kpi_page, "対面率%"),
        "target": raw_number(kpi_page, "対象率%"),
        "talk": raw_number(kpi_page, "対話率%"),
        "appo": raw_number(kpi_page, "アポ率%"),
    }
    week_rate = {
        "home": pct(actuals["home"], actuals["visits"]),
        "face": pct(actuals["face"], actuals["home"]),
        "target": pct(actuals["target"], actuals["face"]),
        "talk": pct(actuals["talk"], actuals["target"]),
        "appo": pct(actuals["appo"], actuals["talk"]),
    }

    visit_target = formula_number(kpi_page, "今週の必要訪問数")
    appo_target = formula_number(kpi_page, "今週の必要アポ数")
    home_target = visit_target * base_rate["home"] / 100
    face_target = home_target * base_rate["face"] / 100
    target_target = face_target * base_rate["target"] / 100
    talk_target = target_target * base_rate["talk"] / 100
    targets = {
        "visits": visit_target, "home": home_target, "face": face_target,
        "target": target_target, "talk": talk_target, "appo": appo_target,
    }

    rows_html = "".join(
        funnel_row_html(
            label, actuals[key], targets.get(key),
            week_rate.get(key), base_rate.get(key), is_base_step=(key == "visits"),
        )
        for label, key, _ in FUNNEL
    )

    data_note = ""
    if len(reported_days) < 5:  # 水〜火7日のうち月火(定休)を除くと平日5日。目安の簡易チェック
        data_note = (f'<p class="note">⚠ 対象週の実績DB反映は{len(reported_days)}日分のみです'
                     f'（反映が遅れている日があるかもしれません）</p>')

    date_label = f"{week_start.month}/{week_start.day}〜{week_end.month}/{week_end.day}"

    body = f"""
<div class="card">
  {view_tabs_html(token, "week")}
  <div class="header">
    <div class="name">{html.escape(member_name)}</div>
    <div class="date">{date_label}（水〜火） 週間実績</div>
  </div>
  {data_note}
  <table>
    <thead>
      <tr><th class="label"></th><th colspan="2">行動量</th><th colspan="3">転換率（前工程から）</th></tr>
      <tr><th class="label">工程</th><th>実績</th><th>残り</th><th>週計</th><th>基準</th><th>差</th></tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p class="legend">残り＝対象週の行動KPIまでの不足数｜基準＝今月の目標設定で使用した転換率</p>
</div>
"""
    return 200, page_shell(body)


def notion_query_v2(data_source_id: str, filter_obj: dict) -> list[dict]:
    out, cursor = [], None
    while True:
        body: dict = {"filter": filter_obj, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"https://api.notion.com/v1/data_sources/{data_source_id}/query", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION_V2,
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        out += data.get("results", [])
        if not data.get("has_more"):
            return out
        cursor = data.get("next_cursor")


def status_name(page: dict, prop_name: str) -> str:
    st = (page.get("properties", {}).get(prop_name) or {}).get("status")
    return st.get("name", "") if st else ""


def rich_plain(page: dict, prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name) or {}
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))


def goal_block_html(member_name: str, role: str, key: date, start: date, end: date, title: str) -> str:
    """週次MTで宣言した目標（📝週次PDCA記録DB、週初日=key）と、start〜endの実績を並べる。
    実績: 訪問・アポ＝📉実績DB（休みの日を除く）、商談・契約＝商談一覧（契約はクーリングオフになった分を含まない）。
    週次MT資料（tools/shuuji）の個人の数え方と同じ。"""
    recs = notion_query_v2(PDCA_DATA_SOURCE_ID, {"and": [
        {"property": "氏名", "select": {"equals": member_name}},
        {"property": "週初日", "date": {"equals": key.isoformat()}},
    ]})
    date_label = f"{start.month}/{start.day}〜{end.month}/{end.day}"
    if not recs:
        return (f'<h3 class="sec">{html.escape(title)}</h3>'
                f'<p class="note">データがありません：週次MTのあと、LINEの記入テンプレ（①学び②宿題③目標）が送られていません。'
                f'MTの最後に送ってください（議事録から反映された場合もここに出ます）。</p>')
    rec = recs[0]
    perf = notion_query_performance_range(member_name, start.isoformat(), end.isoformat()) if start <= end else []
    work = [r for r in perf if (select_name(r, "出勤状況") or "") != "休み"]
    # 名前での絞り込みはNotion側でしない（選択肢に無い名前を指定すると400になるため。期間で取ってから絞る）
    deals = notion_query_v2(DEAL_DATA_SOURCE_ID, {"and": [
        {"property": "商談日時", "date": {"on_or_after": start.isoformat()}},
        {"property": "商談日時", "date": {"on_or_before": end.isoformat()}},
    ]}) if start <= end else []

    def who(d: dict, prop: str) -> str:
        return select_name(d, prop) or ""
    if role == "クローザー":
        mine = [d for d in deals if who(d, "クローザー") == member_name]
        own = [d for d in mine if who(d, "アポインター") in ("", member_name)]
        other = [d for d in mine if d not in own]
        wins = lambda ds: sum(1 for d in ds if status_name(d, "結果") == "契約")
        rows = [("訪問", "訪問数目標", sum(raw_number(r, "訪問数") for r in work)),
                ("アポ", "アポ数目標", sum(raw_number(r, "アポ数") for r in work)),
                ("商談（自アポ）", "商談数目標_自アポ", len(own)), ("商談（他アポ）", "商談数目標_他アポ", len(other)),
                ("契約（自アポ）", "契約数目標_自アポ", wins(own)), ("契約（他アポ）", "契約数目標_他アポ", wins(other))]
    else:
        mine = [d for d in deals if who(d, "アポインター") == member_name]
        rows = [("訪問", "訪問数目標", sum(raw_number(r, "訪問数") for r in work)),
                ("アポ", "アポ数目標", sum(raw_number(r, "アポ数") for r in work)),
                ("商談", "商談数目標_自アポ", len(mine)),
                ("契約", "契約数目標_自アポ", sum(1 for d in mine if status_name(d, "結果") == "契約"))]
    trs = []
    for label, col, actual in rows:
        target = raw_number(rec, col) if (rec["properties"].get(col) or {}).get("number") is not None else None
        if target is None:
            continue
        rest = max(0, target - actual)
        rest_html = '<span class="ok">達成</span>' if actual >= target and target > 0 else f'<span class="warn">{int(rest)}</span>'
        trs.append(f'<tr><td class="label">{html.escape(label)}</td><td class="num">{int(target)}</td>'
                   f'<td class="num">{int(actual)}</td><td class="num">{rest_html}</td></tr>')
    table = ('<table><thead><tr><th class="label">項目</th><th>目標</th><th>実績</th><th>残り</th></tr></thead>'
             f'<tbody>{"".join(trs)}</tbody></table>') if trs else '<p class="note">データがありません：数字の目標（③）が記入されていません。</p>'
    action = rich_plain(rec, "アクションプラン") or "（宿題の記入なし）"
    learning = rich_plain(rec, "原因")
    src = select_name(rec, "記録元") or "LINE"
    days = sorted({row_date_iso(r) for r in perf if row_date_iso(r)})
    missing_note = ""
    if start <= end:
        # 月・火は基本定休日（tools/shuuji/notion_to_csv.pyのcheck_week_completenessと同じ扱い）
        expected = sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() not in (0, 1))
        if len(days) < expected:
            missing_note = (f'<p class="note">実績DB（日報）は{date_label}のうち{len(days)}日分です。'
                            f'未入力の日があれば実績が少なく表示されます。</p>')
    else:
        missing_note = '<p class="note info">今週の実績はまだありません（明日から反映されます）</p>'
    return (f'<h3 class="sec">{html.escape(title)}</h3>'
            f'<div class="goal"><div class="goal-label">宿題</div><div>{html.escape(action)}</div></div>'
            + (f'<div class="goal"><div class="goal-label">学び</div><div>{html.escape(learning)}</div></div>' if learning else "")
            + table + missing_note
            + f'<p class="legend">実績は{date_label}の記録（訪問・アポ＝日報、商談・契約＝商談一覧。クーリングオフ除く）｜記録元：{html.escape(src)}</p>')


def render_goal_page(member_name: str, token: str) -> tuple[int, str]:
    """今週の目標（水曜の週次MTで決めてLINEで送った宿題・数字）と、月曜〜昨日までの実績。
    月・火（MT前）は今週の目標がまだ無いので、先週の目標と先週1週間の結果を出す。"""
    if not NOTION_TOKEN:
        return 503, error_page("設定未完了", "NOTION_TOKENが未設定のため表示できません。今川さんに確認してください。")
    today = datetime.now(JST).date()
    monday = today - timedelta(days=today.weekday())
    try:
        kpi_page = notion_query_kpi_member(member_name)
        role = (select_name(kpi_page, "役割") if kpi_page else None) or "アポインター"
        # 週次PDCA記録のキー＝目標を実行する週の前週の月曜（tools/shuuji/deliver.pyのpdca_pending_week）
        blocks = []
        if today.weekday() >= 2:
            blocks.append(goal_block_html(member_name, role, monday - timedelta(days=7), monday,
                                          today - timedelta(days=1), f"今週の目標（{monday.month}/{monday.day}〜の週）"))
        else:
            blocks.append('<p class="note info">今週の目標は水曜の週次MTで決めます</p>')
        last_mon = monday - timedelta(days=7)
        blocks.append(goal_block_html(member_name, role, last_mon - timedelta(days=7), last_mon,
                                      monday - timedelta(days=1), f"先週の目標と結果（{last_mon.month}/{last_mon.day}〜の週）"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"kpi.py render_goal_page: Notion取得失敗: {e}")
        return 502, error_page("一時エラー", "データの取得に失敗しました。少し待ってからもう一度開いてください。")
    body = f"""
<div class="card">
  {view_tabs_html(token, "goal")}
  <div class="header">
    <div class="name">{html.escape(member_name)}</div>
    <div class="date">{today.month}/{today.day}時点・昨日までの記録</div>
  </div>
  {"".join(blocks)}
</div>
"""
    return 200, page_shell(body)


TREND_WEEKS = 4  # 推移タブで振り返る、完了した週の数（月曜始まり。週次MTのスライドと同じ週）


def _trend_rows(member_name: str, start: date, end: date) -> list[dict]:
    """期間の実績DB行。Notionは同時刻に作った行の1件を返さないことがある（週次MT側のREADMEと同じ）ので、
    2回読んで和集合を取る。"""
    rows: dict[str, dict] = {}
    for _ in range(2):
        for r in notion_query_performance_range(member_name, start.isoformat(), end.isoformat()):
            rows[r["id"]] = r
    return list(rows.values())


def render_trend_page(member_name: str, token: str) -> tuple[int, str]:
    """個人の直近4週の推移（週次MTでは全体の数字を見るため、個人の数字はリッチメニューのこのページで振り返る）。
    週は月曜〜日曜。売上は契約金額−キャンセル金額（週次MTのスライドと同じ）。LINEのメッセージは使わない。"""
    if not NOTION_TOKEN:
        return 503, error_page("設定未完了", "NOTION_TOKENが未設定のため表示できません。今川さんに確認してください。")
    today = datetime.now(JST).date()
    this_mon = today - timedelta(days=today.weekday())
    first_mon = this_mon - timedelta(weeks=TREND_WEEKS)
    try:
        rows = _trend_rows(member_name, first_mon, today)
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"kpi.py render_trend_page: Notion取得失敗: {e}")
        return 502, error_page("一時エラー", "データの取得に失敗しました。少し待ってからもう一度開いてください。")

    weeks = []  # 古い週→今週（途中）
    for i in range(TREND_WEEKS + 1):
        mon = first_mon + timedelta(weeks=i)
        sun = mon + timedelta(days=6)
        wk = [r for r in rows if row_date_iso(r) and mon.isoformat() <= row_date_iso(r) <= sun.isoformat()]
        work = [r for r in wk if ((r["properties"].get("出勤状況") or {}).get("select") or {}).get("name") != "休み"]
        weeks.append({
            "label": f"{mon.month}/{mon.day}〜" + ("（今週）" if mon == this_mon else ""), "current": mon == this_mon,
            "days": len(wk), "visits": sum(raw_number(r, "訪問数") for r in work),
            "face": sum(raw_number(r, "対面数") for r in work), "appo": sum(raw_number(r, "アポ数") for r in work),
            # 契約はクーリングオフ・審査落ちを引いた件数（週次MT側と同じ）
            "win": sum(raw_number(r, "契約") - raw_number(r, "クーリン") - raw_number(r, "審査落") for r in wk),
            "sales": sum(raw_number(r, "契約金額（万円）") - raw_number(r, "キャンセル金額（万円）") for r in wk),
        })
    done = [w for w in weeks[:-1] if w["days"] > 0]  # 実績DBに行がある週だけで平均する
    last = weeks[-2]

    def cell(v, fmt="{:,.0f}"):
        return fmt.format(v)

    trs = []
    for w in reversed(weeks):  # 新しい週を上に
        rate = (f"{w['appo'] / w['face'] * 100:.1f}%" if w["face"] > 0 else "—")
        style = ' class="dim"' if w["current"] else ""
        if w["days"] == 0:
            trs.append(f'<tr{style}><td class="label">{w["label"]}</td><td colspan="5" class="note">データがありません（日報が入っていません）</td></tr>')
            continue
        trs.append(f'<tr{style}><td class="label">{w["label"]}</td><td class="num">{cell(w["visits"])}</td><td class="num">{cell(w["appo"])}</td>'
                   f'<td class="num">{rate}</td><td class="num">{cell(w["win"])}</td><td class="num">{cell(w["sales"], "{:,.1f}")}</td></tr>')
    if done:
        n = len(done)
        av = lambda k: sum(w[k] for w in done) / n
        rate_avg = (f"{sum(w['appo'] for w in done) / sum(w['face'] for w in done) * 100:.1f}%"
                    if sum(w["face"] for w in done) > 0 else "—")
        trs.append(f'<tr class="avg"><td class="label">{n}週の平均</td><td class="num">{av("visits"):,.0f}</td><td class="num">{av("appo"):,.1f}</td>'
                   f'<td class="num">{rate_avg}</td><td class="num">{av("win"):,.1f}</td><td class="num">{av("sales"):,.1f}</td></tr>')

    summary = ""
    prev = [w for w in done if w is not last]
    if last["days"] > 0 and prev:
        # 売上がある人（クローザー等）は売上、アポ専任の人はアポ数で先週とその前の平均を比べる
        key, label, unit = ("sales", "売上", "万") if any(w["sales"] for w in done) else ("appo", "アポ", "件")
        avg_prev = sum(w[key] for w in prev) / len(prev)
        diff = last[key] - avg_prev
        if last[key] or avg_prev:
            word = "上回りました" if diff > 0 else ("下回りました" if diff < 0 else "同じでした")
            tail = f"を <b>{abs(diff):,.1f}{unit}</b> {word}。" if diff else "と同じでした。"
            summary = (f'<p class="note">先週の{label} <b>{last[key]:,.{1 if unit == "万" else 0}f}{unit}</b> は、その前{len(prev)}週の平均 '
                       f'{avg_prev:,.1f}{unit} {tail}</p>')
    body = f"""
<div class="card">
  {view_tabs_html(token, "trend")}
  <div class="header">
    <div class="name">{html.escape(member_name)}</div>
    <div class="date">直近{TREND_WEEKS}週の推移（月〜日）</div>
  </div>
  {summary}
  <table>
    <thead><tr><th class="label">週</th><th>訪問</th><th>アポ</th><th>対面→アポ率</th><th>契約</th><th>売上(万)</th></tr></thead>
    <tbody>{"".join(trs)}</tbody>
  </table>
  <p class="legend">売上＝キャンセルを引いた実質売上。後からクーリングオフ等が出ると過去の週も減ります。平均は日報がある週だけで計算。「今週」は途中経過です。</p>
</div>
"""
    return 200, page_shell(body)


def render_month_page(member_name: str, token: str) -> tuple[int, str]:
    if not NOTION_TOKEN:
        return 503, error_page("設定未完了", "NOTION_TOKENが未設定のため表示できません。今川さんに確認してください。")

    now = datetime.now(JST)
    month_start = now.date().replace(day=1)
    month_end = now.date()

    try:
        kpi_page = notion_query_kpi_member(member_name)
        month_rows = notion_query_performance_range(member_name, month_start.isoformat(), month_end.isoformat())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"kpi.py render_month_page: Notion取得失敗: {e}")
        return 502, error_page("一時エラー", "データの取得に失敗しました。少し待ってからもう一度開いてください。")

    if kpi_page is None:
        return 404, error_page("データ未登録", f"「{member_name}」のKPIデータが見つかりませんでした。今川さんに確認してください。")

    actuals = {key: sum(raw_number(row, col) for row in month_rows) for _, key, col in FUNNEL}

    base_rate = {
        "home": raw_number(kpi_page, "在宅率%"),
        "face": raw_number(kpi_page, "対面率%"),
        "target": raw_number(kpi_page, "対象率%"),
        "talk": raw_number(kpi_page, "対話率%"),
        "appo": raw_number(kpi_page, "アポ率%"),
    }
    month_rate = {
        "home": pct(actuals["home"], actuals["visits"]),
        "face": pct(actuals["face"], actuals["home"]),
        "target": pct(actuals["target"], actuals["face"]),
        "talk": pct(actuals["talk"], actuals["target"]),
        "appo": pct(actuals["appo"], actuals["talk"]),
    }

    # 月間の目標はKPI DB側の手動メンテ値を使う（webhook.pyの「今日の目標を見る」テキスト版
    # build_progress_blockと同じフィールド・考え方。月次逆算formulaがまだKPI DB側に無いため）。
    # アポの月目標はアポインターのみ設定されている（クローザーは常に0のため、targetはNoneにして
    # funnel_row_htmlの「—」表示に委ねる）
    visit_target = raw_number(kpi_page, "今月訪問実績") + raw_number(kpi_page, "今月残り訪問数概算")
    role_name = select_name(kpi_page, "役割")
    appo_target = raw_number(kpi_page, "今月アポ目標概算") if role_name == "アポインター" else None
    home_target = visit_target * base_rate["home"] / 100
    face_target = home_target * base_rate["face"] / 100
    target_target = face_target * base_rate["target"] / 100
    talk_target = target_target * base_rate["talk"] / 100
    targets = {
        "visits": visit_target, "home": home_target, "face": face_target,
        "target": target_target, "talk": talk_target, "appo": appo_target,
    }

    rows_html = "".join(
        funnel_row_html(
            label, actuals[key], targets.get(key),
            month_rate.get(key), base_rate.get(key), is_base_step=(key == "visits"),
        )
        for label, key, _ in FUNNEL
    )

    date_label = f"{month_start.month}月（{month_start.day}〜{month_end.day}日・進行中）"

    body = f"""
<div class="card">
  {view_tabs_html(token, "month")}
  <div class="header">
    <div class="name">{html.escape(member_name)}</div>
    <div class="date">{date_label} 月間実績</div>
  </div>
  <table>
    <thead>
      <tr><th class="label"></th><th colspan="2">行動量</th><th colspan="3">転換率（前工程から）</th></tr>
      <tr><th class="label">工程</th><th>実績</th><th>残り</th><th>月計</th><th>基準</th><th>差</th></tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p class="legend">残り＝今月の行動KPIまでの不足数｜基準＝今月の目標設定で使用した転換率｜アポの月目標はアポインターのみ表示</p>
</div>
"""
    return 200, page_shell(body)


def render_ranking_page(member_name: str, token: str) -> tuple[int, str]:
    if not NOTION_TOKEN:
        return 503, error_page("設定未完了", "NOTION_TOKENが未設定のため表示できません。今川さんに確認してください。")

    now = datetime.now(JST)
    week_start, week_end = last_wed_tue_week(now.date())

    try:
        entries = compute_ranking(week_start, week_end)
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"kpi.py render_ranking_page: Notion取得失敗: {e}")
        return 502, error_page("一時エラー", "データの取得に失敗しました。少し待ってからもう一度開いてください。")

    if not entries:
        return 404, error_page("データ未登録", "ランキングを計算できるデータがありませんでした。今川さんに確認してください。")

    my_rank = None
    rows_html = ""
    for i, e in enumerate(entries, start=1):
        is_me = e["name"] == member_name
        if is_me:
            my_rank = i
        rate_label = f"{e['rate']:.0f}%" if e["rate"] is not None else "—"
        row_cls = " class=\"me\"" if is_me else ""
        rank_label = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, str(i))
        rows_html += (
            f"<tr{row_cls}>"
            f"<td class=\"rank\">{rank_label}</td>"
            f"<td class=\"label\">{html.escape(e['name'])}</td>"
            f"<td class=\"num\">{int(e['actual'])}</td>"
            f"<td class=\"num\">{rate_label}</td>"
            "</tr>"
        )

    date_label = f"{week_start.month}/{week_start.day}〜{week_end.month}/{week_end.day}"
    rank_note = f'<p class="note info">あなたは{my_rank}位／{len(entries)}人中です</p>' if my_rank else ""

    body = f"""
<div class="card">
  {view_tabs_html(token, "ranking")}
  <div class="header">
    <div class="name">全社ランキング</div>
    <div class="date">{date_label}（水〜火） アポ週目標達成率</div>
  </div>
  {rank_note}
  <table>
    <thead><tr><th class="rank"></th><th class="label">メンバー</th><th>アポ数</th><th>達成率</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p class="legend">達成率＝今週の必要アポ数に対する実績の割合（週間ビューと同じ目標値を使用）｜役割が違っても達成率%で公平に比較</p>
</div>
"""
    return 200, page_shell(body)


def error_page(title: str, message: str) -> str:
    body = f'<div class="card"><div class="header"><div class="name">{html.escape(title)}</div></div><p class="note">{html.escape(message)}</p></div>'
    return page_shell(body)


def page_shell(body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KPI</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 16px; background: #f4f5f7; font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif; color: #1a1a1a; }}
  .card {{ background: #fff; border-radius: 16px; padding: 20px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); max-width: 480px; margin: 0 auto; }}
  .header {{ display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #1a1a1a; padding-bottom: 10px; margin-bottom: 4px; }}
  .name {{ font-size: 20px; font-weight: 700; }}
  .date {{ font-size: 13px; color: #666; }}
  .streak {{ font-size: 13px; font-weight: 700; color: #d4380d; margin-top: 8px; }}
  .tabs {{ display: flex; gap: 6px; margin-bottom: 14px; }}
  .tab {{ flex: 1; text-align: center; padding: 8px 0; border-radius: 10px; background: #f0f1f3; color: #666; font-size: 13px; font-weight: 700; text-decoration: none; }}
  .tab.active {{ background: #1a1a1a; color: #fff; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }}
  th, td {{ padding: 8px 4px; text-align: right; }}
  th.label, td.label {{ text-align: left; font-weight: 600; }}
  thead tr:first-child th {{ font-size: 11px; color: #888; font-weight: 500; padding-bottom: 0; }}
  thead tr:last-child th {{ border-bottom: 1px solid #ddd; padding-bottom: 6px; color: #444; }}
  td.num {{ font-variant-numeric: tabular-nums; }}
  tr.dim td {{ color: #999; }}
  tr.avg td {{ font-weight: 700; border-top: 2px solid #1a1a1a; }}
  .muted-cell {{ color: #999; }}
  .ok {{ color: #1a9e4c; font-weight: 700; }}
  .warn {{ color: #d4380d; font-weight: 700; }}
  .muted {{ color: #bbb; }}
  .legend {{ font-size: 11px; color: #999; margin-top: 14px; line-height: 1.6; }}
  .note {{ font-size: 13px; color: #d4380d; background: #fff2e8; border-radius: 8px; padding: 8px 10px; margin: 8px 0 0; }}
  .note.info {{ color: #1a5fd4; background: #eef3ff; font-weight: 700; text-align: center; }}
  td.rank, th.rank {{ width: 28px; text-align: center; font-weight: 700; }}
  tr.me {{ background: #fff8e1; }}
  tr.me td.label {{ font-weight: 700; }}
  h3.sec {{ font-size: 15px; margin: 18px 0 6px; padding-left: 8px; border-left: 4px solid #1a1a1a; }}
  .goal {{ display: flex; gap: 8px; font-size: 14px; line-height: 1.6; margin: 6px 0; }}
  .goal-label {{ flex: none; font-size: 12px; font-weight: 700; color: #fff; background: #1a1a1a; border-radius: 6px; padding: 1px 8px; height: fit-content; margin-top: 2px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
        member_name = verify_token(token) if token else None

        view = (qs.get("view") or ["day"])[0]

        if member_name is None:
            status, html_body = 403, error_page("リンク切れ", "このリンクは無効か期限切れです。Botで「KPIを見る」ともう一度送ってください。")
        else:
            try:
                if view == "goal":
                    status, html_body = render_goal_page(member_name, token)
                elif view == "week":
                    status, html_body = render_week_page(member_name, token)
                elif view == "trend":
                    status, html_body = render_trend_page(member_name, token)
                elif view == "month":
                    status, html_body = render_month_page(member_name, token)
                elif view == "ranking":
                    status, html_body = render_ranking_page(member_name, token)
                else:
                    status, html_body = render_page(member_name, token)
            except Exception as e:  # ページ全体が落ちるのを防ぐ
                print(f"kpi.py do_GET error: {e}")
                status, html_body = 500, error_page("エラー", "表示中にエラーが発生しました。少し待ってからもう一度お試しください。")

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html_body.encode("utf-8"))

    def do_POST(self):
        self.send_response(501)
        self.end_headers()
