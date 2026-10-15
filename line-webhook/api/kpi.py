"""個人の単日KPIダッシュボード（Webページ）— LINE Bot「KPIを見る」から開く（フェーズ1）。

既存の「今日の目標を見る」（webhook.py handle_goal_request。テキスト1通）とは別物として
あえて分離した（ユーザー指示: 見れる内容を切り分けたい）。こちらは参考にしたKPIトラッカー
（訪問→在宅/対面→対象→対話→アポのファネルを「行動量（実績/残り）」「転換率（本日/基準/差）」
の表で見せる形）に寄せたビジュアルなWebページで、単日のみに絞っている（週間・月間・
ランキング等は未実装。商談分析運用.mdに追記予定）。

アクセス制御: webhook.py側でHMAC署名付きトークン（member名+有効期限48時間）を発行し、
LINEのReply APIでURLとして本人に返す（週次PDFの署名付きURL配信と同じ考え方）。
署名鍵はDEAL_LINE_CHANNEL_SECRETを流用する（Webhook署名検証専用の値だが、クライアントに
渡らないサーバー専用シークレットという性質は同じなので転用して問題ない。新規シークレットの
追加・Vercel環境変数への反映待ちを避けるための判断）。

データソース:
- 📉 営業部 実績DB（当日の行動量の実績）
- 📊 個人別KPI逆算データ（参照用）（基準となる転換率%、および「今日やるべき訪問数」
  「今日やるべきアポ数」の逆算済み日次目標）
行動量の「残り」は、訪問・アポは上記の逆算済み日次目標をそのまま使い、在宅・対面・対象・対話は
「訪問の日次目標 × 基準%」の連鎖で算出する（KPI逆算データ側に各工程の日次目標そのものは
まだ無いための簡易換算。詳細は商談分析運用.md参照）。

標準ライブラリのみで実装（webhook.pyと同じ方針。cross-fileインポートはVercelのデプロイ単位の
不確実性を避けるためあえて行わず、必要な関数はこのファイル内に複製する）。
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


def row_date_iso(row: dict) -> str | None:
    date_prop = (row.get("properties", {}).get("実績日") or {}).get("date") or {}
    start = date_prop.get("start")
    return start[:10] if start else None


def compute_streak(member_name: str, as_of: date) -> int:
    """簡易版ストリーク（連続稼働日数）。「目標達成」ではなく「その日に実績の報告があったか」
    だけで判定する（ユーザー指示: 簡易版でよい）。月・火は定休日として常にスキップする
    （祝日稼働の例外は見ない。line-webhook側は外部ライブラリ非依存の方針を保つため、
    tools/shuuji側のjpholiday厳密判定とは別物と割り切っている。実際より過小に出ることは
    あっても過大に出ることはない安全側の簡略化）。
    「今日」はまだ日報が入っていないことが多いため含めず、**前日から**遡って数える。
    直近36日分だけ見る（それ以上遡っても意味が薄く、クエリも重くなるため）。
    """
    since = as_of - timedelta(days=36)
    until = as_of - timedelta(days=1)
    try:
        rows = notion_query_performance_range(member_name, since.isoformat(), until.isoformat())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return 0

    active_days: set[str] = set()
    for row in rows:
        d = row_date_iso(row)
        if d and any(raw_number(row, col) for _, _, col in FUNNEL):
            active_days.add(d)

    streak = 0
    d = until
    while d >= since:
        if d.weekday() in (0, 1):  # 月・火（定休日）はスキップ（途切れさせない）
            d -= timedelta(days=1)
            continue
        if d.isoformat() in active_days:
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


def render_page(member_name: str) -> tuple[int, str]:
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
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }}
  th, td {{ padding: 8px 4px; text-align: right; }}
  th.label, td.label {{ text-align: left; font-weight: 600; }}
  thead tr:first-child th {{ font-size: 11px; color: #888; font-weight: 500; padding-bottom: 0; }}
  thead tr:last-child th {{ border-bottom: 1px solid #ddd; padding-bottom: 6px; color: #444; }}
  td.num {{ font-variant-numeric: tabular-nums; }}
  .muted-cell {{ color: #999; }}
  .ok {{ color: #1a9e4c; font-weight: 700; }}
  .warn {{ color: #d4380d; font-weight: 700; }}
  .muted {{ color: #bbb; }}
  .legend {{ font-size: 11px; color: #999; margin-top: 14px; line-height: 1.6; }}
  .note {{ font-size: 13px; color: #d4380d; background: #fff2e8; border-radius: 8px; padding: 8px 10px; margin: 8px 0 0; }}
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

        if member_name is None:
            status, html_body = 403, error_page("リンク切れ", "このリンクは無効か期限切れです。Botで「KPIを見る」ともう一度送ってください。")
        else:
            try:
                status, html_body = render_page(member_name)
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
