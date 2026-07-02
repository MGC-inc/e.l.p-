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


def wrap_max(text: str, n: int, max_lines: int) -> list[str]:
    """n文字で折り返し、max_lines を超えたら末尾を「…」に丸める。"""
    a = wrap(text, n)
    if len(a) <= max_lines:
        return a
    b = a[:max_lines]
    b[-1] = b[-1][:n - 1] + "…"
    return b


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
    cycle = max(1, int(d.get("cycle", 1) or 1))
    # earned＝これまでに孵した不死鳥の数（過去の周回＋今回100%なら今回も）
    earned = (cycle - 1) + (1 if rate >= 100 else 0)
    return {"rate": rate, "form": form, "allDone": done, "all": all_,
            "need": max(0, nxt[1] - nxt[0]), "phaseDone": nxt[0], "phaseTotal": nxt[1],
            "cycle": cycle, "earned": earned}


def star(cx: float, cy: float, r: float, fill: str) -> str:
    pts = [(0, -1), (0.24, -0.24), (1, 0), (0.24, 0.24),
           (0, 1), (-0.24, 0.24), (-1, 0), (-0.24, -0.24)]
    s = " ".join(f"{cx+x*r:.1f},{cy+y*r:.1f}" for x, y in pts)
    return f'<polygon points="{s}" fill="{fill}"/>'


def bird_markup(form: int, cx: float, cy: float, s: float, phase_done: int, gen: int = 0) -> str:
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
    # 周回番号：2周目以降、卵や鳥の胸（不死鳥は黄金の胸当て）に番号を刻む。
    # 卵に戻ってもリセット感を出さない＝何羽目の不死鳥かが一目で分かる。
    if gen >= 2:
        # フォーム別の胸（卵は本体中央）の位置と紋章サイズ [chx, chy, r]
        ch = {0: (0, 4, 6.4), 1: (0, 3, 5.4), 2: (0, 5, 6.0),
              3: (0, 6, 6.4), 4: (0, 7, 6.8), 5: (0, 9, 5.8)}.get(form, (0, 5, 6.2))
        chx, chy, cr = ch
        o.append(f'<circle cx="{X(chx)}" cy="{Y(chy)}" r="{R(cr)}" fill="#7A3E00" opacity="0.92"/>')
        o.append(f'<circle cx="{X(chx)}" cy="{Y(chy)}" r="{R(cr)}" fill="none" '
                 f'stroke="#F2C230" stroke-width="{R(1)}"/>')
        o.append(f'<text x="{X(chx)}" y="{cy+chy*s+cr*0.52*s:.1f}" text-anchor="middle" '
                 f'font-size="{R(cr*1.5)}" font-weight="800" fill="#FFF3D6">{gen}</text>')
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
    TASK_N, DD_N = 22, 15   # タスク／完了定義の折り返し文字数

    def phase_height(p: dict) -> int:
        tks = p.get("tasks", [])
        right_h = 12 + sum(len(wrap_max(t.get("name", ""), TASK_N, 2)) * 15 + 6 for t in tks)
        left_h = 44 + len(wrap_max("完了：" + p.get("doneDef", ""), DD_N, 2)) * 15
        return max(58, right_h, left_h)

    heights = {i: phase_height(phases[i]) for i in order}
    # 上位目標（大枠＝vision／中目標＝midGoal）。本文に合わせて高さ可変・未設定は描かない
    has_vision = bool(str(d.get("vision", "")).strip())
    has_mid = bool(str(d.get("midGoal", "")).strip())
    vis_lines = wrap_max(d.get("vision", ""), 18, 3) if has_vision else []
    mid_lines = wrap_max(d.get("midGoal", ""), 18, 3) if has_mid else []
    VIS_BAND = (24 + len(vis_lines) * 15) if has_vision else 0
    MID_BAND = (24 + len(mid_lines) * 15) if has_mid else 0
    VIS_GAP, MID_GAP = 22, 20
    VIS_H = (VIS_BAND + VIS_GAP) if has_vision else 0
    MID_H = (MID_BAND + MID_GAP) if has_mid else 0
    TOP_H = VIS_H + MID_H
    goal_lines = wrap_max(d.get("goal", ""), 15, 3)
    GOAL_H = max(66, 30 + len(goal_lines) * 17)
    H = HEADER_H + TOP_H + GOAL_H + GAP + sum(heights[i] + GAP for i in order) + PAD_BOTTOM

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

    # ── ヘッダー（長いタイトルは達成率バーに被らないよう自動で少し小さく）──
    head = d.get("name", "")
    if d.get("note"):
        head += f"（{d['note']}）"
    head += f"｜{d.get('theme','')}"
    head_font = max(13, 19 * 23 // len(head)) if len(head) > 23 else 19
    out.append(
        f'<text x="20" y="34" font-size="{head_font}" font-weight="700" fill="{C_INK}">{esc(head)}</text>'
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

    # ── 上位目標の段（大枠＝紫破線・中目標＝青破線）を上から積み、上向き矢印で連結 ──
    gy = HEADER_H + TOP_H

    def tier_band(y, h, fill, stroke, label, due, lines, body_fill):
        out.append(
            f'<rect x="{PHASE_X}" y="{y}" width="{PHASE_W}" height="{h}" rx="10" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.6" stroke-dasharray="5 4"/>'
        )
        out.append(
            f'<text x="{PHASE_X+12}" y="{y+16}" font-size="10.5" font-weight="700" '
            f'fill="{stroke}">{esc(label)}</text>'
        )
        if due:
            out.append(
                f'<text x="{PHASE_X+PHASE_W-10}" y="{y+16}" text-anchor="end" '
                f'font-size="9.5" fill="{stroke}">{esc(due)}</text>'
            )
        for k, line in enumerate(lines):
            out.append(
                f'<text x="{PHASE_X+12}" y="{y+32+k*15}" font-size="12" font-weight="600" '
                f'fill="{body_fill}">{esc(line)}</text>'
            )

    def dashed_up_arrow(y_upper_bottom, y_lower_top, color, label):
        out.append(
            f'<line x1="{cx}" y1="{y_lower_top}" x2="{cx}" y2="{y_upper_bottom+5}" '
            f'stroke="{color}" stroke-width="2" stroke-dasharray="4 3"/>'
        )
        out.append(
            f'<path d="M{cx-5},{y_upper_bottom+7} L{cx},{y_upper_bottom+1} '
            f'L{cx+5},{y_upper_bottom+7} Z" fill="{color}"/>'
        )
        if label:
            out.append(
                f'<text x="{cx+9}" y="{y_upper_bottom+16}" font-size="9.5" fill="{color}">{label}</text>'
            )

    if has_vision:
        tier_band(HEADER_H, VIS_BAND, "#F3F0FF", "#8A7FD0",
                  "大枠ゴール（長期）", d.get("visionDue", ""), vis_lines, "#4B3FA0")
    if has_mid:
        tier_band(HEADER_H + VIS_H, MID_BAND, "#EEF6FF", "#5C90CE",
                  "中目標（中期）", d.get("midDue", ""), mid_lines, "#2C5A93")
    if has_mid:
        dashed_up_arrow(HEADER_H + VIS_H + MID_BAND, gy, "#5C90CE", "その先へ")
    if has_vision and has_mid:
        dashed_up_arrow(HEADER_H + VIS_BAND, HEADER_H + VIS_H, "#8A7FD0", "")
    if has_vision and not has_mid:
        dashed_up_arrow(HEADER_H + VIS_BAND, gy, "#8A7FD0", "その先へ")
    # ── ゴール箱（輝くグラデーション・金枠）──────────────
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
        f'<text x="{PHASE_X+14}" y="{gy+20}" font-size="12" font-weight="700" '
        f'fill="#FFFFFF">ゴール</text>'
    )
    out.append(star(PHASE_X + PHASE_W - 16, gy + 15, 6, "#FFF1B8"))
    for k, line in enumerate(goal_lines):
        out.append(
            f'<text x="{PHASE_X+14}" y="{gy+38+k*16}" font-size="13" font-weight="600" '
            f'fill="#FFFFFF">{esc(line)}</text>'
        )
    # 時間軸ピル「達成」（ゴールに合わせて金）
    _pill(out, gy + GOAL_H / 2, "達成", "#B7791F", "#FFF3D6")

    # ── 成長キャラ（達成感メーター：1タスク=餌、1フェーズ完食=進化）──
    g = growth(d)
    ccx, ccy, cs, tx = 402, gy + round(GOAL_H / 2) + 6, 1.7, 448
    out.append(bird_markup(g["form"], ccx, ccy, cs, g["phaseDone"],
                           g["cycle"] if g["cycle"] >= 2 else 0))
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
    # 周回（何羽目の不死鳥まで来たか）— 2周目以降 or 不死鳥で表示
    if g["cycle"] >= 2 or g["earned"] > 0:
        suffix = f'・達成 {g["earned"]}羽' if g["earned"] > 0 else ""
        out.append(
            f'<text x="{tx}" y="{ccy+33}" font-size="11" font-weight="700" '
            f'fill="#B7791F">{g["cycle"]}周目{suffix}</text>'
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
        for k, line in enumerate(wrap_max("完了：" + str(p.get("doneDef", "")), DD_N, 2)):
            out.append(
                f'<text x="{PHASE_X+12}" y="{y+40+k*15}" font-size="11" '
                f'fill="{C_SUB}">{esc(line)}</text>'
            )
        # 目標時期ピル
        _pill(out, y + h / 2, pill, pfg, pbg)

        # タスク（箱の右・長い名前は折り返す）
        ty = y + 18
        for t in tasks:
            lines = wrap_max(t.get("name", ""), TASK_N, 2)
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
            for k, line in enumerate(lines):
                out.append(
                    f'<text x="{TASK_X+22}" y="{ty+k*15}" font-size="12.5" fill="{tcol}"{deco}>'
                    f'{esc(line)}</text>'
                )
            ty += len(lines) * 15 + 6

        y_next = y + h + GAP
        if idx != 0:
            up_arrow(y_next, y + h)  # 下の箱上端 → この箱下端
        y = y_next

    # 遅れ告知バナー（赤）。タイトル(y34)と達成率バー(右)に重ならないよう、
    # ヘッダー下段の中央（y44〜66）に配置する。
    if overdue_any:
        bx2, bw2 = 196, 176
        out.append(f'<rect x="{bx2}" y="44" width="{bw2}" height="22" rx="11" fill="{C_LATE}"/>')
        out.append(
            f'<text x="{bx2+bw2/2}" y="59" text-anchor="middle" font-size="12" '
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


# ── 1枚カード（スマホ縦・週次スクショ共有用）。studioの buildSummarySvg と同仕様 ──
GRAD_DEFS = (
    '<defs>'
    '<linearGradient id="goalGrad" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#5B4BE0"/><stop offset="0.5" stop-color="#B14BE0"/>'
    '<stop offset="1" stop-color="#FFC24B"/></linearGradient>'
    '<linearGradient id="phx" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#FFE259"/><stop offset="0.5" stop-color="#FF9A00"/>'
    '<stop offset="1" stop-color="#FF3D00"/></linearGradient>'
    '<linearGradient id="phx2" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#FFF0A0"/><stop offset="1" stop-color="#FF7A00"/></linearGradient>'
    '<linearGradient id="hen" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#37C9B0"/><stop offset="0.6" stop-color="#3DA0E8"/>'
    '<stop offset="1" stop-color="#7A5BE0"/></linearGradient>'
    '<linearGradient id="pcock" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#2BD0C0"/><stop offset="0.55" stop-color="#2E8FD0"/>'
    '<stop offset="1" stop-color="#2E4BB0"/></linearGradient>'
    '<linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#FFF0A8"/><stop offset="0.5" stop-color="#F2C230"/>'
    '<stop offset="1" stop-color="#C8901A"/></linearGradient>'
    '</defs>'
)


def top_task(d: dict) -> dict:
    """今週の最優先タスク：現在ステージ以降で最初の未完タスク。"""
    cur = int(d.get("currentStage", 1))
    phases = d.get("phases", [])
    for s in range(cur - 1, len(phases)):
        for t in phases[s].get("tasks", []):
            if not t.get("done"):
                return {"task": t.get("name", ""), "stage": s}
    for s in range(len(phases)):
        for t in phases[s].get("tasks", []):
            if not t.get("done"):
                return {"task": t.get("name", ""), "stage": s}
    return {"task": "全タスク完了！次のゴールへ", "stage": cur - 1}


def build_summary_svg(d: dict, font: str = FONT) -> str:
    W, PAD, task_h = 430, 12, 16   # LINE可読性のため横幅を広げ文字を一回り大きく
    g = growth(d)
    cur = int(d.get("currentStage", 1))
    tt = top_task(d)
    today_iso = date.today().isoformat()
    phases = d.get("phases", [])
    blocks = [{"idx": i, "p": p, "tasks": p.get("tasks", []),
               "h": 22 + max(1, len(p.get("tasks", []))) * task_h + 6}
              for i, p in enumerate(phases)]
    top_lines = []
    if str(d.get("vision", "")).strip():
        top_lines.append(("大枠：" + str(d.get("vision", "")), "#6357CC"))
    if str(d.get("midGoal", "")).strip():
        top_lines.append(("中目標：" + str(d.get("midGoal", "")), "#2C5A93"))
    vis_h = len(top_lines) * 14 + 6 if top_lines else 0
    gy, gw, gh = 48 + vis_h, 270, 64
    py, strip_h = gy + gh + 12, 46
    head_h = py + strip_h + 10
    H = head_h + sum(b["h"] + 6 for b in blocks) + PAD
    o: list[str] = []
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
             f'font-family="{font}" width="{W}" height="{H}">')
    o.append(GRAD_DEFS)
    o.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#fff"/>')
    # 名前・テーマ／達成率・バー
    head = esc(d.get("name", "")) + (f"（{esc(d['note'])}）" if d.get("note") else "")
    o.append(f'<text x="{PAD}" y="22" font-size="16" font-weight="700" fill="{C_INK}">{head}</text>')
    o.append(f'<text x="{PAD}" y="40" font-size="11.5" fill="{C_SUB}">{esc(d.get("theme",""))}</text>')
    for i, (t, c) in enumerate(top_lines):  # 上位目標（大枠／中目標）を先頭に最大2行
        vs = t[:34] + "…" if len(t) > 34 else t
        o.append(f'<text x="{PAD}" y="{54+i*14}" font-size="11" font-weight="700" '
                 f'fill="{c}">{esc(vs)}</text>')
    o.append(f'<text x="{W-PAD}" y="22" text-anchor="end" font-size="17" font-weight="700" '
             f'fill="{C_DONE}">達成率 {g["rate"]}%</text>')
    o.append(f'<rect x="{W-150}" y="30" width="138" height="8" rx="4" fill="{C_FUTURE_BG}"/>')
    o.append(f'<rect x="{W-150}" y="30" width="{round(138*g["rate"]/100)}" height="8" rx="4" fill="{C_DONE}"/>')
    # ゴール箱
    o.append(f'<rect x="{PAD}" y="{gy}" width="{gw}" height="{gh}" rx="10" '
             f'fill="url(#goalGrad)" stroke="#FFD36E" stroke-width="2"/>')
    o.append(f'<rect x="{PAD}" y="{gy}" width="{gw}" height="{gh/2}" rx="10" fill="#fff" opacity="0.16"/>')
    o.append(f'<text x="{PAD+12}" y="{gy+20}" font-size="12" font-weight="700" fill="#fff">ゴール</text>')
    o.append(star(PAD + gw - 15, gy + 14, 6, "#FFF1B8"))
    for k, line in enumerate(wrap(d.get("goal", ""), 18)[:2]):
        o.append(f'<text x="{PAD+12}" y="{gy+40+k*17}" font-size="14" font-weight="600" '
                 f'fill="#fff">{esc(line)}</text>')
    # アバター（小）＋キャプション
    acx, acy = round((PAD + gw + (W - PAD)) / 2), gy + 32
    o.append(bird_markup(g["form"], acx, acy, 1.3, g["phaseDone"], g["cycle"] if g["cycle"] >= 2 else 0))
    cap = (f'あと{g["need"]}コ' if g["form"] < 5 else "覚醒") + (f'・{g["cycle"]}周目' if g["cycle"] >= 2 else "")
    o.append(f'<text x="{acx}" y="{gy+gh+2}" text-anchor="middle" font-size="11" '
             f'font-weight="700" fill="#B7791F">{esc(cap)}</text>')
    # 今週の最優先ストリップ（🎯は絵文字非対応のため的マークを描画）
    o.append(f'<rect x="{PAD}" y="{py}" width="{W-2*PAD}" height="{strip_h}" rx="10" '
             f'fill="{C_NOW_BG}" stroke="{C_NOW}"/>')
    txn, tyn = PAD + 14, py + 13
    o.append(f'<circle cx="{txn}" cy="{tyn}" r="5.2" fill="none" stroke="{C_NOW}" stroke-width="1.8"/>')
    o.append(f'<circle cx="{txn}" cy="{tyn}" r="2.6" fill="none" stroke="{C_NOW}" stroke-width="1.5"/>')
    o.append(f'<circle cx="{txn}" cy="{tyn}" r="1.3" fill="{C_NOW}"/>')
    o.append(f'<text x="{txn+11}" y="{py+17}" font-size="12" font-weight="700" '
             f'fill="{C_NOW}">今週の最優先（今ここ：{esc(STAGE_NAMES[tt["stage"]])}）</text>')
    o.append(f'<text x="{PAD+12}" y="{py+37}" font-size="15" font-weight="700" '
             f'fill="{C_INK}">{esc(wrap_max(tt["task"], 30, 1)[0])}</text>')
    # ステージ（①→⑤・全タスク）
    y = head_h
    for b in blocks:
        sn = b["idx"] + 1
        is_now = sn == cur
        if sn < cur:
            fg = C_DONE
        elif is_now:
            fg = C_NOW
        else:
            fg = C_FUTURE
        if is_now:
            o.append(f'<rect x="6" y="{y-2}" width="{W-12}" height="{b["h"]}" rx="9" '
                     f'fill="{C_NOW_BG}" opacity="0.5"/>')
        o.append(f'<circle cx="{PAD+6}" cy="{y+11}" r="5.5" fill="{fg}"/>')
        o.append(f'<text x="{PAD+18}" y="{y+15}" font-size="13" font-weight="700" '
                 f'fill="{C_INK}">{esc(STAGE_NAMES[b["idx"]])}</text>')
        tasks = b["tasks"]
        complete = bool(tasks) and all(t.get("done") for t in tasks)
        due = b["p"].get("due")
        if sn < cur or complete:
            pl, pf, pb = "クリア", C_DONE, C_DONE_BG
        elif due and due < today_iso:
            pl, pf, pb = "⚠" + week_label_date(due), C_LATE, C_LATE_BG
        else:
            pl = week_label_date(due) if due else "—"
            pf, pb = (C_NOW, C_NOW_BG) if is_now else (C_FUTURE, C_FUTURE_BG)
        o.append(f'<rect x="{W-100}" y="{y+2}" width="88" height="19" rx="9.5" fill="{pb}" stroke="{pf}"/>')
        o.append(f'<text x="{W-56}" y="{y+15}" text-anchor="middle" font-size="11" '
                 f'font-weight="700" fill="{pf}">{esc(pl)}</text>')
        ty = y + 34
        rows = tasks if tasks else [{"name": "（タスクなし）", "done": False}]
        for t in rows:
            dn = bool(t.get("done"))
            o.append(f'<rect x="{PAD+18}" y="{ty-10}" width="13" height="13" rx="3" '
                     f'fill="{C_DONE if dn else "#fff"}" stroke="{C_DONE if dn else C_FUTURE}" stroke-width="1.4"/>')
            if dn:
                o.append(f'<path d="M{PAD+21},{ty-3.5} l2.4,2.4 l4.6,-6" fill="none" stroke="#fff" '
                         f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')
            deco = ' text-decoration="line-through"' if dn else ""
            o.append(f'<text x="{PAD+36}" y="{ty}" font-size="12" '
                     f'fill="{C_SUB if dn else C_INK}"{deco}>{esc(wrap_max(t.get("name",""), 30, 1)[0])}</text>')
            ty += task_h
        y += b["h"] + 6
    o.append("</svg>")
    return "".join(o)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    opts = argv[1:]
    if not args:
        print(__doc__)
        return 1
    src = args[0]
    data = load_member_data(src)
    stem = data.get("name", "goalmap") if src == "-" else Path(src).stem
    # --summary / -s ：スマホ1枚カード（週次スクショ共有用）を出力
    summary = "--summary" in opts or "-s" in opts
    renderer = build_summary_svg if summary else build_svg

    out_base = None
    if "-o" in opts:
        out_base = opts[opts.index("-o") + 1]
    if out_base is None:
        out_dir = Path(__file__).parent / "out"
        out_dir.mkdir(exist_ok=True)
        out_base = str(out_dir / (stem + "_1枚" if summary else stem))

    svg = renderer(data)
    svg_path = Path(str(out_base) + ".svg")
    svg_path.write_text(svg, encoding="utf-8")
    print(f"wrote {svg_path}")

    try:
        import cairosvg  # type: ignore
        png_path = Path(str(out_base) + ".png")
        svg_raster = renderer(data, font=FONT_RASTER)  # CJKフォントを先頭にした版
        cairosvg.svg2png(bytestring=svg_raster.encode("utf-8"), write_to=str(png_path), scale=3)
        print(f"wrote {png_path}")
    except Exception as e:  # noqa: BLE001
        print(f"(PNG skipped: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
