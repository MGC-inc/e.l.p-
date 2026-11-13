"""使い方: python deliver_meeting.py <meeting.pdfの公開URL> <今川さんのline_user_id> [--slides <全社版スライドURL>]
                                      [--also-names 野間,…] [--dry-run]

全社ミーティング画面共有用PDF（成果物B）と全社版スライドを、今川さんのLINEに1通push送信する
（依頼4章: 「送り先は今川さんのみ（LINE個別送信）」）。
--also-names で、今川さんと同じ全社分を受け取る人を足せる（2026-11-10 野間さん＝NoMax代表も全社分を受け取る）。
名前はSupabase closer_line_users から探す（見つからない人は送らず、その旨を表示する）。
環境変数 DEAL_LINE_CHANNEL_ACCESS_TOKEN が必要（--also-names には ELP_SUPABASE_URL / ELP_SUPABASE_SERVICE_ROLE_KEY も）。
"""
import json
import os
import sys
import urllib.parse
import urllib.request

url, to = sys.argv[1], sys.argv[2]
dry = "--dry-run" in sys.argv

tok = os.environ.get("DEAL_LINE_CHANNEL_ACCESS_TOKEN", "")
if not dry and not tok:
    sys.exit("DEAL_LINE_CHANNEL_ACCESS_TOKEN が未設定です。送信していません。")

slides = sys.argv[sys.argv.index("--slides") + 1] if "--slides" in sys.argv else ""
text = f"今週の全社ミーティング用資料です。\n{url}"
if slides:
    # build_slides.pyの全社版（全員分の週次MTスライド。リーダー会議用）
    text += f"\n\n▼週次MTスライド（全社版）\n{slides}"

if dry:
    print("[DRY]", to, url, "also:", sys.argv[sys.argv.index("--also-names") + 1] if "--also-names" in sys.argv else "-")
    sys.exit(0)

def push(uid: str) -> int:
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": uid, "messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
    )
    return urllib.request.urlopen(req).status


print("今川", push(to))
if "--also-names" in sys.argv:
    base, key = os.environ["ELP_SUPABASE_URL"].rstrip("/"), os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    for name in [x.strip() for x in sys.argv[sys.argv.index("--also-names") + 1].split(",") if x.strip()]:
        q = urllib.request.Request(f"{base}/rest/v1/closer_line_users?closer_name=eq.{urllib.parse.quote(name)}&select=line_user_id",
                                   headers={"apikey": key, "Authorization": f"Bearer {key}"})
        rows = json.loads(urllib.request.urlopen(q, timeout=20).read())
        if not rows:
            print(f"{name}: LINE未登録のため送りません")
            continue
        print(name, push(rows[0]["line_user_id"]))
