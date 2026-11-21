#!/usr/bin/env python3
"""施工案件の更新を、1日分まとめて夜に今川さんへ確認する（施工担当へは、承認されたときだけ送る）。

  python3 scripts/construction_digest.py --group-only  # 施工グループの要点だけ（稼働日の毎晩20:30のRoutine。発言がない日は何も送らない）
  python3 scripts/construction_digest.py --dry-run     # まとめの文面だけ見る（誰にも送らない）
  python3 scripts/construction_digest.py               # 今川さんに「送る／見送り」の確認を送る

流れ（2026-11-10 今川さん指示）:
  1. 営業マンが契約後確認のボタンを押すと、line-webhook/api/webhook.py が更新を deal_recordings
     （storage_path＝construction_event, status＝event_pending）にためる。この時点では誰にも送らない
  2. このスクリプトが、ためた更新を1つのまとめにして、今川さんに確認のLINE（［施工担当へ送る］［見送り］）を送る
  3. 今川さんが［施工担当へ送る］を押したときだけ、bot が施工担当（role＝construction）の個別LINEへ、
     同じ内容を送る（3名とも同じ内容）。LINEグループへは送らない。［見送り］なら送らない

まとめの内容は、お客様名・営業担当・施工店・工事日・更新の種類だけ（住所・電話番号は載せない）。
同じ案件の更新が複数あるときは、いちばん新しいものだけ載せる。

環境変数: ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY, DEAL_LINE_CHANNEL_ACCESS_TOKEN
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import load_env  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
ADMIN_NAME = "今川"
SECTIONS = [("確定", "✅ 確定（契約後の確認が済んだ案件）"), ("中止", "⛔ 中止（クーリングオフ・審査落ち）"),
            ("延期", "📅 延期（新しい工事日が決まった）"), ("調整中", "⏳ 延期・お客様と調整中")]


def sb(method: str, path: str, data=None):
    base, key = os.environ["ELP_SUPABASE_URL"].rstrip("/"), os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/rest/v1/{path}", method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"apikey": key, "Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json", "Prefer": "return=representation"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def line_push(to: str, messages: list) -> int | str:
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": to, "messages": messages}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code} {e.read().decode()[:200]}"


def line_of(e: dict) -> str:
    kind = e["kind"]
    work = e.get("work") or "工事日未定"
    shop = f"｜{e['shop']}" if e.get("shop") else ""
    head = f"・{e['name']}｜営業：{e['closer']}{shop}"
    if kind == "確定":
        return f"{head}｜工事日：{work}"
    if kind == "中止":
        return f"{head}｜元の工事日：{work}｜理由：{e.get('detail') or '契約取り消し'}"
    if kind == "延期":
        return f"{head}｜新しい工事日：{e.get('detail') or '未定'}"
    return f"{head}｜元の工事日：{work}｜日程は調整中"


def build_text(events: list[dict], today: dt.date) -> str:
    lines = [f"【施工案件の更新まとめ {today.month}/{today.day}】", "営業マンから、契約後の確認で次の報告がありました。"]
    for kind, title in SECTIONS:
        items = [e for e in events if e["kind"] == kind]
        if items:
            lines += ["", title] + [line_of(e) for e in items]
    lines += ["", "工事日の調整・確定は、botを通して進めます。"]
    return "\n".join(lines)


RETENTION_DAYS = 45   # グループの発言の保存期間（今川さん了承 2026-11-21。30〜60日の中間）
MAX_BUTTONS = 13


def purge_old() -> None:
    """保存期間を過ぎた、グループの発言・反映の提案を消す。"""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RETENTION_DAYS)).isoformat()
    for sp in ("group_message", "group_proposal"):
        try:
            sb("DELETE", f"deal_recordings?storage_path=eq.{sp}&received_at=lt.{urllib.parse.quote(cutoff)}")
        except Exception as e:  # noqa: BLE001
            print(f"古い記録の削除に失敗（{sp}）: {e}")


def _propose_text(q: dict) -> str:
    bits = []
    ch = q.get("changes") or {}
    if ch.get("進捗"):
        bits.append(f"進捗→{ch['進捗']}")
    if ch.get("施工日時"):
        d = dt.datetime.fromisoformat(ch["施工日時"])
        bits.append(f"施工日時→{d.month}/{d.day} {d:%H:%M}")
    if ch.get("機器発送状況"):
        bits.append(f"機器発送→{ch['機器発送状況']}")
    if q.get("note"):
        bits.append("次の確認に追記")
    return "／".join(bits)


def group_summary(today: dt.date, dry: bool) -> None:
    """施工案件グループの発言を、案件ごとの要点にして、今川さんだけに知らせる（施工担当へは送らない）。
    発言の全文は送らない（保存期間内はSupabaseに残る）。Notionへの反映は、［反映］を押したときだけ（webhook.py の handle_group_proposal）。
    グループがG.LかAZかは、今川さんが「グループ登録 xxxx G.L」と教える（未登録のグループは末尾4文字で見分ける）。"""
    if not dry:
        purge_old()
    rows = sb("GET", "deal_recordings?storage_path=eq.group_message&status=eq.group_pending"
                     "&order=received_at.asc&select=id,customer_name") or []
    if not rows:
        return
    labels = {r["line_user_id"]: json.loads(r["customer_name"]).get("shop", "")
              for r in (sb("GET", "deal_recordings?storage_path=eq.group_label&select=line_user_id,customer_name") or [])}
    msgs = [json.loads(r["customer_name"]) for r in rows]
    by_group: dict[str, int] = {}
    for m in msgs:
        by_group[m.get("group", "")] = by_group.get(m.get("group", ""), 0) + 1
    import group_extract as GE
    try:
        props, other, newitems = GE.build_proposals(msgs, today, labels)
    except Exception as e:  # noqa: BLE001  失敗したら、発言は消さずに残して、次の実行でやり直す
        print(f"要点の作成に失敗しました（発言は残してあります。次の実行でやり直します）: {e}")
        return
    admin = sb("GET", f"closer_line_users?closer_name=eq.{urllib.parse.quote(ADMIN_NAME)}&select=line_user_id") or []
    admin_uid = admin[0]["line_user_id"] if admin else ""
    ids: list = []
    for q in props:
        if dry or not admin_uid:
            ids.append("?")
            continue
        made = sb("POST", "deal_recordings", {"line_user_id": admin_uid, "storage_path": "group_proposal", "status": "proposal_pending",
                                              "customer_name": json.dumps(q, ensure_ascii=False)})
        ids.append((made[0] if isinstance(made, list) else made)["id"])
    unlabeled = []
    counts: dict[str, int] = {}
    for gid, n in by_group.items():
        lab = GE.group_label(gid, labels)
        counts[lab] = counts.get(lab, 0) + n
        if not labels.get(gid):
            unlabeled.append(gid[-4:])
    # グループごとに「【G.L施工連絡】」「【AZ施工連絡】」の見出しをつけて、どの業者からの連絡か分かるようにする（今川さん指示 2026-11-21）
    def title(lab: str) -> str:
        if lab in ("G.L", "AZ"):
            return f"【{lab} 施工連絡】"
        return f"【未登録グループ（…{lab[-4:]}） 施工連絡】"
    order = sorted(counts, key=lambda l: (0 if l == "G.L" else 1 if l == "AZ" else 2, l))
    lines = [f"{today.month}/{today.day} 分（botは読むだけです）"]
    for lab in order:
        mine = [(q, i) for q, i in zip(props, ids) if (q.get("src") or "") == lab]
        newmine = [n for n in newitems if (n.get("src") or "") == lab]
        lines += ["", title(lab)]
        if not mine and not newmine:
            lines.append(f"案件に当てはまる発言はありませんでした（発言{counts[lab]}件）")
            continue
        for k, (q, i) in enumerate(mine, 1):
            lines += ["", f"{k}. {q['name']}：{q.get('summary') or q.get('note') or ''}"]
            if q.get("by"):
                lines.append(f"　連絡した人：{q['by']}")
            pt = _propose_text(q)
            if pt:
                lines.append(f"　反映案：{pt}（No.{i}）")
        if newmine:
            lines += ["", "▼新規の施工依頼"]
            for n in newmine:
                lines += ["", f"・{n['name']}：{n['summary']}"]
                if n.get("by"):
                    lines.append(f"　投げた人：{n['by']}")
        rest = counts[lab] - len(mine) - len(newmine)
        if rest > 0:
            lines.append(f"（案件に関係しない発言 {rest}件は記録のみ）")
    stray = [(q, i) for q, i in zip(props, ids) if (q.get("src") or "") not in counts]
    if stray:
        lines += ["", "【施工連絡】"]
        for k, (q, i) in enumerate(stray, 1):
            lines += ["", f"{k}. {q['name']}：{q.get('summary') or q.get('note') or ''}"]
            pt = _propose_text(q)
            if pt:
                lines.append(f"　反映案：{pt}（No.{i}）")
    if props:
        lines += ["", "押すまでNotionは変わりません。反映してよいものを選んでください。"]
    if unlabeled:
        lines += ["", "※ 未登録のグループは、下のボタンでG.LかAZかを教えてください。"]
    text = "\n".join(lines)[:4800]
    if dry:
        print(text)
        print(f"\n（発言{len(msgs)}件／提案{len(props)}件。--dry-run のため送っていません）")
        return
    if not admin_uid:
        return
    items = [{"type": "action", "action": {"type": "message", "label": f"…{c} は{shop}", "text": f"グループ登録 {c} {shop}"}}
             for c in unlabeled[:3] for shop in ("G.L", "AZ")]
    room = MAX_BUTTONS - len(items) - (2 if props else 0)
    for q, i in list(zip(props, ids))[:max(0, room)]:
        items.append({"type": "action", "action": {"type": "message", "label": f"反映 {q['name']}"[:20], "text": f"施工反映 {i}"}})
    if props:
        items += [{"type": "action", "action": {"type": "message", "label": "すべて反映", "text": "施工反映 all"}},
                  {"type": "action", "action": {"type": "message", "label": "すべて見送り", "text": "施工見送り all"}}]
    msg = {"type": "text", "text": text}
    if items:
        msg["quickReply"] = {"items": items}
    res = line_push(admin_uid, [msg])
    print("グループの要点を今川さんへ送りました:", res)
    if res == 200:
        sb("PATCH", f"deal_recordings?id=in.({','.join(str(r['id']) for r in rows)})", {"status": "group_digested"})


def is_workday(d: dt.date) -> bool:
    """水〜日と、祝日の月火（scripts/daily_mission.py と同じ）。"""
    if d.weekday() >= 2:
        return True
    try:
        import jpholiday
        return bool(jpholiday.is_holiday(d))
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--group-only", action="store_true",
                    help="施工グループの要点だけを送る（稼働日の毎晩20:30。発言がない日は何も送らない）")
    args = ap.parse_args()
    if args.group_only:
        load_env()
        today = dt.datetime.now(JST).date()
        if not is_workday(today):
            print(f"{today} は定休日（月火で祝日でない）なので送りません。発言は次の稼働日にまとめます。")
            return
        group_summary(today, args.dry_run)
        return
    if not args.dry_run:
        from quiet_hours import in_quiet_hours
        if in_quiet_hours():   # 質問・確認は0:00にまとめて送る（0:30〜6:00は送らない。今川さん指示 2026-11-13）
            print("0:30〜6:00は送らない設定のため、今回は送信を見送ります（次の0:00に送ります）。")
            return
    load_env()
    from quiet_hours import business_date
    today = business_date()   # 0:00に動くときは、いま終わった日を「その日」にする

    rows = sb("GET", "deal_recordings?storage_path=eq.construction_event&status=eq.event_pending"
                     "&order=received_at.asc&select=id,customer_name") or []
    if not rows:
        print("施工側へ伝える更新はありません。")
        return
    latest: dict[str, dict] = {}   # 同じ案件は新しい更新だけ載せる
    for r in rows:
        e = json.loads(r["customer_name"])
        e["_id"] = r["id"]
        latest[e["deal"]] = e
    events = list(latest.values())
    text = build_text(events, today)
    if args.dry_run:
        print(text)
        print(f"\n（更新{len(rows)}件／案件{len(events)}件。--dry-run のため送っていません）")
        return

    admin = sb("GET", f"closer_line_users?closer_name=eq.{urllib.parse.quote(ADMIN_NAME)}&select=line_user_id") or []
    if not admin:
        sys.exit("今川さんのLINE user IDが見つかりません。")
    all_ids = [r["id"] for r in rows]
    digest = sb("POST", "deal_recordings", {"line_user_id": admin[0]["line_user_id"], "storage_path": "construction_digest",
                                           "status": "awaiting_approval",
                                           "customer_name": json.dumps({"text": text, "events": all_ids}, ensure_ascii=False)})[0]
    sb("PATCH", f"deal_recordings?id=in.({','.join(str(i) for i in all_ids)})", {"status": "event_digested"})
    msg = [{"type": "text", "text": "🏗 施工担当への連絡まとめです。内容を確認して、送ってよければ［施工担当へ送る］を押してください。"
                                    "（押すまでは誰にも送りません）"},
           {"type": "text", "text": text, "quickReply": {"items": [
               {"type": "action", "action": {"type": "message", "label": "施工担当へ送る", "text": f"施工連絡 承認 {digest['id']}"}},
               {"type": "action", "action": {"type": "message", "label": "見送り", "text": f"施工連絡 見送り {digest['id']}"}}]}}]
    res = line_push(admin[0]["line_user_id"], msg)
    print("今川さんへ確認を送りました:", res, f"（案件{len(events)}件）")


if __name__ == "__main__":
    main()
