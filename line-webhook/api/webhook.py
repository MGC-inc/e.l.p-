"""LINE Webhook — 商談録音分析の受付（Vercel Python Function）

「ユメイク営業分析bot」宛てに送られた商談録音を受け取り、
アポインター→お客様名→結果→分析する/しない、の4問クイックリプライで
必要事項を確定させ、Supabase（deal_recordings・Storage）に記録する。

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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

LINE_CHANNEL_SECRET = os.environ["DEAL_LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]
SUPABASE_URL = os.environ["ELP_SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]

RESULT_OPTIONS = ["契約", "保留", "失注", "クーリングオフ", "審査落ち", "キャンセル"]
# アポインター（商談を設定した人。自分では録音を送らない役割）。
# 代理店別の現在の稼働メンバー（2026-09時点。今川さん確認済み）:
#   ピタサチ=藤江 / wanny=戸田・巻田・林 / TRYGROUP=安達・平井・山川 / VIZZ=田村
# 要: 今川さんが実際のメンバー構成に合わせて随時更新する（LINEのクイックリプライは最大13件まで）
APPOINTER_OPTIONS = ["藤江", "戸田", "巻田", "林", "安達", "平井", "山川", "田村"]
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
    LINE表示名がKNOWN_CLOSERSに一致すれば、closer_nameを即座に確定させる
    （今川さんの手動承認を待たずに以降のメッセージ・録音を処理できる）。
    """
    existing = sb("GET", f"closer_line_users?line_user_id=eq.{line_user_id}&select=*")
    if existing:
        return existing[0]
    profile = line_get_profile(line_user_id)
    display_name = profile.get("displayName", "")
    closer_name = KNOWN_CLOSERS.get(display_name.strip())
    row = {"line_user_id": line_user_id, "display_name": display_name, "closer_name": closer_name}
    created = sb("POST", "closer_line_users", [row])
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


# ---- イベント処理 ---------------------------------------------------------

def handle_audio_message(event: dict, closer: dict):
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

    line_reply(reply_token, [{
        "type": "text",
        "text": "録音を受け取りました。アポインターは誰ですか？",
        "quickReply": quick_reply(APPOINTER_OPTIONS),
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


def handle_event(event: dict):
    if event.get("type") != "message":
        return  # フォロー/アンフォロー等は今回は無視
    line_user_id = event.get("source", {}).get("userId")
    if not line_user_id:
        return  # グループ・複数人トークは対象外（1:1のみの運用）

    closer = find_closer(line_user_id)
    if closer is None:
        closer = register_unknown_sender(line_user_id)
        # KNOWN_CLOSERSに一致していればここで closer_name が確定しているので、
        # 手動登録を待たずにこのメッセージ自体（録音も含む）をそのまま処理する
    if closer.get("closer_name") is None:
        line_reply(event["replyToken"], [{"type": "text", "text": "担当者名が未登録です。今川さんに連絡してください。"}])
        return

    message_type = event.get("message", {}).get("type")
    if message_type in ("audio", "file"):
        handle_audio_message(event, closer)
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
