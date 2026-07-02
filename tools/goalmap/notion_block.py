#!/usr/bin/env python3
"""members/<名前>.json から、Notionゴールページに貼る Notion-flavored Markdown を出力する。

出力内容:
  - callout（ゴール・達成率・現在ステージ・今週のフォーカス）
  - mermaid フローチャート（下から上へ登る①〜⑤＋ゴールの図解）
  - ステージ別タスクのチェックリスト

使い方:
    python tools/goalmap/notion_block.py tools/goalmap/members/岡野.json
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from generate_goalmap import load_member_data  # noqa: E402

PREFIX = ["①", "②", "③", "④", "⑤"]


def rate(d) -> int:
    ts = [t for p in d["phases"] for t in p.get("tasks", [])]
    done = sum(1 for t in ts if t.get("done"))
    return round(done / len(ts) * 100) if ts else 0


def build(d) -> str:
    cur = int(d.get("currentStage", 1))
    today = dt.date.today().isoformat()
    head = d.get("name", "")
    if d.get("note"):
        head += f"（{d['note']}）"

    out = []
    out.append(f'## 🧭 ゴールマップ｜{head} {{color="purple"}}')
    # callout（1行＋<br>でインデント不要）
    cal = [f'**ゴール**：{d.get("goal","")}',
           f'**達成率 {rate(d)}%** ／ 現在ステージ：{PREFIX[cur-1]}{d["phases"][cur-1]["name"]}']
    if d.get("focus"):
        cal.append(f'**今週のフォーカス**：{d["focus"]}')
    if d.get("next"):
        cal.append(f'**ネクスト**：{d["next"]}')
    cal.append(f'*自動生成 {today}*')
    out.append(f'<callout icon="🎯" color="purple_bg">{"<br>".join(cal)}</callout>')

    # mermaid 図解（下から上へ）
    m = ["```mermaid", "flowchart BT"]
    nodes = []
    for i, p in enumerate(d["phases"]):
        sn = i + 1
        tasks = p.get("tasks", [])
        done = sum(1 for t in tasks if t.get("done"))
        if sn == cur:
            mark, cls = "👈今ここ", "now"
        elif tasks and done == len(tasks):
            mark, cls = "✅", "done"
        else:
            mark, cls = "", "future"
        label = f'{PREFIX[i]}{p["name"]} ({done}/{len(tasks)}) {mark}'.strip()
        m.append(f'  S{sn}["{label}"]:::{cls}')
        nodes.append(f"S{sn}")
    m.append(f'  GOAL["🎯 {d.get("goal","")}"]:::goal')
    m.append("  " + " --> ".join(nodes + ["GOAL"]))
    m.append("  classDef done fill:#D1FAE5,stroke:#047A55,color:#064E3B;")
    m.append("  classDef now fill:#FEF3C7,stroke:#EF9F27,color:#7A4E06;")
    m.append("  classDef future fill:#F2F4F6,stroke:#C0C6CC,color:#6B7280;")
    m.append("  classDef goal fill:#EDE9FE,stroke:#6357CC,color:#3A2E8C;")
    m.append("```")
    out.append("\n".join(m))

    # ステージ別タスク
    out.append('### タスク（ステージ別）')
    body = []
    for i, p in enumerate(d["phases"]):
        sn = i + 1
        mark = " 👈今ここ" if sn == cur else ""
        body.append(f'**{PREFIX[i]}{p["name"]}**{mark}')
        for t in p.get("tasks", []):
            box = "x" if t.get("done") else " "
            st = t.get("status")
            suffix = f'（{st}）' if st and st != "完了" and st != "未着手" else ""
            body.append(f'- [{box}] {t["name"]}{suffix}')
    out.append("\n".join(body))

    return "\n\n".join(out)


def main(argv):
    if len(argv) < 2:
        print("usage: notion_block.py <members/名前.json>")
        return 1
    d = load_member_data(argv[1])
    print(build(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
