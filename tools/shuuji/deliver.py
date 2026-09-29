"""使い方: python deliver.py 出力フォルダ 公開ベースURL [--dry-run] [--managers-only] [--test-to <line_user_id>]
manifest.csv を読み、各代理店のリンクと週次PDCAの記入テンプレを、営業分析BOT（公式LINE）から
1人1回のプッシュ（2メッセージ）で送る。
--managers-only を付けると各代理店の管理者(is_manager=yes)だけに送る。
--test-to <line_user_id> を付けると、本来の送り先の代わりに全員分を指定した1人（例: 今川さん）宛てに送る
（本文の宛名・代理店名はそのまま残すので、今川さんは「誰に何が届くはずだったか」を1人で全件確認できる。
本番配信前のテスト送信用。LINE配信キットのREADME.txt「初回セットアップ」5番の手動テストに相当）。
テストモード（--test-to）では、本人がまだ何も受け取っていないため、Supabaseの
pdca_pending_weekは更新しない（本番配信時のみ、実際の送り先に対して更新する）。

環境変数 DEAL_LINE_CHANNEL_ACCESS_TOKEN が必要(Messaging APIのチャネルアクセストークン。
line-webhook/README.mdと同じ変数名に統一している。元のキットでは LINE_CHANNEL_ACCESS_TOKEN)。
--test-toを使わない場合、環境変数 ELP_SUPABASE_URL / ELP_SUPABASE_SERVICE_ROLE_KEY も必要
（pdca_pending_weekの更新用。line-webhook/supabase/schema.sqlのalter tableが未実行だと
このPATCHはSupabase側で列が存在せずエラーになるので、その場合は事前に実行しておく）。

商談分析運用.md 8章参照（このリポジトリの元のスクリプトはLINE配信キット.zipを移植したもの。
判定・PDF生成ロジックは build_all.py / build_meeting.py 側にあり、ここは配信のみを担当する）。
"""
import csv
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

out, base = sys.argv[1], sys.argv[2].rstrip("/")
dry = "--dry-run" in sys.argv
mgr = "--managers-only" in sys.argv
test_to = None
if "--test-to" in sys.argv:
    test_to = sys.argv[sys.argv.index("--test-to") + 1]

tok = os.environ.get("DEAL_LINE_CHANNEL_ACCESS_TOKEN", "")
if not dry and not tok:
    sys.exit("DEAL_LINE_CHANNEL_ACCESS_TOKEN が未設定です。送信していません。")


def week_start_and_label(out_dir: str) -> tuple[str, str]:
    """config.csvのdata_week_startから、週初日(ISO)と表示ラベル(M/D〜M/D)を作る。"""
    cfg = {row["key"]: row["value"] for row in csv.DictReader(open(f"{out_dir}/config.csv", encoding="utf-8-sig"))}
    start = datetime.date.fromisoformat(cfg["data_week_start"])
    end = start + datetime.timedelta(days=6)
    label = f"{start.month}/{start.day}〜{end.month}/{end.day}"
    return start.isoformat(), label


def pdca_template(name: str, week_label: str) -> str:
    return (
        f"📝週次PDCA｜{name}さん（今週：{week_label}）\n\n"
        "PDFの「最優先の改善項目」を見て、下の2つに答えて、このまま返信してください。\n\n"
        "①なぜ低いと思いますか？（原因）\n\n\n"
        "②良くするために、今週何をしますか？（いつ・誰と・何を）\n"
    )


def sb_patch(path: str, data: dict) -> None:
    url = os.environ["ELP_SUPABASE_URL"].rstrip("/") + f"/rest/v1/{path}"
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(), method="PATCH",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20):
        pass


week_start_iso, week_label = week_start_and_label(out)

for r in csv.DictReader(open(f"{out}/manifest.csv", encoding="utf-8-sig")):
    if mgr and r["is_manager"] != "yes":
        continue
    pdf_text = f"{r['name']}さん\n今週の{r['agency']}のPDCAシートです。\n{base}/{r['file']}"
    template_text = pdca_template(r["name"], week_label)
    to = test_to or r["line_user_id"]
    if test_to:
        pdf_text = f"【テスト送信・本来の宛先: {r['name']}さん（{r['agency']}）】\n{pdf_text}"

    if dry:
        print("[DRY]", r["name"], "->", to, f"{base}/{r['file']}", f"({r['agency']}・{r['sheets']}人分)")
        continue

    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": to, "messages": [
            {"type": "text", "text": pdf_text},
            {"type": "text", "text": template_text},
        ]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
    )
    print(r["name"], urllib.request.urlopen(req).status)

    if not test_to:
        try:
            sb_patch(f"closer_line_users?line_user_id=eq.{r['line_user_id']}",
                     {"pdca_pending_week": week_start_iso})
        except urllib.error.HTTPError as e:
            print(f"  [警告] {r['name']}のpdca_pending_week更新に失敗: {e.code} "
                  f"{e.read().decode()[:200]}（schema.sqlのalter tableが未実行の可能性）")
    time.sleep(0.3)
