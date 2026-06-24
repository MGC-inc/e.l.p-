#!/usr/bin/env python3
"""ゴールマップ図解レンダラー（SVG / PNG）

HANDOFF.md §3「図解の確定仕様」の実装。1人分の目標データ（§4 データモデル）を
受け取り、「下から上へ登る地図」の図解を SVG で出力する。cairosvg があれば PNG も出す。

使い方:
    python generate_goalmap.py members/岡野.json            # out/岡野.svg(.png) を生成
    python generate_goalmap.py members/岡野.json -o /path/foo # 出力先を指定
    cat 岡野.json | python generate_goalmap.py -            # 標準入力から

データモデル（members/_template.json 参照）:
    name, note, theme, goal, why(任意), currentStage(1..5),
    phases[5] = { name, doneDef, tasks[] = { name, done(bool) } }
    phases は index0=①(最下段) … index4=⑤(最上段)。必ず5要素。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

# ── 図仕様の定数（HANDOFF §3）─────────────────────────────
WIDTH = 680
# 配布用SVGはMac/Win/Linuxで最適なものを使うフォールバック順（仕様）。
FONT = "Hiragino Sans, 'Noto Sans CJK JP', 'Yu Gothic', sans-serif"
# PNGラスタライズ用。cairoは先頭が無いとCJKフォールバックに失敗するため、
# 実際にインストール済みのCJKフォントを先頭に置いた版で描画する。
FONT_RASTER = "'Noto Sans CJK JP', 'Yu Gothic', sans-serif"

# 色 ＝ 状態
C_DONE = "#1D9E75"      # 済（ティール）
C_DONE_BG = "#E7F5EF"
C_NOW = "#EF9F27"       # 今（アンバー）
C_NOW_BG = "#FDF3E2"
C_FUTURE = "#9AA3AD"    # これから（グレー）
C_FUTURE_BG = "#F2F4F6"
C_GOAL = "#6357CC"      # ゴール（パープル）
C_GOAL_BG = "#EEECFA"
C_INK = "#1F2933"
C_SUB = "#67707A"

# レイアウト
PHASE_X, PHASE_W = 130, 210     # フェーズ箱
PILL_X, PILL_W = 20, 58         # 時間軸ピル
TASK_X = 360                    # タスク開始
HEADER_H = 70
GAP = 26                        # 箱の縦間隔（上向き矢印が入る）
GOAL_H = 70
PAD_BOTTOM = 28
TASK_LH = 18                    # タスク1行の高さ

STAGE_NAMES = ["①型を知る", "②練習", "③実践", "④振り返り", "⑤自走"]


def esc(s) -> str:
    return escape(str(s if s is not None else ""))


def phase_box_height(n_tasks: int) -> int:
    """フェーズ箱の高さ = max(60, 24 + 18×タスク数)（HANDOFF §3）。"""
    return max(60, 24 + TASK_LH * n_tasks)


def wrap(text: str, n: int) -> list[str]:
    """日本語向けの素朴な折り返し（n文字ごと）。"""
    text = str(text or "")
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


def build_svg(d: dict, font: str = FONT) -> str:
    phases = d["phases"]
    assert len(phases) == 5, "phases は必ず5要素（①〜⑤）"
    cur = int(d.get("currentStage", 1))  # 1..5

    # 達成率 ＝ 完了タスク数 ÷ 全タスク数 ×100
    all_tasks = [t for p in phases for t in p.get("tasks", [])]
    done_tasks = [t for t in all_tasks if t.get("done")]
    total = len(all_tasks)
    rate = round(len(done_tasks) / total * 100) if total else 0

    # 各箱の高さを先に計算して総高さを決める（描画は上→下: goal,⑤,④,③,②,①）
    order = [4, 3, 2, 1, 0]  # phases index、上から
    heights = {i: phase_box_height(len(phases[i].get("tasks", []))) for i in order}
    H = HEADER_H + GOAL_H + GAP + sum(heights[i] + GAP for i in order) + PAD_BOTTOM

    out: list[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {H}" '
        f'font-family="{font}" width="{WIDTH}" height="{H}">'
    )
    out.append(f'<rect x="0" y="0" width="{WIDTH}" height="{H}" fill="#FFFFFF"/>')

    # ── ヘッダー ─────────────────────────────
    head = d.get("name", "")
    if d.get("note"):
        head += f"（{d['note']}）"
    head += f"｜{d.get('theme','')}"
    out.append(
        f'<text x="20" y="34" font-size="19" font-weight="700" fill="{C_INK}">{esc(head)}</text>'
    )
    # 達成率（右上に数値＋バー）
    bar_x, bar_w = WIDTH - 210, 150
    out.append(
        f'<text x="{WIDTH-20}" y="26" text-anchor="end" font-size="13" fill="{C_SUB}">達成率</text>'
    )
    out.append(
        f'<text x="{bar_x-8}" y="50" text-anchor="end" font-size="20" font-weight="700" '
        f'fill="{C_DONE}">{rate}%</text>'
    )
    out.append(
        f'<rect x="{bar_x}" y="38" width="{bar_w}" height="12" rx="6" fill="{C_FUTURE_BG}"/>'
    )
    out.append(
        f'<rect x="{bar_x}" y="38" width="{round(bar_w*rate/100)}" height="12" rx="6" fill="{C_DONE}"/>'
    )

    cx = PHASE_X + PHASE_W / 2  # 箱・矢印の中心x

    def up_arrow(y_bottom: float, y_top: float):
        """下の箱→上の箱への上向き矢印（中央列）。"""
        out.append(
            f'<line x1="{cx}" y1="{y_bottom}" x2="{cx}" y2="{y_top+6}" '
            f'stroke="{C_FUTURE}" stroke-width="2"/>'
        )
        out.append(
            f'<path d="M{cx-5},{y_top+8} L{cx},{y_top+2} L{cx+5},{y_top+8} Z" fill="{C_FUTURE}"/>'
        )

    # ── ゴール箱（最上部・パープル・2px枠）──────────────
    gy = HEADER_H
    out.append(
        f'<rect x="{PHASE_X}" y="{gy}" width="{PHASE_W}" height="{GOAL_H}" rx="10" '
        f'fill="{C_GOAL_BG}" stroke="{C_GOAL}" stroke-width="2"/>'
    )
    out.append(
        f'<text x="{PHASE_X+14}" y="{gy+22}" font-size="12" font-weight="700" '
        f'fill="{C_GOAL}">ゴール</text>'
    )
    for k, line in enumerate(wrap(d.get("goal", ""), 13)[:2]):
        out.append(
            f'<text x="{PHASE_X+14}" y="{gy+42+k*16}" font-size="13" '
            f'fill="{C_INK}">{esc(line)}</text>'
        )
    # 時間軸ピル「達成」
    _pill(out, gy + GOAL_H / 2, "達成", C_GOAL, C_GOAL_BG)

    # ── フェーズ箱（⑤→①）─────────────────────
    y = gy + GOAL_H + GAP
    up_arrow(y, gy + GOAL_H)  # ⑤上端 → ゴール下端
    weeks = 0
    for idx in order:           # 4,3,2,1,0
        p = phases[idx]
        h = heights[idx]
        stage_no = idx + 1
        tasks = p.get("tasks", [])

        if stage_no < cur:
            state, fg, bg, pill = "done", C_DONE, C_DONE_BG, "クリア"
        elif stage_no == cur:
            state, fg, bg, pill = "now", C_NOW, C_NOW_BG, "今"
        else:
            weeks += 1
            state, fg, bg, pill = "future", C_FUTURE, C_FUTURE_BG, f"＋{weeks}週"

        sw = 2 if state == "now" else 1  # 今＝2px強調
        out.append(
            f'<rect x="{PHASE_X}" y="{y}" width="{PHASE_W}" height="{h}" rx="10" '
            f'fill="{bg}" stroke="{fg}" stroke-width="{sw}"/>'
        )
        # 状態マーカー円（箱の左）
        mcy = y + 20
        out.append(f'<circle cx="{PHASE_X-12}" cy="{mcy}" r="9" fill="{fg}"/>')
        if state == "done":
            out.append(
                f'<path d="M{PHASE_X-16},{mcy} l3,3 l6,-7" fill="none" stroke="#fff" '
                f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
            )
        # フェーズ名＋完了定義
        out.append(
            f'<text x="{PHASE_X+12}" y="{y+22}" font-size="14" font-weight="700" '
            f'fill="{C_INK}">{esc(STAGE_NAMES[idx])}</text>'
        )
        for k, line in enumerate(wrap("完了：" + str(p.get("doneDef", "")), 15)[:2]):
            out.append(
                f'<text x="{PHASE_X+12}" y="{y+40+k*15}" font-size="11" '
                f'fill="{C_SUB}">{esc(line)}</text>'
            )
        # 時間軸ピル
        _pill(out, y + h / 2, pill, fg, bg)

        # タスク（箱の右）
        for j, t in enumerate(tasks):
            ty = y + 16 + j * TASK_LH
            done = bool(t.get("done"))
            box_c = C_DONE if done else C_FUTURE
            out.append(
                f'<rect x="{TASK_X}" y="{ty-11}" width="14" height="14" rx="3" '
                f'fill="{C_DONE if done else "#fff"}" stroke="{box_c}" stroke-width="1.5"/>'
            )
            if done:
                out.append(
                    f'<path d="M{TASK_X+3},{ty-4} l3,3 l5,-6" fill="none" stroke="#fff" '
                    f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
                )
            tcol = C_SUB if done else C_INK
            deco = ' text-decoration="line-through"' if done else ""
            out.append(
                f'<text x="{TASK_X+22}" y="{ty}" font-size="12.5" fill="{tcol}"{deco}>'
                f'{esc(t.get("name",""))}</text>'
            )

        y_next = y + h + GAP
        if idx != 0:
            up_arrow(y_next, y + h)  # 下の箱上端 → この箱下端
        y = y_next

    out.append("</svg>")
    return "\n".join(out)


def _pill(out: list[str], cy: float, label: str, fg: str, bg: str):
    out.append(
        f'<rect x="{PILL_X}" y="{cy-11}" width="{PILL_W}" height="22" rx="11" '
        f'fill="{bg}" stroke="{fg}" stroke-width="1"/>'
    )
    out.append(
        f'<text x="{PILL_X+PILL_W/2}" y="{cy+4}" text-anchor="middle" font-size="11" '
        f'font-weight="700" fill="{fg}">{esc(label)}</text>'
    )


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    opts = argv[1:]
    if not args:
        print(__doc__)
        return 1
    src = args[0]
    if src == "-":
        data = json.load(sys.stdin)
        stem = data.get("name", "goalmap")
    else:
        data = json.loads(Path(src).read_text(encoding="utf-8"))
        stem = Path(src).stem

    out_base = None
    if "-o" in opts:
        out_base = opts[opts.index("-o") + 1]
    if out_base is None:
        out_dir = Path(__file__).parent / "out"
        out_dir.mkdir(exist_ok=True)
        out_base = str(out_dir / stem)

    svg = build_svg(data)
    svg_path = Path(str(out_base) + ".svg")
    svg_path.write_text(svg, encoding="utf-8")
    print(f"wrote {svg_path}")

    try:
        import cairosvg  # type: ignore
        png_path = Path(str(out_base) + ".png")
        svg_raster = build_svg(data, font=FONT_RASTER)  # CJKフォントを先頭にした版
        cairosvg.svg2png(bytestring=svg_raster.encode("utf-8"), write_to=str(png_path), scale=2)
        print(f"wrote {png_path}")
    except Exception as e:  # noqa: BLE001
        print(f"(PNG skipped: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
