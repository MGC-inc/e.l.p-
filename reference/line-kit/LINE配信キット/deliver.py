"""使い方: python deliver.py 出力フォルダ 公開ベースURL [--dry-run]
manifest.csv を読み、各代理店のリンクをLINE公式アカウントからプッシュ送信する。\n--managers-only を付けると各代理店の管理者(is_manager=yes)だけに送る。
環境変数 LINE_CHANNEL_ACCESS_TOKEN が必要(Messaging APIのチャネルアクセストークン)。"""
import csv, os, sys, time, json, urllib.request
out, base = sys.argv[1], sys.argv[2].rstrip("/"); dry = "--dry-run" in sys.argv; mgr = "--managers-only" in sys.argv
tok = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
if not dry and not tok: sys.exit("LINE_CHANNEL_ACCESS_TOKEN が未設定です。送信していません。")
for r in csv.DictReader(open(f"{out}/manifest.csv", encoding="utf-8-sig")):
    if mgr and r["is_manager"] != "yes": continue
    text = f"{r['name']}さん\n今週の{r['agency']}のPDCAシートです。\n{base}/{r['file']}"
    if dry: print("[DRY]", r["name"], r["line_user_id"], f"{base}/{r['file']}", f"({r['agency']}・{r['sheets']}人分)"); continue
    req = urllib.request.Request("https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": r["line_user_id"], "messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    print(r["name"], urllib.request.urlopen(req).status); time.sleep(0.3)
