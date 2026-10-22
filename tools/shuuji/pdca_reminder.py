"""使い方: python pdca_reminder.py --stage 1|2|report [--dry-run]

今週の宿題（週次PDCA）をBOTに送っていない人へ、催促LINEを送る（商談分析運用.md 8章参照）。

■ 誰に送るか（判定はNotionの記録が正）
  Supabase closer_line_users.pdca_pending_week が立っている（配信済みで未返信の）人のうち、
  📝週次PDCA記録DBに「その週の宿題（アクションプラン）が入った行」が無い人。
  ・LINEで送った場合も、MTの議事録から反映した場合（pdca_apply.py）も、Notionに行ができるので同じ扱い
  ・pdca_pending_weekは再配信などで立ち直ることがあり、それだけで判定すると提出済みの人にも送ってしまう。
    Notionに記録がある人は、印（pdca_pending_week）も外す
  ・今川さん（管理者本人）と、週次の対象外の人（藤江）には送らない

■ いつ送るか（workflows/weekly-pdca-reminder.yml。送信は平日の日中だけ。月火は定休なので送らない）
  --stage 1       水 14:30 JST  MTのあと（BOT送信の締切11:15と、議事録の反映11:50のあと）。やさしく「届いていません」
  --stage 2       木 09:00 JST  木曜は「アポ中心」の日。今日の動きを決めるために、今日18時までに送ってもらう
  --stage report  木 18:00 JST  まだ出していない人を、代理店ごとに今川さんだけに知らせる（本人には送らない）
  催促は本人あてに最大2回まで。それでも出ない人は、リーダー／今川さんが声をかける（マニュアル/週次MT.md）

環境変数: DEAL_LINE_CHANNEL_ACCESS_TOKEN, ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY, NOTION_TOKEN,
IMAGAWA_LINE_USER_ID（reportの送り先）
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from monthly_budget_floor import BUDGET_DB, load_env, prop_text, query_all  # noqa: E402

PDCA_DB = "17f23c69-8a24-429a-8957-6b5490a87680"  # 📝 週次PDCA記録DB（data_source_id）
NO_REMINDER = {"今川", "藤江"}  # 今川＝管理者本人／藤江＝週次の対象外（tools/shuuji/notion_to_csv.py の WEEKLY_HIDDEN と同じ）

dry = "--dry-run" in sys.argv
stage = sys.argv[sys.argv.index("--stage") + 1] if "--stage" in sys.argv else "2"
if stage not in ("1", "2", "report"):
    sys.exit("--stage は 1 / 2 / report のどれか")


def sb(method: str, path: str, data: dict | None = None):
    url = os.environ["ELP_SUPABASE_URL"].rstrip("/") + f"/rest/v1/{path}"
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(
        url, method=method, data=json.dumps(data).encode() if data is not None else None,
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
        return json.loads(body) if body else None


def push(to: str, text: str) -> int | str:
    tok = os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": to, "messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code} {e.read().decode()[:200]}"


def week_label(week_start_iso: str) -> str:
    # week_start_isoは集計対象週（先週）の月曜。表示は宿題の対象＝その翌週（deliver.pyと同じ）
    start = datetime.date.fromisoformat(week_start_iso) + datetime.timedelta(days=7)
    end = start + datetime.timedelta(days=6)
    return f"{start.month}/{start.day}〜{end.month}/{end.day}"


def template(role: str) -> str:
    # deliver.pyのpdca_template()と同じ項目（webhook.pyのPDCA_TARGET_LABELSと一致させること）
    targets = ("訪問数：\nアポ数：\n商談数（自アポ）：\n商談数（他アポ）：\n契約数（自アポ）：\n契約数（他アポ）：\n"
               if role == "closer" else "訪問数：\nアポ数：\n商談数：\n契約数：\n")
    return ("①先週の振り返り・学び\n\n\n②今週の宿題を1つ（いつ・誰と・何を）\n\n\n③今週の目標（数字で。日曜までの分）\n" + targets)


def message(stage: str, name: str, role: str, label: str) -> str:
    if stage == "1":
        return (f"📝{name}さん、今週（{label}）の宿題がまだ届いていません。\n\n"
                "今朝の週次MTで決めた「宿題」と「数字の目標」を、下の形で書いて返信してください。2分で終わります。\n\n"
                + template(role))
    return (f"📝{name}さん、今週（{label}）の宿題がまだ届いていません。\n\n"
            "今日（木）は「アポ中心」の日です。今日の動きを決めるためにも、今日18時までに、下の形で返信してください。"
            "書き方に迷うときは、先週の数字を見て「いつ・誰と・何を」だけ決めればOKです。\n\n"
            + template(role))


def main() -> None:
    load_env()
    rows = sb("GET", "closer_line_users?select=closer_name,role,line_user_id,pdca_pending_week"
                     "&pdca_pending_week=not.is.null&closer_name=not.is.null&order=closer_name") or []
    rows = [r for r in rows if r["closer_name"] not in {"藤江"}]
    if not rows:
        print("配信済みで未返信の人は0人です。")
        return
    week = max(r["pdca_pending_week"] for r in rows)  # 今週の宿題の対象（deliver.pyが付けた週）
    rows = [r for r in rows if r["pdca_pending_week"] == week]

    recs = query_all(f"/data_sources/{PDCA_DB}/query", {"property": "週初日", "date": {"equals": week}}, "2025-09-03")
    submitted = {prop_text(r, "氏名") for r in recs
                 if "".join(t.get("plain_text", "") for t in r["properties"]["アクションプラン"]["rich_text"]).strip()}

    # Notionに宿題がある人は、再配信などで立ち直った「未返信」の印を外す
    for r in rows:
        if r["closer_name"] in submitted and not dry:
            sb("PATCH", f"closer_line_users?line_user_id=eq.{urllib.parse.quote(r['line_user_id'])}", {"pdca_pending_week": None})

    missing = [r for r in rows if r["closer_name"] not in submitted]
    label = week_label(week)
    print(f"対象週 {label}: 提出済み{len([r for r in rows if r['closer_name'] in submitted])}名／未提出{len(missing)}名")

    if stage == "report":
        if not missing:
            print("全員提出済みです（今川さんへの報告は不要）。")
            return
        agency = {}
        for b in query_all(f"/data_sources/{BUDGET_DB}/query",
                           {"property": "年月（YYYY-MM）", "rich_text": {"equals": datetime.date.today().strftime("%Y-%m")}}, "2025-09-03"):
            agency[prop_text(b, "メンバー")] = (b["properties"].get("代理店") or {}).get("select", {}).get("name") or "（所属なし）" \
                if (b["properties"].get("代理店") or {}).get("select") else "（所属なし）"
        by: dict[str, list[str]] = {}
        for r in missing:
            by.setdefault(agency.get(r["closer_name"], "（所属なし）"), []).append(r["closer_name"])
        text = (f"📝今週（{label}）の宿題、木曜18時時点の未提出は{len(missing)}名です。\n"
                + "\n".join(f"・{a}: {'、'.join(ns)}" for a, ns in by.items())
                + "\n\n本人への催促は済んでいます。各代理店のリーダーから声をかけてもらうか、1対1で確認してください。")
        print(text)
        to = os.environ.get("IMAGAWA_LINE_USER_ID")
        if dry or not to:
            return
        print("今川さんへ:", push(to, text))
        return

    targets = [r for r in missing if r["closer_name"] not in NO_REMINDER]
    for r in targets:
        text = message(stage, r["closer_name"], r["role"], label)
        if dry:
            print("[DRY]", r["closer_name"])
            continue
        print(r["closer_name"], push(r["line_user_id"], text))
    skipped = [r["closer_name"] for r in missing if r["closer_name"] in NO_REMINDER]
    if skipped:
        print("催促しない（管理者・対象外）:", "、".join(skipped))


if __name__ == "__main__":
    main()
