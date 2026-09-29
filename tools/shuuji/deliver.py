"""使い方: python deliver.py 出力フォルダ 公開ベースURL [--dry-run] [--managers-only] [--test-to <line_user_id>]
manifest.csv を読み、各代理店のリンクを営業分析BOT（公式LINE）からプッシュ送信する。
--managers-only を付けると各代理店の管理者(is_manager=yes)だけに送る。
--test-to <line_user_id> を付けると、本来の送り先の代わりに全員分を指定した1人（例: 今川さん）宛てに送る
（本文の宛名・代理店名はそのまま残すので、今川さんは「誰に何が届くはずだったか」を1人で全件確認できる。
本番配信前のテスト送信用。LINE配信キットのREADME.txt「初回セットアップ」5番の手動テストに相当）。
環境変数 DEAL_LINE_CHANNEL_ACCESS_TOKEN が必要(Messaging APIのチャネルアクセストークン。
line-webhook/README.mdと同じ変数名に統一している。元のキットでは LINE_CHANNEL_ACCESS_TOKEN)。

商談分析運用.md 8章参照（このリポジトリの元のスクリプトはLINE配信キット.zipを移植したもの。
判定・PDF生成ロジックは build_all.py / build_meeting.py 側にあり、ここは配信のみを担当する）。
"""
import csv, os, sys, time, json, urllib.request

out, base = sys.argv[1], sys.argv[2].rstrip("/")
dry = "--dry-run" in sys.argv
mgr = "--managers-only" in sys.argv
test_to = None
if "--test-to" in sys.argv:
    test_to = sys.argv[sys.argv.index("--test-to") + 1]

tok = os.environ.get("DEAL_LINE_CHANNEL_ACCESS_TOKEN", "")
if not dry and not tok:
    sys.exit("DEAL_LINE_CHANNEL_ACCESS_TOKEN が未設定です。送信していません。")

for r in csv.DictReader(open(f"{out}/manifest.csv", encoding="utf-8-sig")):
    if mgr and r["is_manager"] != "yes":
        continue
    text = f"{r['name']}さん\n今週の{r['agency']}のPDCAシートです。\n{base}/{r['file']}"
    to = test_to or r["line_user_id"]
    if test_to:
        text = f"【テスト送信・本来の宛先: {r['name']}さん（{r['agency']}）】\n{text}"
    if dry:
        print("[DRY]", r["name"], "->", to, f"{base}/{r['file']}", f"({r['agency']}・{r['sheets']}人分)")
        continue
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": to, "messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
    )
    print(r["name"], urllib.request.urlopen(req).status)
    time.sleep(0.3)
