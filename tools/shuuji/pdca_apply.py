#!/usr/bin/env python3
"""週次MTの議事録から抽出した内容を、📝週次PDCA記録DBに確定事項として反映する
（スキル `週次MT議事録反映` から呼ぶ。議事録の読み取り・抽出はスキル側＝Claudeが行い、
DBの突き合わせと書き込みはこの決定的コードが行う）。

使い方:
  python3 tools/shuuji/pdca_apply.py 抽出結果.json [--dry-run] [--notify]

抽出結果.json の形:
  {"mt_date": "2026-10-21", "agency": "wanny", "minutes_url": "https://app.notion.com/p/...",
   "members": [{"name": "林", "evaluation": "◎", "evaluation_note": "木金にロープレ実施",
                "learning": "...", "action": "...", "targets": {"訪問数目標": 60, "アポ数目標": 6},
                "note": "..."}]}
  evaluation/learning/action/targets/note は議事録で分からなければ省略する（省略した項目は触らない）。

週の対応（水曜MT。tools/shuuji/deliver.pyのpdca_pending_weekと同じキー）:
  今週の記録 = 週初日がMTの前週の月曜（LINEでMTの最後に送った行）
  評価する記録 = その1週前の月曜（先週のMTで決めた宿題の行）

反映ルール:
  - 先週の宿題の行に MT評価（◎○×）と MTメモ を書く
  - 今週の行が無い（LINE未送信）→ 議事録の内容で行を作る（記録元=議事録）
  - 今週の行がある → 議事録で言った内容と違う項目だけ議事録の内容で上書きし、
    記録元=LINE＋議事録で修正。LINEの原文は「元メッセージ」に残っている
  - 一致していれば記録元=LINE のまま。どの場合も議事録URLを付ける
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
from monthly_budget_floor import load_env, notify_admin, notion, prop_num, query_all  # noqa: E402

PDCA_DB = "17f23c69-8a24-429a-8957-6b5490a87680"  # 📝 週次PDCA記録DB（data_source_id）
TARGET_PROPS = ["訪問数目標", "アポ数目標", "商談数目標_自アポ", "商談数目標_他アポ", "契約数目標_自アポ", "契約数目標_他アポ"]
EVALS = {"◎", "○", "×"}


def rich(page: dict, name: str) -> str:
    return "".join(t.get("plain_text", "") for t in page["properties"].get(name, {}).get("rich_text", []))


def rt(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text[:2000]}}]}


def find_row(name: str, week: dt.date) -> dict | None:
    rows = query_all(f"/data_sources/{PDCA_DB}/query", {"and": [
        {"property": "氏名", "select": {"equals": name}},
        {"property": "週初日", "date": {"equals": week.isoformat()}},
    ]}, "2025-09-03")
    return rows[0] if rows else None


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry, notify = "--dry-run" in sys.argv, "--notify" in sys.argv
    load_env()
    data = json.load(open(args[0], encoding="utf-8"))
    mt = dt.date.fromisoformat(data["mt_date"])
    cur_week = mt - dt.timedelta(days=mt.weekday() + 7)   # MTの前週の月曜
    prev_week = cur_week - dt.timedelta(days=7)
    url = data.get("minutes_url") or None

    report = [f"📝 週次MT議事録の反映（{data.get('agency', '')}・{mt.month}/{mt.day}）"]
    for m in data["members"]:
        name = m["name"]
        lines = []
        # 1) 先週の宿題の評価
        ev = m.get("evaluation")
        if ev in EVALS:
            prev = find_row(name, prev_week)
            if prev:
                props = {"MT評価": {"select": {"name": ev}}}
                if m.get("evaluation_note"):
                    props["MTメモ"] = rt(m["evaluation_note"])
                if url:
                    props["議事録URL"] = {"url": url}
                if not dry:
                    notion("PATCH", f"/pages/{prev['id']}", {"properties": props})
                lines.append(f"先週の宿題 {ev}")
            else:
                lines.append(f"先週の宿題 {ev}（先週の記録が無いため保存先なし）")
        # 2) 今週の宿題・目標
        want: dict = {}
        if m.get("learning"):
            want["原因"] = m["learning"]
        if m.get("action"):
            want["アクションプラン"] = m["action"]
        for k, v in (m.get("targets") or {}).items():
            if k in TARGET_PROPS and v is not None:
                want[k] = int(v)
        if not want and not m.get("note"):
            report.append(f"・{name}: " + ("／".join(lines) or "議事録に記載なし"))
            continue
        cur = find_row(name, cur_week)
        if cur is None:
            props = {"タイトル": {"title": [{"text": {"content": f"{name}_{cur_week.isoformat()}"}}]},
                     "氏名": {"select": {"name": name}}, "週初日": {"date": {"start": cur_week.isoformat()}},
                     "元メッセージ": rt("（LINE未送信のため議事録から作成）"), "記録元": {"select": {"name": "議事録"}}}
            for k, v in want.items():
                props[k] = {"number": v} if k in TARGET_PROPS else rt(v)
            if m.get("note"):
                props["MTメモ"] = rt(m["note"])
            if url:
                props["議事録URL"] = {"url": url}
            if not dry:
                notion("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": PDCA_DB}, "properties": props})
            lines.append("LINE未送信 → 議事録から今週の記録を作成")
        else:
            changed, props = [], {}
            for k, v in want.items():
                if k in TARGET_PROPS:
                    old = prop_num(cur, k)
                    if old != v:
                        changed.append(f"{k.replace('目標', '')} {old if old is not None else '空'}→{v}")
                        props[k] = {"number": v}
                elif rich(cur, k).strip() != v.strip():
                    label = "学び" if k == "原因" else "宿題"
                    changed.append(f"{label}を議事録の内容に修正")
                    props[k] = rt(v)
            props["記録元"] = {"select": {"name": "LINE＋議事録で修正" if changed else "LINE"}}
            if m.get("note"):
                props["MTメモ"] = rt(m["note"])
            if url:
                props["議事録URL"] = {"url": url}
            if not dry:
                notion("PATCH", f"/pages/{cur['id']}", {"properties": props})
            lines.append(("修正: " + "、".join(changed)) if changed else "LINEの内容と一致")
        report.append(f"・{name}: " + "／".join(lines))

    text = "\n".join(report)
    print(text)
    if notify and not dry:
        notify_admin(text)


if __name__ == "__main__":
    main()
