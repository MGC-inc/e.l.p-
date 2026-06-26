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
import math
import sys
from datetime import date, timedelta
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
C_LATE = "#DC2626"      # 遅れ（赤）
C_LATE_BG = "#FEE2E2"

# レイアウト
PHASE_X, PHASE_W = 130, 210     # フェーズ箱
PILL_X, PILL_W = 16, 80         # 目標時期ピル（○月第○週まで）
TASK_X = 360                    # タスク開始
HEADER_H = 70
GAP = 26                        # 箱の縦間隔（上向き矢印が入る）
GOAL_H = 70
PAD_BOTTOM = 28
TASK_LH = 18                    # タスク1行の高さ

STAGE_NAMES = ["①型を知る", "②練習", "③実践", "④振り返り", "⑤自走"]


def week_label(weeks_ahead: int) -> str:
    """weeks_ahead 週後を「○月第○週」で返す（1フェーズ＝約1週の目安）。"""
    d = date.today() + timedelta(weeks=weeks_ahead)
    return f"{d.month}月第{math.ceil(d.day / 7)}週"


def week_label_date(s: str) -> str:
    """ISO日付文字列を「○月第○週」で返す（その月の第何週か）。"""
    d = date.fromisoformat(s)
    return f"{d.month}月第{math.ceil(d.day / 7)}週"


def load_member_data(src: str) -> dict:
    """members/<名前>.json を読み込む。'-' なら標準入力。"""
    if src == "-":
        return json.load(sys.stdin)
    return json.loads(Path(src).read_text(encoding="utf-8"))


def esc(s) -> str:
    return escape(str(s if s is not None else ""))


def phase_box_height(n_tasks: int) -> int:
    """フェーズ箱の高さ = max(60, 24 + 18×タスク数)（HANDOFF §3）。"""
    return max(60, 24 + TASK_LH * n_tasks)


def wrap(text: str, n: int) -> list[str]:
    """日本語向けの素朴な折り返し（n文字ごと）。"""
    text = str(text or "")
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


# ── 成長キャラ（鶏→不死鳥）。1タスク=餌1つ、1フェーズ食べ切る=進化 ──
FORM_NAMES = ["たまご", "ひよこ", "小鶏", "とさか鶏", "極彩鶏", "不死鳥"]


def growth(d: dict) -> dict:
    per = []
    for p in d.get("phases", []):
        t = p.get("tasks", [])
        per.append((sum(1 for x in t if x.get("done")), len(t)))
    all_ = sum(tot for _, tot in per)
    done = sum(dn for dn, _ in per)
    rate = round(done / all_ * 100) if all_ else 0
    cleared = 0
    for dn, tot in per:
        if tot > 0 and dn == tot:
            cleared += 1
        else:
            break
    form = 5 if rate >= 100 else min(cleared, 4)
    nxt = None
    for dn, tot in per:
        if not (tot > 0 and dn == tot):
            nxt = (dn, tot)
            break
    if nxt is None:
        nxt = per[-1] if per else (0, 0)
    return {"rate": rate, "form": form, "allDone": done, "all": all_,
            "need": max(0, nxt[1] - nxt[0]), "phaseDone": nxt[0], "phaseTotal": nxt[1]}


def star(cx: float, cy: float, r: float, fill: str) -> str:
    pts = [(0, -1), (0.24, -0.24), (1, 0), (0.24, 0.24),
           (0, 1), (-0.24, 0.24), (-1, 0), (-0.24, -0.24)]
    s = " ".join(f"{cx+x*r:.1f},{cy+y*r:.1f}" for x, y in pts)
    return f'<polygon points="{s}" fill="{fill}"/>'


def bird_markup(form: int, cx: float, cy: float, s: float, phase_done: int) -> str:
    X = lambda v: f"{cx+v*s:.1f}"  # noqa: E731
    Y = lambda v: f"{cy+v*s:.1f}"  # noqa: E731
    R = lambda v: f"{v*s:.1f}"     # noqa: E731
    o: list[str] = []

    def line(x1, y1, x2, y2, c, w):
        o.append(f'<line x1="{X(x1)}" y1="{Y(y1)}" x2="{X(x2)}" y2="{Y(y2)}" '
                 f'stroke="{c}" stroke-width="{R(w)}" stroke-linecap="round"/>')

    def circ(x, y, r, f):
        o.append(f'<circle cx="{X(x)}" cy="{Y(y)}" r="{R(r)}" fill="{f}"/>')

    def ell(x, y, rx, ry, f):
        o.append(f'<ellipse cx="{X(x)}" cy="{Y(y)}" rx="{R(rx)}" ry="{R(ry)}" fill="{f}"/>')

    def beak(x, y, w, c="#F2870D"):
        o.append(f'<polygon points="{X(x)},{Y(y-2.2)} {X(x+w)},{Y(y)} '
                 f'{X(x)},{Y(y+2.2)}" fill="{c}"/>')

    def eye(x, y):
        circ(x, y, 2.1, "#FFFFFF")
        circ(x + 0.3, y, 1.2, "#33270D")

    def comb(x, y, n, r):
        for i in range(n):
            circ(x + i * r * 1.3 - ((n - 1) * r * 1.3) / 2, y - (1.6 if i % 2 else 0), r, "#E0352B")

    def M(x, y):
        return f"M{X(x)},{Y(y)}"

    def Q(a, b, x, y):
        return f"Q{X(a)},{Y(b)} {X(x)},{Y(y)}"

    def L(x, y):
        return f"L{X(x)},{Y(y)}"

    def path(d_, f=None, st=None, w=2):
        stroke = (f' stroke="{st}" stroke-width="{R(w)}" stroke-linecap="round" '
                  f'stroke-linejoin="round"') if st else ""
        o.append(f'<path d="{d_}" fill="{f or "none"}"{stroke}/>')

    if form == 0:
        ell(0, 0, 15, 19, "#FFF3D6")
        o.append(f'<ellipse cx="{X(0)}" cy="{Y(0)}" rx="{R(15)}" ry="{R(19)}" '
                 f'fill="none" stroke="#E2D2A0" stroke-width="{R(1.4)}"/>')
        circ(-5, -3, 1.5, "#E6D3A0"); circ(5, 5, 1.3, "#E6D3A0"); circ(1, 11, 1.2, "#E6D3A0")
        if phase_done > 0:
            path(M(-6, -7) + L(-2, -3) + L(-6, 1) + L(-1, 5), None, "#C9B27A", 1.6)
    elif form == 1:
        line(-4, 13, -4, 19, "#F2A30D", 2); line(4, 13, 4, 19, "#F2A30D", 2)
        circ(0, 2, 13, "#FFD23F"); ell(-8, 3, 4.5, 7, "#F4C12B")
        circ(0, -9, 8, "#FFD23F"); beak(7, -9, 6); eye(3, -11)
        path(M(-1, -16) + Q(-3, -21, 1, -21), None, "#F4C12B", 1.6)
    elif form == 2:
        line(-5, 15, -5, 21, "#F2A30D", 2); line(5, 15, 5, 21, "#F2A30D", 2)
        path(M(-13, 2) + Q(-21, 0, -22, -8), None, "#F4C12B", 3)
        ell(0, 3, 15, 14, "#FFE066"); ell(-7, 4, 5.5, 9, "#F4C12B"); line(-9, 0, -5, 7, "#E8B23A", 1.4)
        circ(2, -9, 9, "#FFE066"); comb(2, -18, 2, 2.4); beak(11, -9, 6); eye(6, -11)
    elif form == 3:
        line(-5, 17, -5, 24, "#E8901F", 2.2); line(5, 17, 5, 24, "#E8901F", 2.2)
        path(M(-14, 2) + Q(-26, -2, -24, -14), None, "#E8B04B", 4)
        path(M(-13, 5) + Q(-24, 4, -26, -6), None, "#C98A2E", 3)
        ell(0, 4, 17, 15, "#FBEFD0"); ell(-6, 5, 7, 10, "#E8B04B"); line(-8, 1, -3, 9, "#CC9A3D", 1.5)
        circ(4, -9, 9, "#FBEFD0"); comb(4, -19, 3, 2.6)
        o.append(f'<ellipse cx="{X(11)}" cy="{Y(-4)}" rx="{R(1.8)}" ry="{R(3.4)}" fill="#E0352B"/>')
        beak(12, -8, 6); eye(7, -11)
    elif form == 4:
        line(-5, 18, -5, 25, "#E8901F", 2.4); line(5, 18, 5, 25, "#E8901F", 2.4)
        path(M(-14, 3) + Q(-30, -3, -27, -18), None, "#37C9B0", 4.5)
        path(M(-15, 6) + Q(-30, 4, -30, -9), None, "#FF8A3D", 4)
        path(M(-13, 9) + Q(-26, 11, -29, 1), None, "#FFD23F", 3.5)
        ell(0, 5, 18, 16, "url(#hen)"); ell(-5, 6, 7.5, 11, "#FF8A3D"); line(-7, 2, -2, 11, "#E0702A", 1.6)
        circ(5, -9, 10, "url(#hen)"); comb(5, -21, 3, 3)
        o.append(f'<ellipse cx="{X(13)}" cy="{Y(-4)}" rx="{R(2)}" ry="{R(3.8)}" fill="#E0352B"/>')
        beak(13, -8, 7); eye(8, -11)
        o.append(star(cx + 15 * s, cy - 16 * s, 3.4 * s, "#FFFFFF"))
    else:
        # 最終進化：クジャク×黄金のゴージャス版（青緑〜金イリデッセント・特大の羽・目玉の大扇・黄金の装飾）
        rays = [(31, 0), (22, 22), (0, 31), (-22, 22), (-31, 0), (-22, -22), (0, -31), (22, -22)]
        for i, (rx, ry) in enumerate(rays):
            o.append(f'<line x1="{X(0)}" y1="{Y(0)}" x2="{X(rx)}" y2="{Y(ry)}" '
                     f'stroke="#F2C230" stroke-width="{R(1.4 if i % 2 else 2.4)}" '
                     f'stroke-linecap="round" opacity="0.28"/>')
        o.append(f'<circle cx="{X(0)}" cy="{Y(0)}" r="{R(30)}" fill="#2BD0C0" opacity="0.10"/>')
        o.append(f'<circle cx="{X(0)}" cy="{Y(0)}" r="{R(24)}" fill="none" '
                 f'stroke="#F2C230" stroke-width="{R(1.4)}" opacity="0.45"/>')
        o.append(f'<circle cx="{X(0)}" cy="{Y(0)}" r="{R(20)}" fill="#FFF6D8" opacity="0.14"/>')
        uf = [(-0.914, -0.407), (-0.695, -0.719), (-0.375, -0.927), (0, -1),
              (0.375, -0.927), (0.695, -0.719), (0.914, -0.407)]
        rf, bx, by = 32, 0, 6
        for dx, dy in uf:
            tx, ty = bx + dx * rf, by + dy * rf
            mx, my = bx + dx * rf * 0.55, by + dy * rf * 0.55
            path(M(bx, by) + Q(mx, my, tx, ty), None, "#C9A227", 2.4)
            circ(tx, ty, 6, "#E6B422"); circ(tx, ty, 4.4, "#C9A227")
            circ(tx, ty, 3.2, "#1FA6A0"); circ(tx, ty, 1.9, "#173A8C")
            o.append(f'<circle cx="{X(tx-0.6)}" cy="{Y(ty-0.6)}" r="{R(0.7)}" fill="#FFFFFF"/>')
        path(M(-3, 0) + Q(-22, -6, -35, -16) + Q(-20, -12, -5, -5) + "Z", "url(#pcock)")
        path(M(-3, 3) + Q(-24, 2, -35, -3) + Q(-18, -2, -5, -1) + "Z", "#1746A0")
        path(M(-3, 0) + Q(-22, -6, -35, -16), None, "#F2C230", 1.6)
        path(M(3, 0) + Q(22, -6, 35, -16) + Q(20, -12, 5, -5) + "Z", "url(#pcock)")
        path(M(3, 3) + Q(24, 2, 35, -3) + Q(18, -2, 5, -1) + "Z", "#1746A0")
        path(M(3, 0) + Q(22, -6, 35, -16), None, "#F2C230", 1.6)
        ell(0, 6, 12, 15, "url(#pcock)")
        o.append(f'<ellipse cx="{X(0)}" cy="{Y(9)}" rx="{R(6)}" ry="{R(9.5)}" fill="url(#gold)" opacity="0.95"/>')
        o.append(f'<ellipse cx="{X(-2)}" cy="{Y(5)}" rx="{R(2.4)}" ry="{R(4.5)}" fill="#FFF6D8" opacity="0.55"/>')
        circ(0, -9, 7.5, "#1C4FB0")
        path(M(-5, -15) + L(-4, -21) + L(-2, -16) + L(0, -22)
             + L(2, -16) + L(4, -21) + L(5, -15) + "Z", "url(#gold)")
        circ(0, -22.5, 1.5, "#FFF0A8")
        beak(7, -9, 7, "#E8E0C8"); eye(3, -11)
        o.append(star(cx, cy - 13 * s, 2 * s, "#FFFFFF"))
        o.append(star(cx - 27 * s, cy - 9 * s, 2.6 * s, "#FFF1B8"))
        o.append(star(cx + 28 * s, cy - 3 * s, 2.2 * s, "#FFF1B8"))
        o.append(star(cx + 20 * s, cy + 15 * s, 2 * s, "#FFE7A0"))
    return "".join(o)


def build_svg(d: dict, font: str = FONT) -> str:
    phases = d["phases"]
    assert len(phases) == 5, "phases は必ず5要素（①〜⑤）"
    cur = int(d.get("currentStage", 1))  # 1..5

    # 達成率 ＝ 完了タスク数 ÷ 全タスク数 ×100
    all_tasks = [t for p in phases for t in p.get("tasks", [])]
    done_tasks = [t for t in all_tasks if t.get("done")]
    total = len(all_tasks)
    rate = round(len(done_tasks) / total * 100) if total else 0
    today_iso = date.today().isoformat()
    overdue_any = False

    # 各箱の高さを先に計算して総高さを決める（描画は上→下: goal,⑤,④,③,②,①）
    order = [4, 3, 2, 1, 0]  # phases index、上から
    heights = {i: phase_box_height(len(phases[i].get("tasks", []))) for i in order}
    H = HEADER_H + GOAL_H + GAP + sum(heights[i] + GAP for i in order) + PAD_BOTTOM

    out: list[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {H}" '
        f'font-family="{font}" width="{WIDTH}" height="{H}">'
    )
    out.append(
        '<defs>'
        '<linearGradient id="goalGrad" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#5B4BE0"/>'
        '<stop offset="0.5" stop-color="#B14BE0"/>'
        '<stop offset="1" stop-color="#FFC24B"/></linearGradient>'
        '<linearGradient id="phx" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#FFE259"/>'
        '<stop offset="0.5" stop-color="#FF9A00"/>'
        '<stop offset="1" stop-color="#FF3D00"/></linearGradient>'
        '<linearGradient id="phx2" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#FFF0A0"/>'
        '<stop offset="1" stop-color="#FF7A00"/></linearGradient>'
        '<linearGradient id="hen" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#37C9B0"/>'
        '<stop offset="0.6" stop-color="#3DA0E8"/>'
        '<stop offset="1" stop-color="#7A5BE0"/></linearGradient>'
        '<linearGradient id="pcock" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#2BD0C0"/>'
        '<stop offset="0.55" stop-color="#2E8FD0"/>'
        '<stop offset="1" stop-color="#2E4BB0"/></linearGradient>'
        '<linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#FFF0A8"/>'
        '<stop offset="0.5" stop-color="#F2C230"/>'
        '<stop offset="1" stop-color="#C8901A"/></linearGradient>'
        '</defs>'
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

    # ── ゴール箱（最上部・輝くグラデーション・金枠）──────────────
    gy = HEADER_H
    out.append(
        f'<rect x="{PHASE_X}" y="{gy}" width="{PHASE_W}" height="{GOAL_H}" rx="10" '
        f'fill="url(#goalGrad)" stroke="#FFD36E" stroke-width="2.5"/>'
    )
    # 上半分のグロス（白を薄く重ねて“ツヤ”を出す）
    out.append(
        f'<rect x="{PHASE_X}" y="{gy}" width="{PHASE_W}" height="{GOAL_H/2}" rx="10" '
        f'fill="#FFFFFF" opacity="0.16"/>'
    )
    out.append(
        f'<text x="{PHASE_X+14}" y="{gy+22}" font-size="12" font-weight="700" '
        f'fill="#FFFFFF">ゴール</text>'
    )
    out.append(star(PHASE_X + PHASE_W - 16, gy + 15, 6, "#FFF1B8"))
    for k, line in enumerate(wrap(d.get("goal", ""), 13)[:2]):
        out.append(
            f'<text x="{PHASE_X+14}" y="{gy+42+k*16}" font-size="13" font-weight="600" '
            f'fill="#FFFFFF">{esc(line)}</text>'
        )
    # 時間軸ピル「達成」（ゴールに合わせて金）
    _pill(out, gy + GOAL_H / 2, "達成", "#B7791F", "#FFF3D6")

    # ── 成長キャラ（達成感メーター：1タスク=餌、1フェーズ完食=進化）──
    g = growth(d)
    ccx, ccy, cs, tx = 402, 112, 1.7, 448
    out.append(bird_markup(g["form"], ccx, ccy, cs, g["phaseDone"]))
    if g["form"] < 5:
        out.append(
            f'<text x="{tx}" y="{ccy-3}" font-size="13" font-weight="700" '
            f'fill="{C_INK}">あと{g["need"]}コで進化</text>'
        )
        out.append(
            f'<text x="{tx}" y="{ccy+15}" font-size="11" fill="{C_SUB}">'
            f'餌 {g["phaseDone"]}/{g["phaseTotal"]}　累計 {g["allDone"]}/{g["all"]}</text>'
        )
    else:
        out.append(
            f'<text x="{tx}" y="{ccy+2}" font-size="13" font-weight="700" '
            f'fill="#C2410C">覚醒！全タスク完了</text>'
        )

    # 目標時期ピルの列見出し
    out.append(
        f'<text x="{PILL_X}" y="62" font-size="10" fill="{C_SUB}">目標時期</text>'
    )

    # ── フェーズ箱（⑤→①）─────────────────────
    y = gy + GOAL_H + GAP
    up_arrow(y, gy + GOAL_H)  # ⑤上端 → ゴール下端
    for idx in order:           # 4,3,2,1,0
        p = phases[idx]
        h = heights[idx]
        stage_no = idx + 1
        tasks = p.get("tasks", [])

        if stage_no < cur:
            state, fg, bg = "done", C_DONE, C_DONE_BG
        elif stage_no == cur:
            state, fg, bg = "now", C_NOW, C_NOW_BG
        else:
            state, fg, bg = "future", C_FUTURE, C_FUTURE_BG
        # 目標時期ピル（終了日から固定。未完了かつ期限超過＝遅れは赤で強調）
        phase_complete = bool(tasks) and all(t.get("done") for t in tasks)
        due = p.get("due")
        if stage_no < cur or phase_complete:
            pill, pfg, pbg = "クリア", C_DONE, C_DONE_BG
        elif due and due < today_iso:
            pill, pfg, pbg = "⚠" + week_label_date(due), C_LATE, C_LATE_BG
            overdue_any = True
        else:
            pill = week_label_date(due) if due else "—"
            pfg, pbg = (C_NOW, C_NOW_BG) if state == "now" else (C_FUTURE, C_FUTURE_BG)

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
        # 目標時期ピル
        _pill(out, y + h / 2, pill, pfg, pbg)

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

    # 遅れ告知バナー（赤）
    if overdue_any:
        bx2, bw2 = 268, 156
        out.append(f'<rect x="{bx2}" y="10" width="{bw2}" height="22" rx="11" fill="{C_LATE}"/>')
        out.append(
            f'<text x="{bx2+bw2/2}" y="25" text-anchor="middle" font-size="12" '
            f'font-weight="700" fill="#FFFFFF">⚠ 期限に遅れあり</text>'
        )

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
    data = load_member_data(src)
    stem = data.get("name", "goalmap") if src == "-" else Path(src).stem

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
