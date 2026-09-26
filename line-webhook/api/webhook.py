"""LINE Webhook — 商談録音分析の受付（Vercel Python Function）

「ユメイク営業分析bot」宛てのメッセージを受け付ける。

- 初回メッセージの送信者は、LINE表示名がKNOWN_CLOSERS/KNOWN_APPOINTERS/KNOWN_ADMINSに
  一致すれば即座に本登録される。一致しない場合はあいさつメッセージで
  お名前（苗字）→クローザー/アポインター/管理者の役割、の2問で自己登録してもらう
  （closer_line_users.role）。
- 登録済みのクローザーが録音を送ると、アポインター→お客様名→結果→
  分析する/しない、の4問クイックリプライで必要事項を確定させ、
  Supabase（deal_recordings・Storage）に記録する。
- アポインター・管理者は録音を送らず、週次の実績配信（別途のバッチ処理）の
  宛先として line_user_id を保持するためだけに登録する。管理者は個人の実績
  ではなく週次のチーム全体の結果だけを受け取る。

標準ライブラリのみで実装（外部SDK不使用）。詳細: 商談分析運用.md セクション3。
"""
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

LINE_CHANNEL_SECRET = os.environ["DEAL_LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]
SUPABASE_URL = os.environ["DEAL_SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["DEAL_SUPABASE_SERVICE_ROLE_KEY"]

# 「今日の目標を見る」リッチメニュー用（任意設定。未設定でも録音受付フローは動く）。
# NOTION_TOKENのセットアップ: 商談分析運用.md セクション6-6参照。
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_VERSION = "2022-06-28"
# 「📊 個人別KPI逆算データ（参照用）」DBのdatabase_id（固定値・秘密情報ではない）
KPI_DATABASE_ID = "5f110c1c-b977-4054-9b00-5b3ce27d3493"
# 「DB 商談分析＆アポ分析（契約案件一覧）」DBのdatabase_id（固定値・秘密情報ではない）
DEAL_DATABASE_ID = "aa496718-e62f-4a70-818d-953492cad435"
# 「📉 営業部 実績DB」のdatabase_id（固定値・秘密情報ではない）。日報の「備考」欄から
# 前日共有した内容を拾うために使う（要Notionインテグレーション共有。他の2DBとは別）
PERFORMANCE_DATABASE_ID = "f11afda4-a39d-4139-8e12-817d3f70267b"
GOAL_BUTTON_TEXT = "今日の目標を見る"
ANALYSIS_BUTTON_TEXT = "直近の商談分析結果を見る"
# ⚔️営業ステータスの評価基準を知りたい時にテキストで送ってもらう合言葉（低コスト運用:
# リッチメニューのボタンは増やさず、この文字列を送るとReply APIで基準一覧を返すだけにする）
STATUS_DEFINITION_TEXT = "評価基準"
# scripts/deal_bot_richmenu.py が作成するクローザー専用メニューの名前（IDで固定せず
# 名前で引くことで、デザインを作り直してIDが変わっても追従できるようにする）
CLOSER_RICHMENU_NAME = "営業分析BOT クローザー用"
# LINEのReply APIは応答メッセージとして扱われ、料金プランのメッセージ配信数には
# カウントされない（LINE Developers公式ドキュメントで確認済み。2026-09時点）。
# そのため既定はfalseで、連打時も毎回Notionから最新値を取り直して返す。
# 仕様変更等でカウント対象になった場合のみ環境変数で true にする
# （1ユーザー1日1回に制限し、2回目以降は直近の内容を再送する）。
REPLY_COUNTS_TOWARD_QUOTA = os.environ.get("REPLY_COUNTS_TOWARD_QUOTA", "false").lower() == "true"
JST = timezone(timedelta(hours=9))

RESULT_OPTIONS = ["契約", "保留", "失注", "クーリングオフ", "審査落ち", "キャンセル"]
PENDING_STATUSES = "awaiting_appointer,awaiting_customer,awaiting_result,awaiting_confirm"

# クローザー（自分で商談録音をbotに送る役割）。LINEの表示名が一致すれば初回メッセージで
# 自動的にクローザーとして認識される（今川さんの手動承認を待たずに録音を送れる）。
# LINE表示名は本名・姓のみ・姓名（スペースあり/なし）などブレがあるため、想定される
# 表記を複数登録しておく。新規メンバーや表記が一致しない場合は従来どおり手動登録
# （scripts/add_closer.py）が必要。
# 代理店別の現在の稼働メンバー（2026-09時点。今川さん確認済み）:
#   E.L.P（自社）=今川 / ピタサチ=門田・三浦 / wanny=岡野・宮腰 / TRYGROUP=柚木 / ネクアス=福本
# 要: 入退社・表記変更があったら随時更新する（従業員.md と揃える）
KNOWN_CLOSERS = {
    "今川": "今川", "今川吉輝": "今川", "今川 吉輝": "今川",
    "門田": "門田", "門田義斗": "門田", "門田 義斗": "門田",
    "三浦": "三浦", "三浦虎之介": "三浦", "三浦 虎之介": "三浦",
    "岡野": "岡野", "岡野翔": "岡野", "岡野 翔": "岡野",
    "宮腰": "宮腰", "宮腰幹士": "宮腰", "宮腰 幹士": "宮腰",
    "柚木": "柚木",
    "福本": "福本",
}

# アポインターも同様にLINE表示名が一致すれば即座に本登録される（role="appointer"）。
# 表記が一致しない場合は他の新規メンバーと同じくあいさつ→苗字→役割選択の
# 自己登録フローに進む。
KNOWN_APPOINTERS = {
    "藤江": "藤江", "藤江真白": "藤江", "藤江 真白": "藤江",
    "戸田": "戸田", "戸田昴": "戸田", "戸田 昴": "戸田",
    "巻田": "巻田",
    "林": "林",
    "安達": "安達",
    "平井": "平井",
    "山川": "山川",
    "田村": "田村", "田村征大": "田村", "田村 征大": "田村",
}

# 管理者（週次のチーム全体の結果だけ受け取る役割）。通常は人数が少ないため空でよい。
# クローザー・アポインターと同様、LINE表示名が一致すれば即座に本登録される（role="admin"）。
KNOWN_ADMINS = {}

# 役割ごとの登録完了メッセージ。手動登録フロー（handle_registration）と自動一致登録
# （handle_event、KNOWN_CLOSERS等にLINE表示名が完全一致した場合）の両方から参照する。
ROLE_WELCOME_MESSAGES = {
    "closer": "登録完了しました。以後、商談録音をMP3形式のファイルで送信してください。",
    "appointer": "登録完了しました。毎週、実績をお送りします。",
    "admin": "登録完了しました。毎週、チーム全体の実績をお送りします。",
}

# ---- KNOWN_CLOSERS/KNOWN_APPOINTERS/KNOWN_ADMINSの追加・削除 ----
# 手で編集せず scripts/add_closer.py・scripts/remove_closer.py を使うこと
# （Claude操作マニュアル.md セクション9参照）。


# ---- LINE API ----------------------------------------------------------

def verify_signature(body: bytes, signature: str) -> bool:
    mac = hmac.new(LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return hmac.compare_digest(expected, signature or "")


def _line_request(url: str, method: str, data=None, extra_headers=None) -> bytes:
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def line_reply(reply_token: str, messages: list):
    body = json.dumps({"replyToken": reply_token, "messages": messages}).encode()
    _line_request("https://api.line.me/v2/bot/message/reply", "POST", body,
                  {"Content-Type": "application/json"})


def line_get_content(message_id: str) -> bytes:
    return _line_request(f"https://api-data.line.me/v2/bot/message/{message_id}/content", "GET")


def line_get_profile(user_id: str) -> dict:
    try:
        raw = _line_request(f"https://api.line.me/v2/bot/profile/{user_id}", "GET")
        return json.loads(raw)
    except urllib.error.HTTPError:
        return {}


def quick_reply(labels: list) -> dict:
    return {"items": [
        {"type": "action", "action": {"type": "message", "label": label, "text": label}}
        for label in labels
    ]}


def link_closer_richmenu(line_user_id: str) -> None:
    """役割がクローザーに確定したユーザーに、2ボタン（今日の目標＋直近の商談分析結果）の
    専用リッチメニューを個別リンクする。アポインター・管理者は全員向けデフォルト
    （今日の目標を見るのみ）のままでよいため、ここは呼ばない。
    scripts/deal_bot_richmenu.py が未実行・メニュー未作成の場合は何もしない
    （登録フロー自体は失敗させない）。
    """
    try:
        menus = json.loads(_line_request("https://api.line.me/v2/bot/richmenu/list", "GET")).get("richmenus", [])
        target = next((m for m in menus if m.get("name") == CLOSER_RICHMENU_NAME), None)
        if not target:
            return
        _line_request(f"https://api.line.me/v2/bot/user/{line_user_id}/richmenu/{target['richMenuId']}", "POST")
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        pass  # メニュー未作成・API一時エラー等。登録フロー自体は継続させる


# ---- Supabase (PostgREST + Storage) ------------------------------------

def sb(method: str, path: str, data=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def sb_storage_upload(bucket: str, path: str, content: bytes, content_type: str):
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    req = urllib.request.Request(url, data=content, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60):
        pass


def find_closer(line_user_id: str):
    rows = sb("GET", f"closer_line_users?line_user_id=eq.{line_user_id}&select=*")
    return rows[0] if rows else None


def register_unknown_sender(line_user_id: str) -> dict:
    """初回メッセージの送信者をcloser_line_usersに登録する。
    LINE表示名がKNOWN_CLOSERS/KNOWN_APPOINTERSに一致すれば、closer_name・role
    を即座に確定させる（今川さんの手動承認や本人の自己申告を待たずに、
    以降のメッセージ・録音（クローザーの場合）を処理できる）。
    一致しない場合は closer_name・role とも空のまま登録し、handle_event側の
    あいさつ→苗字→役割選択フローに委ねる。
    """
    existing = sb("GET", f"closer_line_users?line_user_id=eq.{line_user_id}&select=*")
    if existing:
        return existing[0]
    profile = line_get_profile(line_user_id)
    display_name = profile.get("displayName", "")
    key = display_name.strip()

    closer_name = KNOWN_CLOSERS.get(key)
    role = "closer" if closer_name else None
    if not closer_name:
        closer_name = KNOWN_APPOINTERS.get(key)
        role = "appointer" if closer_name else None
    if not closer_name:
        closer_name = KNOWN_ADMINS.get(key)
        role = "admin" if closer_name else None

    row = {"line_user_id": line_user_id, "display_name": display_name, "closer_name": closer_name, "role": role}
    created = sb("POST", "closer_line_users", [row])
    if role == "closer":
        link_closer_richmenu(line_user_id)
    return created[0] if created else row


def latest_pending_recording(line_user_id: str):
    rows = sb(
        "GET",
        f"deal_recordings?line_user_id=eq.{line_user_id}"
        f"&status=in.({PENDING_STATUSES})&order=received_at.desc&limit=1&select=*",
    )
    return rows[0] if rows else None


def to_customer_label(text: str) -> str:
    text = text.strip()
    if not text or text.endswith("邸"):
        return text
    return f"{text}邸"


# ---- Notion（📊 個人別KPI逆算データ（参照用）） ----------------------------
# 「通知本文（LINE配信用）」等はNotion側の数式（formula）プロパティで、Notion公式
# REST APIでしか計算結果の文字列を取得できない（Notion MCP経由では参照URLしか
# 返らず中身が読めない）。そのためこのbotだけは他の処理と違い、Notion REST APIを
# 直接叩く（tools/roadmap/sync_notion_to_json.py と同じ方式）。

def notion_query_member(member_name: str) -> dict | None:
    """「📊 個人別KPI逆算データ（参照用）」DBから、メンバー名が完全一致する1行を取得する。
    NOTION_TOKEN未設定、または該当行がなければNone。
    """
    if not NOTION_TOKEN:
        return None
    body = json.dumps({
        "filter": {"property": "メンバー", "title": {"equals": member_name}},
        "page_size": 1,
    }).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{KPI_DATABASE_ID}/query",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        results = json.loads(r.read()).get("results", [])
    return results[0] if results else None


def notion_formula_text(page: dict, prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name) or {}
    formula = prop.get("formula") or {}
    return (formula.get("string") or "").strip()


def notion_richtext_plain(page: dict, prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name) or {}
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))


def notion_query_recent_performance(member_name: str, before_date_iso: str, limit: int = 30) -> list[dict]:
    """「📉 営業部 実績DB」から、指定メンバーの指定日より前の実績行を新しい順にlimit件取得する。
    ⚔️営業ステータス（行動力・アポ力・継続力）と「前回共有した内容」の算出に共用する
    （1回のクエリにまとめてNotion APIの呼び出し回数を抑える）。NOTION_TOKEN未設定・
    DB未共有時は空リスト（呼び出し側で静かにスキップされる）。
    """
    if not NOTION_TOKEN:
        return []
    body = json.dumps({
        "filter": {
            "and": [
                {"property": "メンバー", "select": {"equals": member_name}},
                {"property": "実績日", "date": {"before": before_date_iso}},
            ]
        },
        "sorts": [{"property": "実績日", "direction": "descending"}],
        "page_size": limit,
    }).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{PERFORMANCE_DATABASE_ID}/query",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("results", [])


def latest_remark_from_rows(rows: list[dict]) -> str | None:
    """直近の実績行から、非空の「備考」を持つ最初の1件を返す（＝前回報告時の共有内容。
    何日前であっても直近のものをそのまま返す。例: 水曜にタップしたら日曜の備考が返る）。
    """
    for row in rows:
        remark = notion_richtext_plain(row, "備考")
        if remark:
            return remark
    return None


def formula_number(page: dict, prop_name: str) -> float:
    prop = page.get("properties", {}).get(prop_name) or {}
    formula = prop.get("formula") or {}
    return formula.get("number") or 0


def raw_number(page: dict, prop_name: str) -> float:
    prop = page.get("properties", {}).get(prop_name) or {}
    return prop.get("number") or 0


def row_number(row: dict, prop_name: str) -> float:
    """実績DBの1行から、数値プロパティ（formula・number どちらでも）を取り出す。"""
    prop = row.get("properties", {}).get(prop_name) or {}
    if "formula" in prop:
        return (prop.get("formula") or {}).get("number") or 0
    return prop.get("number") or 0


def row_date_iso(row: dict) -> str | None:
    date_prop = (row.get("properties", {}).get("実績日") or {}).get("date") or {}
    start = date_prop.get("start")
    return start[:10] if start else None


def is_working_row(row: dict) -> bool:
    """「出勤状況」が休みの日は行動量・継続力の計算対象から外す（未入力の過去データは
    出勤扱いとして残す）。"""
    select = (row.get("properties", {}).get("出勤状況") or {}).get("select")
    name = select.get("name") if select else None
    return name != "休み"


def stdev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean_v = sum(values) / n
    return (sum((v - mean_v) ** 2 for v in values) / n) ** 0.5


def stars_text(value: float, bands: list[tuple[float, int]]) -> str:
    """valueがband閾値以上の中で最大のstar数を採用する（bandsは昇順の(閾値, star数)）。
    どの閾値も満たさなければ最低保証の1つ星にする。"""
    count = 1
    for threshold, n in bands:
        if value >= threshold:
            count = n
    return "⭐" * count + "☆" * (5 - count)


def cv_of(values: list[float]) -> float | None:
    """変動係数（標準偏差 ÷ 平均）を返す。データが3件未満、または平均が0以下なら
    None（呼び出し側でこの指標を除外する）。"""
    if len(values) < 3:
        return None
    mean_v = sum(values) / len(values)
    if mean_v <= 0:
        return None
    return stdev(values) / mean_v


def build_status_block(page: dict, recent_rows: list[dict], role: str, now: datetime) -> str:
    """⚔️ 営業ステータス（ユーザー指示によるゲーム性付与）。**全項目、直近1週間
    （過去7日・出勤日のみ）の実績DB生データから算出する**。各項目は
    「項目名（説明）」→改行→「★の数（実数）」の2行形式で表示する
    （ユーザー指示: 「項目（説明）改行／★★★（〇％OR〇件）の形式」）。
    - 行動力（週間アポ数）: 週間アポ数の合計（15件以下★1・25件以下★2・40件以下★3・
      60件以下★4・61件以上★5。役割問わず共通）
    - 商談力・商談化力（ユーザー指示: アポインター側は「商談化力」という名称にする）:
      - クローザー＝商談力（週間商談数）: 週間商談数の合計（1件以下★1・2件★2・3件★3・4件★4・
        5件以上★5）
      - アポインター＝商談化力（商談作成率）: Σ有効商談作成数 ÷ Σアポ数（%）
        （5%以下★1・15%以下★2・25%以下★3・40%以下★4・41%以上★5）
    - 「決め切る力」（ユーザー指示: アポインターは`アポ力`・クローザーは`クロージング力`と
      呼び名を分けるが、どちらも同じ考え方＝直近1週間の転換率で評価する）:
      - クローザー＝クロージング力（契約率）: Σ契約 ÷ Σ商談数（%）
      - アポインター＝アポ力（アポ率）: Σアポ数 ÷ Σ対話数（%）
      - 両方とも同じ閾値を使う（10%以下★1・15%以下★2・30%以下★3・40%以下★4・
        41%以上★5。まだアポ力側の実データで検証していないため、必要なら調整する）
    - 継続力（安定度）: 訪問数・商談数・契約（週間・出勤日）それぞれの変動係数
      （標準偏差÷平均）から「安定度%」= (1 - 変動係数の平均) × 100（0〜100にクランプ）
      に変換して表示する（ユーザー指示: 変動係数は直感的でないため数値化し直す）。
      安定度10%未満★1・10〜39%★2・40〜59%★3・60〜79%★4・80%以上★5。
      対象データが1つも無ければ中間評価（3つ星）にする
    """
    is_closer = role == "closer"
    week_ago_iso = (now.date() - timedelta(days=7)).isoformat()
    week_rows = [r for r in recent_rows if (row_date_iso(r) or "") >= week_ago_iso]
    week_working_rows = [r for r in week_rows if is_working_row(r)]

    # 行動力: 週間アポ数合計（役割問わず共通）
    weekly_apo = sum(row_number(r, "アポ数") for r in week_working_rows)
    action = stars_text(weekly_apo, [(0, 1), (16, 2), (26, 3), (41, 4), (61, 5)])
    action_line = f"行動力（週間アポ数）\n{action}（{int(weekly_apo)}件）"

    if is_closer:
        weekly_deals = sum(row_number(r, "商談数") for r in week_working_rows)
        deal = stars_text(weekly_deals, [(0, 1), (2, 2), (3, 3), (4, 4), (5, 5)])
        deal_line = f"商談力（週間商談数）\n{deal}（{int(weekly_deals)}件）"
    else:
        valid_deals = sum(row_number(r, "有効商談作成数") for r in week_working_rows)
        deal_rate = (100 * valid_deals / weekly_apo) if weekly_apo > 0 else 0
        deal = stars_text(deal_rate, [(0, 1), (6, 2), (16, 3), (26, 4), (41, 5)])
        deal_line = f"商談化力（商談作成率）\n{deal}（{round(deal_rate)}%）"

    # 「決め切る力」: クローザー＝クロージング力（契約率）、アポインター＝アポ力（アポ率）
    # 呼び名は違うが同じ閾値で評価する（ユーザー指示）
    close_bands = [(0, 1), (11, 2), (16, 3), (31, 4), (41, 5)]
    if is_closer:
        weekly_contracts = sum(row_number(r, "契約") for r in week_working_rows)
        weekly_deals_for_close = sum(row_number(r, "商談数") for r in week_working_rows)
        close_rate = (100 * weekly_contracts / weekly_deals_for_close) if weekly_deals_for_close > 0 else 0
        close = stars_text(close_rate, close_bands)
        close_line = f"クロージング力（契約率）\n{close}（{round(close_rate)}%）"
    else:
        weekly_taiwa = sum(row_number(r, "対話数") for r in week_working_rows)
        apo_rate = (100 * weekly_apo / weekly_taiwa) if weekly_taiwa > 0 else 0
        apo_power = stars_text(apo_rate, close_bands)
        close_line = f"アポ力（アポ率）\n{apo_power}（{round(apo_rate)}%）"

    visit_vals = [row_number(r, "訪問数") for r in week_working_rows]
    deal_vals = [row_number(r, "商談数") for r in week_working_rows]
    contract_vals = [row_number(r, "契約") for r in week_working_rows]
    cvs = [c for c in (cv_of(visit_vals), cv_of(deal_vals), cv_of(contract_vals)) if c is not None]
    if cvs:
        avg_cv = sum(cvs) / len(cvs)
        stability_pct = max(0, min(100, round((1 - avg_cv) * 100)))
        cont = stars_text(stability_pct, [(0, 1), (10, 2), (40, 3), (60, 4), (80, 5)])
        cont_line = f"継続力（安定度）\n{cont}（{stability_pct}%）"
    else:
        cont_line = "継続力（安定度）\n⭐⭐⭐☆☆（データ不足）"

    return "\n\n".join([
        "⚔️ 営業ステータス",
        action_line,
        deal_line,
        close_line,
        cont_line,
        f"※基準の詳細は「{STATUS_DEFINITION_TEXT}」と送ってください",
    ])


def build_status_definition_text(role: str) -> str:
    """「評価基準」と送られた時にReply APIで返す、⚔️営業ステータス各項目の
    ★の付け方一覧（build_status_blockの閾値と一致させる。低コスト運用: リッチメニューの
    ボタンを増やす代わりに、この合言葉テキストで確認できるようにした）。
    """
    is_closer = role == "closer"
    if is_closer:
        deal_def = "商談力（週間商談数）\n1件以下★1・2件★2・3件★3・4件★4・5件以上★5"
        close_def = "クロージング力（契約率＝契約÷商談数）\n10%以下★1・15%以下★2・30%以下★3・40%以下★4・41%以上★5"
    else:
        deal_def = "商談化力（商談作成率＝有効商談作成数÷アポ数）\n5%以下★1・15%以下★2・25%以下★3・40%以下★4・41%以上★5"
        close_def = "アポ力（アポ率＝アポ数÷対話数）\n10%以下★1・15%以下★2・30%以下★3・40%以下★4・41%以上★5"

    return "\n\n".join([
        "⚔️ 営業ステータスの評価基準",
        "行動力（週間アポ数）\n15件以下★1・25件以下★2・40件以下★3・60件以下★4・61件以上★5",
        deal_def,
        close_def,
        "継続力（安定度＝訪問数・商談数・契約の波の無さを0〜100%に変換）\n"
        "10%未満★1・10〜39%★2・40〜59%★3・60〜79%★4・80%以上★5",
        "※すべて直近1週間（出勤日のみ）の実績が対象です",
    ])


def handle_status_definition_request(event: dict, closer: dict):
    """「評価基準」テキストへの応答。Reply APIのみ使用（配信数を消費しない）。"""
    reply_token = event["replyToken"]
    role = closer.get("role") or ""
    line_reply(reply_token, [{"type": "text", "text": build_status_definition_text(role)}])


def notion_query_latest_deal(closer_name: str) -> dict | None:
    """「DB 商談分析＆アポ分析（契約案件一覧）」DBから、指定クローザーの
    直近の商談日時1件を取得する。NOTION_TOKEN未設定、または該当行がなければNone。
    """
    if not NOTION_TOKEN:
        return None
    body = json.dumps({
        "filter": {"property": "クローザー", "select": {"equals": closer_name}},
        "sorts": [{"property": "商談日時", "direction": "descending"}],
        "page_size": 1,
    }).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{DEAL_DATABASE_ID}/query",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        results = json.loads(r.read()).get("results", [])
    return results[0] if results else None


def notion_title_text(page: dict, prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name) or {}
    return "".join(t.get("plain_text", "") for t in prop.get("title", []))


def notion_status_name(page: dict, prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name) or {}
    status = prop.get("status")
    return status.get("name", "") if status else ""


def notion_rollup_status(page: dict, prop_name: str) -> str | None:
    """「在籍状況(名簿参照)」のような show_unique ロールアップ（selectの配列）から
    先頭の選択肢名を取り出す。未設定・空配列ならNone。
    """
    prop = page.get("properties", {}).get(prop_name) or {}
    rollup = prop.get("rollup") or {}
    for item in rollup.get("array", []):
        select = item.get("select")
        if select and select.get("name"):
            return select["name"]
    return None


def notion_query_month_deals(role_prop: str, name: str, month_start_iso: str) -> list[dict]:
    """「DB 商談分析＆アポ分析（契約案件一覧）」DBから、今月分（商談日時が月初以降）の
    本人の行を全件取得する。`role_prop`は"クローザー"または"アポインター"（本人の役割に
    応じてどちらの列で絞り込むかを切り替える）。契約/成約の集計に使う。
    """
    if not NOTION_TOKEN:
        return []
    body = json.dumps({
        "filter": {
            "and": [
                {"property": role_prop, "select": {"equals": name}},
                {"property": "商談日時", "date": {"on_or_after": month_start_iso}},
            ]
        },
        "page_size": 100,
    }).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{DEAL_DATABASE_ID}/query",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("results", [])


def count_contracts(deal_rows: list[dict]) -> tuple[int, int]:
    """月間の(契約件数, 成約件数)を返す。ユーザー指示: 「クーリングオフが来た場合、
    契約数に一旦カウントした上で、契約と成約を分けて出力」。
    - 契約（gross）: 結果が「契約」または「クーリングオフ」の件数
      （クーリングオフ発生時は結果が「契約」から「クーリングオフ」に書き換わるため、
      両方を合算することで「一旦契約になった件数」を保つ）
    - 成約（net）: 結果が今も「契約」のままの件数（クーリングオフで取り消された分を除く）
    """
    gross = sum(1 for r in deal_rows if notion_status_name(r, "結果") in ("契約", "クーリングオフ"))
    net = sum(1 for r in deal_rows if notion_status_name(r, "結果") == "契約")
    return gross, net


def contract_target(page: dict, role: str) -> int:
    """契約目標(月)。クローザーは`今月契約目標`の実数、アポインターは
    `今月予算万円 ÷ 65万円`（契約単価60〜70万円の中間値。ユーザー指示）で概算する。"""
    if role == "closer":
        t = raw_number(page, "今月契約目標")
        return int(round(t)) if t else 0
    budget = raw_number(page, "今月予算万円")
    return max(1, round(budget / 65)) if budget > 0 else 0


def build_progress_block(page: dict, role: str, gross_contracts: int, net_contracts: int) -> str:
    """📊 進捗（訪問・アポ・契約/成約の週/月の目標・実績・達成率）。
    契約行はNotion数式ではなく、その場で「DB 商談分析＆アポ分析」を集計して出す
    （クーリングオフを契約/成約に分けて出すため。notion_query_month_deals参照）。
    """
    visit_w_target = formula_number(page, "今週の必要訪問数")
    visit_w_actual = raw_number(page, "今週実績訪問数")
    visit_w_pct = round(100 * visit_w_actual / visit_w_target) if visit_w_target > 0 else 0
    visit_m_actual = raw_number(page, "今月訪問実績")
    visit_m_target = visit_m_actual + raw_number(page, "今月残り訪問数概算")
    visit_m_pct = round(100 * visit_m_actual / visit_m_target) if visit_m_target > 0 else 0

    apo_w_target = formula_number(page, "今週の必要アポ数")
    apo_w_actual = raw_number(page, "今週実績アポ数")
    apo_w_pct = round(100 * apo_w_actual / apo_w_target) if apo_w_target > 0 else 0
    apo_line = f"アポ: 週{int(apo_w_actual)}/{int(apo_w_target)}件({apo_w_pct}%)"
    if role == "appointer":
        apo_m_target = raw_number(page, "今月アポ目標概算")
        apo_m_actual = raw_number(page, "今月アポ実績")
        apo_m_pct = round(100 * apo_m_actual / apo_m_target) if apo_m_target > 0 else 0
        apo_line += f"　月{int(apo_m_actual)}/{int(apo_m_target)}件({apo_m_pct}%)"

    target = contract_target(page, role)
    pct = round(100 * net_contracts / target) if target > 0 else 0
    cooling = gross_contracts - net_contracts
    cooling_note = f"（うちクーリングオフ{cooling}件）" if cooling > 0 else ""
    contract_line = f"契約: 月{gross_contracts}件{cooling_note}／成約{net_contracts}/{target}件({pct}%)"

    return (
        "📊 進捗\n"
        f"訪問: 週{int(visit_w_actual)}/{int(visit_w_target)}件({visit_w_pct}%)　"
        f"月{int(visit_m_actual)}/{int(visit_m_target)}件({visit_m_pct}%)\n"
        f"{apo_line}\n"
        f"{contract_line}"
    )


# ---- イベント処理 ---------------------------------------------------------

def handle_audio_message(event: dict, closer: dict, extra_messages=None):
    message = event["message"]
    message_id = message["id"]
    line_user_id = event["source"]["userId"]
    reply_token = event["replyToken"]

    if message["type"] == "file":
        file_name = message.get("fileName", "recording.mp3")
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "mp3"
    else:  # audio
        ext = "m4a"
    content_type = mimetypes.guess_type(f"x.{ext}")[0] or "application/octet-stream"

    audio_bytes = line_get_content(message_id)
    now = datetime.now(timezone.utc)
    storage_path = f"{now:%Y}/{now:%Y-%m-%d}_{line_user_id}_{message_id}.{ext}"
    sb_storage_upload("deal-recordings", storage_path, audio_bytes, content_type)

    sb("POST", "deal_recordings", [{
        "line_user_id": line_user_id,
        "closer_name": closer.get("closer_name"),
        "storage_path": storage_path,
        "status": "awaiting_appointer",
        "received_at": now.isoformat(),
    }])

    line_reply(reply_token, [*(extra_messages or []), {
        "type": "text",
        "text": "録音を受け取りました。アポインターは誰ですか？（お名前を入力してください）",
    }])


def handle_text_message(event: dict):
    line_user_id = event["source"]["userId"]
    reply_token = event["replyToken"]
    text = event["message"]["text"]

    row = latest_pending_recording(line_user_id)
    if not row:
        return  # 対象の録音がない状態でのメッセージ（雑談等）は無視

    status = row["status"]
    row_id = row["id"]

    if status == "awaiting_appointer":
        sb("PATCH", f"deal_recordings?id=eq.{row_id}",
           {"appointer": text, "status": "awaiting_customer"})
        line_reply(reply_token, [{"type": "text", "text": "お客様の苗字を教えてください（例: 杉浦）"}])

    elif status == "awaiting_customer":
        customer_name = to_customer_label(text)
        sb("PATCH", f"deal_recordings?id=eq.{row_id}",
           {"customer_name": customer_name, "status": "awaiting_result"})
        line_reply(reply_token, [{
            "type": "text",
            "text": f"{customer_name}ですね。商談の結果は？",
            "quickReply": quick_reply(RESULT_OPTIONS),
        }])

    elif status == "awaiting_result":
        if text not in RESULT_OPTIONS:
            line_reply(reply_token, [{
                "type": "text", "text": "ボタンから選んでください。",
                "quickReply": quick_reply(RESULT_OPTIONS),
            }])
            return
        sb("PATCH", f"deal_recordings?id=eq.{row_id}",
           {"result": text, "status": "awaiting_confirm"})
        line_reply(reply_token, [{
            "type": "text", "text": "この商談を分析しますか？",
            "quickReply": quick_reply(["分析する", "分析しない"]),
        }])

    elif status == "awaiting_confirm":
        if text == "分析する":
            sb("PATCH", f"deal_recordings?id=eq.{row_id}", {"status": "ready"})
            line_reply(reply_token, [{
                "type": "text",
                "text": f"受け付けました。{row.get('customer_name', '')}は次回の分析対象です。",
            }])
        elif text == "分析しない":
            sb("PATCH", f"deal_recordings?id=eq.{row_id}", {"status": "skipped"})
            line_reply(reply_token, [{"type": "text", "text": "承知しました。分析対象外として記録しました。"}])
        else:
            line_reply(reply_token, [{
                "type": "text", "text": "ボタンから選んでください。",
                "quickReply": quick_reply(["分析する", "分析しない"]),
            }])


def handle_registration(event: dict, closer: dict, is_first_contact: bool) -> bool:
    """closer_name・role が未確定の相手からのメッセージをあいさつ登録フローで処理する。
    処理した（＝これ以上event側で扱う必要がない）場合True、
    登録済みで通常フローに進んでよい場合Falseを返す。
    """
    reply_token = event["replyToken"]
    message = event.get("message", {})

    if closer.get("closer_name") is None:
        if is_first_contact:
            line_reply(reply_token, [{
                "type": "text",
                "text": "はじめまして。ユメイク営業分析botです。担当者確認のため、お名前（苗字）を送ってください。",
            }])
        elif message.get("type") == "text" and message.get("text", "").strip():
            name = message["text"].strip()
            sb("PATCH", f"closer_line_users?id=eq.{closer['id']}", {"closer_name": name})
            line_reply(reply_token, [{
                "type": "text",
                "text": f"{name}さんですね。クローザー（商談録音を送る）・アポインター（週次の実績だけ受け取る）・"
                        "管理者（週次のチーム全体の結果だけ受け取る）、どちらですか？",
                "quickReply": quick_reply(["クローザー", "アポインター", "管理者"]),
            }])
        else:
            line_reply(reply_token, [{"type": "text", "text": "お名前（苗字）をテキストで送ってください。"}])
        return True

    if closer.get("role") is None:
        text = message.get("text", "") if message.get("type") == "text" else ""
        role_map = {"クローザー": "closer", "アポインター": "appointer", "管理者": "admin"}
        if text in role_map:
            role = role_map[text]
            sb("PATCH", f"closer_line_users?id=eq.{closer['id']}", {"role": role})
            if role == "closer":
                link_closer_richmenu(closer["line_user_id"])
            line_reply(reply_token, [{"type": "text", "text": ROLE_WELCOME_MESSAGES[role]}])
        else:
            line_reply(reply_token, [{
                "type": "text", "text": "クローザー・アポインター・管理者、どちらですか？ボタンから選んでください。",
                "quickReply": quick_reply(["クローザー", "アポインター", "管理者"]),
            }])
        return True

    return False


def handle_goal_request(event: dict, closer: dict):
    """リッチメニュー「今日の目標を見る」タップへの応答。
    Notion「📊 個人別KPI逆算データ（参照用）」の役割に応じた通知本文を、その場で
    Reply APIで返す（Push APIは使わない＝メッセージ配信数を消費しない）。
    """
    reply_token = event["replyToken"]
    member_name = closer.get("closer_name")
    now = datetime.now(JST)

    # 連打防止（LINEの再送・素早い連打対策）。last_goal_reply_atカラムが未追加の
    # 環境ではcloser.get()がNoneを返すだけで安全にスキップされる。
    last_at = closer.get("last_goal_reply_at")
    if last_at:
        try:
            if now - datetime.fromisoformat(last_at) < timedelta(seconds=5):
                return
        except ValueError:
            pass

    if not NOTION_TOKEN:
        line_reply(reply_token, [{
            "type": "text",
            "text": "目標配信の設定が完了していません（NOTION_TOKEN未設定）。今川さんに確認してください。",
        }])
        return

    today_iso = now.date().isoformat()

    # REPLY_COUNTS_TOWARD_QUOTA=true の場合のみ、1ユーザー1日1回に制限し
    # 2回目以降は直近の内容を再送する（既定はfalse。今のLINE仕様ではReply APIは
    # メッセージ配信数にカウントされないため、既定では毎回最新値を取り直す）。
    if REPLY_COUNTS_TOWARD_QUOTA and closer.get("last_goal_reply_date") == today_iso:
        cached = closer.get("last_goal_reply_text") or "本日分は送信済みです。"
        line_reply(reply_token, [{
            "type": "text",
            "text": f"{cached}\n（本日2回目以降のため、直近の内容を再送しています）",
        }])
        try:
            sb("PATCH", f"closer_line_users?id=eq.{closer['id']}", {"last_goal_reply_at": now.isoformat()})
        except urllib.error.HTTPError:
            pass  # last_goal_reply_at列未追加の環境では無視する
        return

    page = notion_query_member(member_name)
    if page is None:
        line_reply(reply_token, [{
            "type": "text",
            "text": f"「{member_name}」のKPIデータが見つかりませんでした。今川さんに確認してください。",
        }])
        return

    status = notion_rollup_status(page, "在籍状況(名簿参照)")
    if status and status != "稼働中":
        line_reply(reply_token, [{"type": "text", "text": "現在このアカウントへの配信対象外です。"}])
        return

    text = notion_formula_text(page, "通知本文（LINE配信用）")
    if not text:
        text = "本日分の目標データがまだ準備できていません。しばらくしてから再度お試しください。"

    # 「📉 営業部 実績DB」の直近実績行をまとめて取得し、⚔️営業ステータスと
    # 「前回共有した内容」の両方に使い回す（Notion APIの呼び出しを1回にまとめる）
    try:
        recent_rows = notion_query_recent_performance(member_name, today_iso)
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        recent_rows = []

    # 平日のみ：前回のLINE日報（/shoudannhoukoku等）の「備考」欄に書かれた共有事項・
    # 意識するポイントを、次にタップした時に再確認できるよう添える（ユーザー指示。
    # 「前日」固定ではなく、間が空いていても直近の報告内容をそのまま出す＝
    # 水曜にタップしたら日曜の内容が出る、等）
    remark_block = ""
    if now.weekday() < 5:
        remark = latest_remark_from_rows(recent_rows)
        if remark:
            remark_block = f"📝 前回共有した内容\n{remark}\n\n"

    # 平日のみ：📊進捗（契約はクーリングオフを「契約」に含めつつ「成約」で別出しする。
    # ユーザー指示）。「DB 商談分析＆アポ分析」から今月分を集計する
    progress_block = ""
    if now.weekday() < 5:
        role = closer.get("role") or ""
        role_prop = "クローザー" if role == "closer" else "アポインター"
        month_start_iso = now.replace(day=1).date().isoformat()
        try:
            month_deals = notion_query_month_deals(role_prop, member_name, month_start_iso)
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
            month_deals = []
        gross, net = count_contracts(month_deals)
        progress_block = build_progress_block(page, role, gross, net) + "\n\n"

    # メッセージの最上部に⚔️営業ステータス（ゲーム性、ユーザー指示）を置く
    status_block = build_status_block(page, recent_rows, closer.get("role") or "", now)
    text = status_block + "\n\n" + progress_block + remark_block + text

    line_reply(reply_token, [{"type": "text", "text": text}])

    update = {"last_goal_reply_at": now.isoformat()}
    if REPLY_COUNTS_TOWARD_QUOTA:
        update["last_goal_reply_date"] = today_iso
        update["last_goal_reply_text"] = text
    try:
        sb("PATCH", f"closer_line_users?id=eq.{closer['id']}", update)
    except urllib.error.HTTPError:
        pass  # last_goal_reply_*列未追加の環境では無視する（本文の返信自体は既に成功している）


def handle_analysis_request(event: dict, closer: dict):
    """リッチメニュー「直近の商談分析結果を見る」タップへの応答（クローザー専用）。
    Notion「DB 商談分析＆アポ分析」から自分の最新1件を検索し、Reply APIで返す。
    """
    reply_token = event["replyToken"]
    if closer.get("role") != "closer":
        # クローザー用メニューからしか出現しないボタンだが、念のためサーバー側でも守る
        line_reply(reply_token, [{"type": "text", "text": "この機能はクローザー限定です。"}])
        return

    if not NOTION_TOKEN:
        line_reply(reply_token, [{
            "type": "text",
            "text": "商談分析結果の照会設定が完了していません（NOTION_TOKEN未設定）。今川さんに確認してください。",
        }])
        return

    page = notion_query_latest_deal(closer.get("closer_name"))
    if page is None:
        line_reply(reply_token, [{"type": "text", "text": "まだ商談分析の記録が見つかりませんでした。"}])
        return

    customer = notion_title_text(page, "お客様名") or "（お客様名未設定）"
    result = notion_status_name(page, "結果") or "-"
    score = page.get("properties", {}).get("採点", {}).get("number")
    score_line = f"採点: {score}点" if score is not None else "採点: 未算出"
    text = f"📋 直近の商談分析結果\n\n{customer}（{result}）\n{score_line}\n\n{page.get('url', '')}"

    line_reply(reply_token, [{"type": "text", "text": text}])


def handle_event(event: dict):
    if event.get("type") != "message":
        return  # フォロー/アンフォロー等は今回は無視
    line_user_id = event.get("source", {}).get("userId")
    if not line_user_id:
        return  # グループ・複数人トークは対象外（1:1のみの運用）

    closer = find_closer(line_user_id)
    is_first_contact = closer is None
    if closer is None:
        closer = register_unknown_sender(line_user_id)
        # KNOWN_CLOSERS/KNOWN_APPOINTERSに一致していればここでcloser_name・roleが
        # 確定しているので、手動登録やあいさつフローを待たずにこのメッセージ自体
        # （録音も含む）をそのまま処理する

    # 自動一致登録（上記）の場合、あいさつフローを経由しないため本来の登録完了
    # メッセージが一度も送られない。無言のままだと本人・今川さんから見て「登録
    # できていない」ように見える（実際に田村さんで発生）ため、初回メッセージへの
    # 返信として登録完了メッセージを送る。
    welcome_messages = []
    if is_first_contact and closer.get("closer_name") and closer.get("role"):
        welcome_messages = [{"type": "text", "text": ROLE_WELCOME_MESSAGES[closer["role"]]}]

    if handle_registration(event, closer, is_first_contact):
        return

    message_type = event.get("message", {}).get("type")
    if message_type in ("audio", "file"):
        handle_audio_message(event, closer, extra_messages=welcome_messages)
    elif welcome_messages:
        line_reply(event["replyToken"], welcome_messages)
    elif message_type == "text" and event["message"]["text"] == GOAL_BUTTON_TEXT:
        # 録音登録フローの途中（awaiting_customer等）でタップされた場合も、
        # 目標確認を優先する（handle_text_messageの状態遷移より先に判定する）
        handle_goal_request(event, closer)
    elif message_type == "text" and event["message"]["text"] == ANALYSIS_BUTTON_TEXT:
        handle_analysis_request(event, closer)
    elif message_type == "text" and event["message"]["text"] == STATUS_DEFINITION_TEXT:
        handle_status_definition_request(event, closer)
    elif message_type == "text":
        handle_text_message(event)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        signature = self.headers.get("X-Line-Signature", "")

        if not verify_signature(body, signature):
            self.send_response(403)
            self.end_headers()
            return

        payload = json.loads(body)
        for event in payload.get("events", []):
            try:
                handle_event(event)
            except Exception as e:  # 1件の失敗で他のイベント処理を止めない
                print(f"event handling error: {e}")

        # LINEの再送ループを防ぐため、内部エラーがあっても200を返す
        self.send_response(200)
        self.end_headers()
