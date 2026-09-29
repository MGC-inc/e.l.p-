"""使い方: python deliver_meeting.py <meeting.pdfの公開URL> <今川さんのline_user_id> [--dry-run]

全社ミーティング画面共有用PDF（成果物B）を、今川さんのLINEにだけ1通push送信する
（依頼4章: 「送り先は今川さんのみ（LINE個別送信）」）。
環境変数 DEAL_LINE_CHANNEL_ACCESS_TOKEN が必要。
"""
import json
import os
import sys
import urllib.request

url, to = sys.argv[1], sys.argv[2]
dry = "--dry-run" in sys.argv

tok = os.environ.get("DEAL_LINE_CHANNEL_ACCESS_TOKEN", "")
if not dry and not tok:
    sys.exit("DEAL_LINE_CHANNEL_ACCESS_TOKEN が未設定です。送信していません。")

text = f"今週の全社ミーティング用資料です。\n{url}"

if dry:
    print("[DRY]", to, url)
    sys.exit(0)

req = urllib.request.Request(
    "https://api.line.me/v2/bot/message/push", method="POST",
    data=json.dumps({"to": to, "messages": [{"type": "text", "text": text}]}).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
)
print("今川", urllib.request.urlopen(req).status)
