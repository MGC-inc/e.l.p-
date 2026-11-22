#!/usr/bin/env python3
"""アポ報告・商談報告のグループの発言を、毎晩22:00に整理して、今川さんだけに「確認用」として送る。2026-11-22 今川さん指示。

  python3 scripts/report_digest.py --dry-run         # 文面だけ見る（送らない・記録も変えない）
  python3 scripts/report_digest.py                   # 今川さんのBotへ送る（発言がない日は何も送らない）
  python3 scripts/report_digest.py --input t.json     # テスト用：発言のJSON（{group,name,text,ts}の配列）を読む（DBは使わない）

段階（2026-11-22）:
  1段階目（いま）: 記録→整理→今川さんへ確認用の送信まで。Notionには反映しない（今の /apohoukoku・/shoudannhoukoku と並行運用して見比べる）。
  2段階目: 確認のうえ［反映］を押したものだけ、実績DB・商談分析DB・学び共有ログDBへ登録する。

読み取りの決まり:
  ・アポ報告: 「〇〇邸💧N」の行をアポ（Nはその人のその日の累計）。時刻も控える。日報の「アポ数」と突き合わせる。声かけ・スタンプは無視。
  ・商談報告: 報告を送った人がクローザー。結果が分からないもの（入りますだけ、「クーリングオフ再訪」など）は要確認。
グループの種類は、今川さんが「グループ登録 xxxx アポ報告」「グループ登録 xxxx 商談報告」と教える。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import construction_digest as CD  # noqa: E402
import report_parse as RP  # noqa: E402

LABEL_APO, LABEL_SHODAN = "アポ報告", "商談報告"
REPORT_LABELS = (LABEL_APO, LABEL_SHODAN)

SHODAN_PROMPT = """あなたは、訪問営業会社の商談報告の読み取り補助です。商談報告のLINEグループの発言（日時・発言者・本文）を渡します。
商談1件ごとに、次のJSON（配列）だけを出力してください（前後に文章やコードブロックは付けない）。

[
  {{
    "customer": "お客様名（例: 石川邸）。同じ名前が複数あるときは「石川邸（安城市）」のように市区町村を付ける",
    "area": "市区町村",
    "time": "商談の時刻（例: 9時）。なければ空",
    "closer": "報告を送った人の登録名（発言者欄の（）の中の名前。クローザーはこの人）",
    "appointer": "「〇〇アポ」「〇〇ハウクリアポ」の〇〇。なければ空",
    "appo_type": "ハウクリ／メンテアポ／追い焚き配管洗浄アポ／商談のみアポ／不明 のどれか。「ハウクリ」の記載があればハウクリ",
    "result": "契約／保留／失注／クーリングオフ／審査落ち／AC／要確認 のどれか。「CLO」「CL」は失注。結果が書かれていない（入りますだけ）ものは「結果なし」。「クーリングオフ再訪」のように意味が一意でないものは「要確認」",
    "amount_man": "契約のとき、税込の金額（万円・数値）。「150万＋税」は165、「100万＋税」は110、「150万(税込)」は150。契約でなければ null",
    "work_date": "契約のとき、工事予定日（例: 11/30 9:00〜）。なければ空",
    "summary": "お客様の状況・理由を1〜2文（60字以内）。保留・失注は理由、契約は決め手。住所・電話番号は書かない",
    "note": "確認が必要な点（なければ空）"
  }}
]

守ること:
- 「入ります」の行と、結果報告の行は、同じ商談としてまとめて1件にする（お客様名・時刻・担当アポで対応づける）。
- 「入ります」だけで結果がまだの商談は result を「結果なし」にする。
- 声かけ・お祝い・お礼のやりとりは出さない。発言にないことは足さない。
- 同じ商談で結果が更新されているときは、最新の結果を使う。

今日の日付: {today}

【発言】
{messages}
"""


def label_of(gid: str, labels: dict[str, str]) -> str:
    return labels.get(gid, "")


def build_shodan(msgs: list[dict], today: dt.date) -> list[dict]:
    import group_extract as GE
    lines = []
    for m in sorted(msgs, key=lambda x: x.get("ts") or 0):
        name, st = RP.member(m.get("name", ""))
        if st == "skip":
            continue
        who = f"{m.get('name') or '不明'}（{name or '不明'}）"
        lines.append(f"{RP.hhmm(m.get('ts'))}｜{who}｜{GE.scrub((m.get('text') or '').replace(chr(10), ' / '))[:500]}")
    if not lines:
        return []
    raw = GE.gemini_json(SHODAN_PROMPT.format(today=today.isoformat(), messages="\n".join(lines)))
    return raw if isinstance(raw, list) else (raw.get("deals") or [])


def apo_section(apo: dict, day: dt.date) -> list[str]:
    rows = [(k[0], v) for k, v in apo.items() if k[1] == day]
    if not rows:
        return []
    rows.sort(key=lambda kv: -max(kv[1]["max"], len(kv[1]["lines"])))
    total = sum(max(v["max"], len(v["lines"])) for _, v in rows if not str(_).startswith("?"))
    out = [f"【アポ報告】合計 {total}件"]
    warn = []
    for name, v in rows:
        n = max(v["max"], len(v["lines"]))
        nip = v["nippo"]
        if str(name).startswith("?"):
            warn.append(f"表示名「{name[1:]}」が変換表にありません（アポ{n}件）")
            continue
        if nip:
            rep = nip.get("アポ数")
            mark = "✓" if rep == n else f"⚠日報{rep}件"
            if rep != n:
                warn.append(f"{name}：💧の最大{v['max']}／行数{len(v['lines'])}／日報{rep}")
            out.append(f"・{name} {n}件（日報あり{mark}）")
        else:
            out.append(f"・{name} {n}件（日報なし）")
            warn.append(f"{name}：日報がまだありません")
    for name, v in rows:
        if v["nippo"] and not v["lines"] and not str(name).startswith("?"):
            out.append(f"・{name} 0件（日報のみ・アポ数{v['nippo'].get('アポ数', '?')}）")
    if warn:
        out += ["", "確認:"] + [f"・{w}" for w in warn]
    return out


def shodan_section(deals: list[dict]) -> list[str]:
    if not deals:
        return []
    order = {"契約": 0, "保留": 1, "失注": 2, "クーリングオフ": 3, "審査落ち": 3, "AC": 4, "要確認": 5, "結果なし": 6}
    deals = sorted(deals, key=lambda d: order.get(d.get("result"), 9))
    cnt = {}
    for d in deals:
        cnt[d.get("result", "?")] = cnt.get(d.get("result", "?"), 0) + 1
    out = ["【商談報告】" + "／".join(f"{k}{v}" for k, v in cnt.items())]
    for d in deals:
        head = f"・{d.get('customer', '?')} {d.get('result', '?')}"
        if d.get("result") == "契約" and d.get("amount_man"):
            amt = d["amount_man"]
            head += f" {int(amt) if float(amt) == int(float(amt)) else amt}万(税込)"
        out.append("")
        out.append(head)
        out.append(f"　クローザー：{d.get('closer') or '?'}／アポ：{d.get('appointer') or '不明'}／{d.get('appo_type') or '不明'}")
        if d.get("work_date"):
            out.append(f"　工事予定：{d['work_date']}")
        if d.get("summary"):
            out.append(f"　{d['summary']}")
        if d.get("note"):
            out.append(f"　⚠ {d['note']}")
        elif d.get("result") in ("要確認", "結果なし"):
            out.append("　⚠ 結果が読み取れません。確認してください")
    return out


def build_text(day: dt.date, apo_msgs: list[dict], shodan_msgs: list[dict]) -> str:
    apo = RP.parse_apo_reports(apo_msgs)
    deals = build_shodan(shodan_msgs, day)
    lines = [f"{day.month}/{day.day} アポ・商談報告の整理（確認用）", "※ まだNotionには反映していません。いつもの /コマンド と見比べてください。"]
    a, s = apo_section(apo, day), shodan_section(deals)
    if a:
        lines += ["", *a]
    if s:
        lines += ["", *s]
    refl = [(k[0], v["reflect"]) for k, v in apo.items() if k[1] == day and v["reflect"] and not str(k[0]).startswith("?")]
    if refl:
        lines += ["", f"【日報の反省文】{len(refl)}人分（学び共有ログへの登録は次の段階で行います）"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--input")
    args = ap.parse_args()
    CD.load_env()
    today = dt.datetime.now(CD.JST).date()
    rows, labels = [], {}
    if args.input:
        data = json.load(open(args.input, encoding="utf-8"))
        msgs = data
        labels = {m["group"]: m.get("label", "") for m in data}
        rows = [{"id": "t"} for _ in msgs]
    else:
        rows = CD.sb("GET", "deal_recordings?storage_path=eq.group_message&status=eq.group_pending&order=received_at.asc&select=id,customer_name") or []
        labels = {r["line_user_id"]: json.loads(r["customer_name"]).get("shop", "")
                  for r in (CD.sb("GET", "deal_recordings?storage_path=eq.group_label&select=line_user_id,customer_name") or [])}
        msgs = [json.loads(r["customer_name"]) for r in rows]
    pairs = [(r, m) for r, m in zip(rows, msgs) if label_of(m.get("group", ""), labels) in REPORT_LABELS]
    if not pairs:
        print("アポ報告・商談報告の新しい発言はありません。送りません。")
        return
    apo_msgs = [m for _, m in pairs if label_of(m.get("group", ""), labels) == LABEL_APO]
    shodan_msgs = [m for _, m in pairs if label_of(m.get("group", ""), labels) == LABEL_SHODAN]
    days = sorted({RP.jst_date(m["ts"]) for _, m in pairs if m.get("ts")}) or [today]
    texts = []
    for d in days:
        am = [m for m in apo_msgs if m.get("ts") and RP.jst_date(m["ts"]) == d]
        sm = [m for m in shodan_msgs if m.get("ts") and RP.jst_date(m["ts"]) == d]
        try:
            texts.append(build_text(d, am, sm))
        except Exception as e:  # noqa: BLE001  失敗したら発言は消さずに残して、次の実行でやり直す
            print(f"整理に失敗しました（発言は残してあります。次の実行でやり直します）: {e}")
            return
    if args.dry_run or args.input:
        print("\n\n=====\n\n".join(texts))
        print(f"\n（アポ報告{len(apo_msgs)}件・商談報告{len(shodan_msgs)}件。送っていません）")
        return
    admin = CD.sb("GET", f"closer_line_users?closer_name=eq.{CD.urllib.parse.quote(CD.ADMIN_NAME)}&select=line_user_id") or []
    if not admin:
        print("今川さんのLINEが見つかりません")
        return
    uid = admin[0]["line_user_id"]
    ok = True
    for t in texts:
        # LINEの1通は5000字まで。長いときは見出しごとに分ける
        chunks, cur = [], ""
        for block in t.split("\n\n"):
            if len(cur) + len(block) + 2 > 4500 and cur:
                chunks.append(cur)
                cur = ""
            cur += ("\n\n" if cur else "") + block
        chunks.append(cur)
        for c in chunks:
            res = CD.line_push(uid, [{"type": "text", "text": c}])
            print("今川さんへ送りました:", res)
            ok = ok and res == 200
    if ok:
        ids = ",".join(str(r["id"]) for r, _ in pairs)
        CD.sb("PATCH", f"deal_recordings?id=in.({ids})", {"status": "report_previewed"})


if __name__ == "__main__":
    main()
