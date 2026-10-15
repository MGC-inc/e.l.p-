"""使い方: python pdca_reminder.py [--dry-run]

週次PDCA（tools/shuuji/deliver.py配信）に未回答のまま数日経った人へ、記入テンプレの
リマインドを1通LINEで送る（商談分析運用.md 8章参照。15人中半数以上が無回答のまま、という
実態が判明したため追加）。

判定方法: Supabase closer_line_users.pdca_pending_week は、deliver.py配信時に対象週の
月曜日がセットされ、本人が返信すると line-webhook（handle_pdca_reply）がNULLに戻す
（line-webhook/api/webhook.py参照）。つまり「pdca_pending_weekが今も立っている」人は
まだ返信していない人。Notion側を見に行く必要はない。

同じ週に何度もリマインドしないよう、pdca_reminded_weekにリマインド済みの週を記録する
（pdca_pending_weekと同じ値ならスキップ）。

毎週土曜9:00 JST（配信の3日後）に実行する想定（.github/workflows/weekly-pdca-reminder.yml）。

環境変数: DEAL_LINE_CHANNEL_ACCESS_TOKEN, ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY,
IMAGAWA_LINE_USER_ID（任意。管理者サマリー送信用。未設定ならサマリー送信をスキップする）
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

dry = "--dry-run" in sys.argv


def sb_get(path: str) -> list[dict]:
    url = os.environ["ELP_SUPABASE_URL"].rstrip("/") + f"/rest/v1/{path}"
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(url, headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def sb_patch(path: str, data: dict) -> None:
    url = os.environ["ELP_SUPABASE_URL"].rstrip("/") + f"/rest/v1/{path}"
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(), method="PATCH",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20):
        pass


def week_label(week_start_iso: str) -> str:
    start = datetime.date.fromisoformat(week_start_iso)
    end = start + datetime.timedelta(days=6)
    return f"{start.month}/{start.day}〜{end.month}/{end.day}"


def pdca_template(name: str, label: str, role: str) -> str:
    # deliver.pyのpdca_template()と文言・ラベルを揃える（webhook.pyのPDCA_TARGET_LABELSと一致させること）
    if role == "closer":
        target_lines = (
            "訪問数：\n"
            "アポ数：\n"
            "商談数（自アポ）：\n"
            "商談数（他アポ）：\n"
            "契約数（自アポ）：\n"
            "契約数（他アポ）：\n"
        )
    else:
        target_lines = (
            "訪問数：\n"
            "アポ数：\n"
            "商談数：\n"
            "契約数：\n"
        )
    return (
        f"⏰週次PDCA未回答｜{name}さん（今週：{label}）\n\n"
        "まだ今週のPDCA記入が届いていません。お手すきにこのまま3つに答えて返信してください。\n\n"
        "①なぜ低いと思いますか？（原因）\n\n\n"
        "②良くするために、今週何をしますか？（いつ・誰と・何を）\n\n\n"
        "③今週の目標（数字で）\n" + target_lines
    )


def push(to: str, text: str) -> int | str:
    tok = os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": to, "messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code} {e.read().decode()[:200]}"


def main() -> None:
    try:
        rows = sb_get(
            "closer_line_users?select=closer_name,role,line_user_id,pdca_pending_week,pdca_reminded_week"
            "&pdca_pending_week=not.is.null"
        )
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
        # pdca_reminded_week列がまだ追加されていない環境（schema.sqlのalter table未実行）。
        # 列が無い前提で続行する（＝今週すでにリマインド済みかどうかは判定できないが、
        # 対象週が変わる水曜にはpdca_pending_week自体がリセットされるので実害は小さい）
        print("[警告] pdca_reminded_week列が見つかりません。schema.sqlのalter tableを"
              "実行してください。今回は重複送信防止なしで続行します。", file=sys.stderr)
        rows = sb_get(
            "closer_line_users?select=closer_name,role,line_user_id,pdca_pending_week"
            "&pdca_pending_week=not.is.null"
        )
        for r in rows:
            r["pdca_reminded_week"] = None

    targets = [r for r in rows if r.get("pdca_reminded_week") != r.get("pdca_pending_week")]

    if not targets:
        print("未回答者は0人です（リマインド不要）。")
        return

    reminded = []
    for r in targets:
        name, role, uid, week = r["closer_name"], r["role"], r["line_user_id"], r["pdca_pending_week"]
        text = pdca_template(name, week_label(week), role)
        if dry:
            print("[DRY]", name, "->", uid)
            continue
        status = push(uid, text)
        print(name, status)
        if status == 200:
            reminded.append(name)
            try:
                sb_patch(f"closer_line_users?line_user_id=eq.{uid}", {"pdca_reminded_week": week})
            except urllib.error.HTTPError as e:
                print(f"  [警告] {name}のpdca_reminded_week更新に失敗: {e.code} "
                      f"{e.read().decode()[:200]}（schema.sqlのalter tableが未実行の可能性）")

    if dry or not reminded:
        return

    imagawa_id = os.environ.get("IMAGAWA_LINE_USER_ID")
    if imagawa_id:
        summary = f"📝週次PDCA未回答リマインド: {', '.join(reminded)}（計{len(reminded)}名）に送信しました。"
        push(imagawa_id, summary)


if __name__ == "__main__":
    main()
