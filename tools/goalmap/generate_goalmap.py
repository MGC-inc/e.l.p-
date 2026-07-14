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
FONT = "Hiragino Sans, 'Noto Sans CJK JP', 'Yu Gothic', IPAGothic, sans-serif"
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


# ── 成長キャラ（鯉→龍・10段階のレベル制）──────────────────────
# アバターの姿＝本人の実力レベル（level 0-9・メンター評価）。タスク消化は
# 「進化ゲージ（餌）」を貯めるだけで姿は変えない。ゲージ満タン＝レベルアップ
# 判定待ち（ready＝光る）。studio（goalmap-studio.html）の birdMarkup と同一デザイン。
FORM_NAMES = ["たまご", "針子", "稚鯉", "若鯉", "錦鯉",
              "大錦鯉", "滝登り", "化龍", "青龍", "金龍"]


def member_level(d: dict) -> int:
    """本人の現在レベル（0-9）。level 未設定なら旧 startForm を見る。"""
    v = d.get("level", d.get("startForm", 0))
    try:
        v = int(float(v))
    except (TypeError, ValueError):
        v = 0
    return max(0, min(9, v))


def growth(d: dict) -> dict:
    per = []
    for p in d.get("phases", []):
        t = p.get("tasks", [])
        per.append((sum(1 for x in t if x.get("done")), len(t)))
    all_ = sum(tot for _, tot in per)
    done = sum(dn for dn, _ in per)
    rate = round(done / all_ * 100) if all_ else 0     # 今期ゴールの進化ゲージ（餌の割合）
    level = member_level(d)                            # 本人の現在レベル（メンター評価）
    form = level                                       # アバターは常にレベルそのもの
    ready = rate >= 100 and level < 9                  # ゲージ満タン＝レベルアップ判定待ち（光る）
    nxt = None
    for dn, tot in per:
        if not (tot > 0 and dn == tot):
            nxt = (dn, tot)
            break
    if nxt is None:
        nxt = per[-1] if per else (0, 0)
    cycle = max(1, int(d.get("cycle", 1) or 1))
    earned = cycle - 1                                 # これまでに達成した今期ゴールの数
    return {"rate": rate, "form": form, "level": level, "ready": ready,
            "allDone": done, "all": all_, "need": max(0, nxt[1] - nxt[0]),
            "phaseDone": nxt[0], "phaseTotal": nxt[1], "cycle": cycle, "earned": earned}


def star(cx: float, cy: float, r: float, fill: str) -> str:
    pts = [(0, -1), (0.24, -0.24), (1, 0), (0.24, 0.24),
           (0, 1), (-0.24, 0.24), (-1, 0), (-0.24, -0.24)]
    s = " ".join(f"{cx+x*r:.1f},{cy+y*r:.1f}" for x, y in pts)
    return f'<polygon points="{s}" fill="{fill}"/>'


def bird_markup(form: int, cx: float, cy: float, s: float,
                phase_done: int, gen: int = 0, ready: bool = False) -> str:
    """studio の birdMarkup と同一：原点で形を組み、最後に translate+scale で配置。"""
    gen = int(gen or 0)
    form = max(0, min(9, int(form)))

    def path(d, f=None, st=None, w=None, op=None):
        stroke = (f' stroke="{st}" stroke-width="{w or 1}" stroke-linecap="round" '
                  f'stroke-linejoin="round"') if st else ""
        opa = f' opacity="{op}"' if op else ""
        return f'<path d="{d}" fill="{f or "none"}"{stroke}{opa}/>'

    def circ(x, y, r, f, op=None):
        opa = f' opacity="{op}"' if op else ""
        return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{f}"{opa}/>'

    def ell(x, y, rx, ry, f, op=None):
        opa = f' opacity="{op}"' if op else ""
        return f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}" fill="{f}"{opa}/>'

    def lstar(x, y, r, f):
        pts = [(0, -1), (0.24, -0.24), (1, 0), (0.24, 0.24),
               (0, 1), (-0.24, 0.24), (-1, 0), (-0.24, -0.24)]
        p = " ".join(f"{x+a*r:.1f},{y+b*r:.1f}" for a, b in pts)
        return f'<polygon points="{p}" fill="{f}"/>'

    # ── 写実寄りの鯉（横向き・右が頭）。紡錘形の胴＋二叉尾＋背びれ・胸びれ・鱗・えら・ひげ
    def koi(opt):
        g = opt["grad"]; fin = opt["fin"]; line = opt["line"]; sc = opt["scaleCol"]
        a = []
        a.append(path("M-14.8,-1 C-18.6,-4.8 -22.6,-8.2 -27.4,-9.4 C-24.9,-5.6 -24.3,-2.6 -23.9,0.2 C-24.3,2.9 -25.1,5.8 -27.6,9.6 C-22.7,8.4 -18.6,4.9 -14.8,1.4 Z", fin, line, 0.5, 0.95))
        for d in ["M-16,-0.4 C-19.5,-3 -22.4,-5.3 -25.2,-7", "M-16.4,0.4 C-19.8,0.3 -22,0.2 -24.4,0.2", "M-16,1.1 C-19.5,3.4 -22.3,5.5 -25.4,7.4"]:
            a.append(path(d, None, line, 0.5, 0.4))
        if opt.get("flow"):
            a.append(path("M-24,-6 C-26.8,-8.4 -29,-9 -30.6,-8.4", None, fin, 1.1, 0.7))
            a.append(path("M-24.6,6.4 C-27.4,8.8 -29.6,9.4 -30.9,8.9", None, fin, 1.1, 0.7))
        a.append(path("M-1.4,-7.7 C-4.6,-11.6 -9.6,-12.6 -13.2,-10.5 C-9.9,-9.2 -5.8,-8.2 -2.6,-7.8 Z", fin, line, 0.5, 0.95))
        a.append(path("M-4.2,7.9 C-5.8,10.5 -7.9,11.7 -9.7,11.8 C-8.1,9.8 -6.6,8.6 -5,7.8 Z", fin, line, 0.5, 0.9))
        a.append(path("M17.2,0.3 C16.6,-3.6 9.5,-7.8 1,-7.9 C-6.8,-8 -12.6,-4.6 -15.8,-1.4 L-15.8,2 C-12.6,4.9 -6.8,8.4 1,8.4 C9.5,8.3 16.6,4.2 17.2,0.3 Z", g, line, 0.6, None))
        for p in opt.get("patches", []):
            a.append(path(p[0], p[1], None, None, p[2] if len(p) > 2 else 0.95))
        rows = [(-4.6, [-9, -4.8, -0.6, 3.6]), (-1.6, [-11.4, -7.2, -3, 1.2, 5.4]),
                (1.5, [-11.4, -7.2, -3, 1.2, 5.4]), (4.4, [-9, -4.8, -0.6, 3.6])]
        for y, xs in rows:
            for x in xs:
                a.append(path(f"M{x},{y} q2.1,2.4 4.2,0", None, sc, 0.55, 0.22))
        a.append(ell(0, -5.2, 11, 2.4, "#1F2933", 0.06))
        a.append(ell(3, 4.6, 9.2, 2.8, "#FFFFFF", 0.28))
        a.append(path("M10.8,-4.6 C8.9,-1.4 8.9,1.6 10.6,4.4", None, line, 0.6, 0.4))
        a.append(path("M17.2,1.1 C16.4,1.8 15.4,2 14.5,1.9", None, line, 0.6, 0.6))
        a.append(circ(15.7, -1.7, 0.35, line, 0.5))
        a.append(path("M8.6,3.6 C6.8,7.7 4.1,10.1 1,10.9 C3.5,7 5.6,4.8 7.4,3.4 Z", fin, line, 0.5, 0.95))
        if opt.get("barbel", 0) >= 1:
            a.append(path("M15.6,2 C18.6,2.2 20.5,3.5 21.3,5.6", None, "#8A7A66", 0.8))
        if opt.get("barbel", 0) >= 2:
            a.append(path("M14.6,2.8 C16.7,3.4 17.9,4.5 18.3,6.1", None, "#8A7A66", 0.7))
        a.append(circ(12.3, -2.6, 1.8, "#C89A4A" if opt.get("eyeAmber") else "#5B6670"))
        a.append(circ(12.5, -2.5, 1.05, "#17110B"))
        a.append(circ(11.8, -3.1, 0.45, "#FFFFFF", 0.95))
        if opt.get("antlers"):
            a.append(path("M11.6,-5.4 C10.6,-9.6 8.2,-11.6 5.2,-12.2", None, "#A9700F", 1.3))
            a.append(path("M8.9,-10.4 C7.6,-11.8 6,-12.4 4.4,-12.4", None, "#A9700F", 0.9))
            a.append(path("M14.4,-4.8 C15,-9.2 17.4,-11.2 20.2,-11.6", None, "#A9700F", 1.3))
            a.append(path("M17,-10.2 C18.4,-11.6 20,-12.2 21.6,-12.2", None, "#A9700F", 0.9))
            a.append(path("M15.6,2 C20,2.4 22.8,4.4 23.8,7.4", None, "#A9700F", 0.9))
            for d in ["M-3,-8.2 C-4.6,-10.6 -6.8,-11.8 -9,-12", "M-7,-7.4 C-8.6,-9.6 -10.6,-10.6 -12.6,-10.8"]:
                a.append(path(d, None, "#E5651A", 1.2, 0.85))
        return "".join(a)

    # ── 龍（蛇体・長い口吻・枝角・たてがみ・三本爪。gold=金龍/False=青龍）
    def dragon(gold):
        g = "url(#drgGld)" if gold else "url(#drgBlu)"
        line = "#8A5A00" if gold else "#1E4E86"
        mane = "#E5651A" if gold else "#E8B23A"
        belly = "#F6E7BE" if gold else "#CFE6F2"
        a = []
        if gold:
            a.append(circ(0, -2, 27, "url(#glowG)"))
            for i in range(8):
                th = i * math.pi / 4; x2 = math.cos(th) * 26; y2 = -2 + math.sin(th) * 26
                a.append(f'<line x1="0" y1="-2" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#F2C230" '
                         f'stroke-width="{1 if i % 2 else 1.8}" stroke-linecap="round" opacity="0.3"/>')
            a.append('<circle cx="0" cy="-2" r="21" fill="none" stroke="#F2C230" stroke-width="0.9" opacity="0.4"/>')
            a.append(ell(-14, 20, 9, 3.4, "#FFFFFF", 0.55)); a.append(ell(13, 21.5, 7.5, 3, "#FFFFFF", 0.55))
        s1 = "M8,-17 C-3,-13.5 -8.5,-5.5 1,0"; s2 = "M1,0 C9.5,5 4,12 -4.5,13.8"; s3 = "M-4.5,13.8 C-11,15.2 -16.5,16.4 -22,19.5"
        for d, w in [(s1, 10.6), (s2, 8.4), (s3, 5.2)]:
            a.append(path(d, None, line, w + 1.5))
        for d, w in [(s1, 9.4), (s2, 7.2), (s3, 4.2)]:
            a.append(path(d, None, g, w))
        for d, w in [(s1, 4), (s2, 3.4), (s3, 2)]:
            a.append(f'<path d="{d}" fill="none" stroke="{belly}" stroke-width="{w}" '
                     f'stroke-dasharray="1.6 2" stroke-linecap="butt" opacity="0.85" transform="translate(0.9,1.5)"/>')
        for d, w in [(s1, 2), (s2, 1.8), (s3, 1.2)]:
            a.append(f'<path d="{d}" fill="none" stroke="{mane}" stroke-width="{w}" '
                     f'stroke-dasharray="1.2 2.6" stroke-linecap="butt" opacity="0.9" transform="translate(-0.9,-1.6)"/>')
        a.append(path("M4.6,-5.4 C3.2,-3 2.6,-1 3,1", None, line, 2.2))
        for d in ["M3,1 C1.4,2 0.2,2.4 -1,2.3", "M3,1 C2.5,2.6 2.2,3.8 2.4,4.9", "M3,1 C4.2,2.2 5,3 5.9,3.4"]:
            a.append(path(d, None, belly, 1))
        a.append(path("M-5.5,12.5 C-6.6,14 -7,15.4 -6.7,16.9", None, line, 1.9))
        for d in ["M-6.7,16.9 C-8,17.6 -9,17.8 -10,17.6", "M-6.7,16.9 C-7,18.2 -7.1,19.2 -6.9,20.1", "M-6.7,16.9 C-5.7,17.8 -5,18.4 -4.2,18.7"]:
            a.append(path(d, None, belly, 0.9))
        a.append(path("M-22,19.5 C-25.4,18 -27.6,18.4 -29.4,20.2 C-27.2,20.4 -25.9,21 -24.9,22.1 C-26.7,22.5 -28,23.5 -28.7,24.9 C-26.2,24.3 -23.8,23.6 -21.6,21.6 Z", mane, line, 0.5))
        a.append(path("M4.5,-18.5 C3.8,-23 6.6,-26.6 11.4,-27.4 C14.6,-27.9 17.8,-27.4 20.8,-26 L25.6,-23.6 C24,-22.5 22.2,-22.2 20.6,-22.4 L24.4,-19.8 C21.6,-17.6 17.6,-16.9 13.8,-17.8 C10,-18.7 7,-18.4 4.5,-18.5 Z", g, line, 0.7))
        a.append(path("M19,-21.8 C17,-20 14.4,-19.2 11.8,-19.4", None, line, 0.7, 0.7))
        a.append(circ(22.8, -24.6, 0.55, line))
        a.append(ell(12.4, -23.4, 1.5, 1.1, "#FFFFFF"))
        a.append(circ(12.8, -23.3, 0.75, "#17110B"))
        a.append(path("M10.2,-25.3 L15,-25.9", None, line, 0.9))
        a.append(path("M10.2,-27 C8.8,-31.6 5.6,-34 1.8,-34.6", None, line, 1.8))
        a.append(path("M6.4,-32.6 C4.8,-34 3,-34.6 1.2,-34.6", None, line, 1.1))
        a.append(path("M15.2,-27.3 C16,-32 19,-34.4 23.2,-34.9", None, line, 1.8))
        a.append(path("M19,-33.2 C20.6,-34.6 22.4,-35.2 24.2,-35.2", None, line, 1.1))
        for d in ["M5.8,-22 C0.4,-22 -3.6,-19.4 -6.4,-15.6", "M6.2,-20 C1.6,-19.4 -1.8,-17 -4,-13.6", "M7,-25.6 C3,-27 -0.6,-26.6 -3.6,-24.6"]:
            a.append(path(d, None, mane, 1.6, 0.9))
        a.append(path("M12,-19.2 C11,-17 9.6,-15.6 7.8,-14.8", None, mane, 1.2, 0.85))
        a.append(path("M23.4,-22.6 C28.2,-21.6 31,-18 31.8,-13.2", None, mane, 0.9))
        a.append(path("M22.6,-21 C26.4,-19.2 28.4,-16 28.8,-12", None, mane, 0.8))
        if gold:
            a.append(path("M8.2,-27.8 L9,-31.4 L10.8,-28.6 L12,-31.9 L13.2,-28.6 L15,-31.5 L15.6,-27.9 Z", "url(#drgGld)", "#8A5A00", 0.7))
            a.append(circ(11.9, -32.6, 1.1, "#E0352B"))
        a.append(lstar(-22 if gold else -20, -8, 1.9, "#FFF1B8"))
        a.append(lstar(24, 6, 1.7, "#FFE7A0" if gold else "#DCEFFA"))
        if gold:
            a.append(lstar(0, -1, 1.6, "#FFFFFF"))
        return "".join(a)

    o = []
    if form == 0:
        o.append(ell(0, 20, 11, 2.2, "#1F2933", 0.08))
        o.append(ell(0, 0, 15, 19, "url(#eggG)"))
        o.append('<ellipse cx="0" cy="0" rx="15" ry="19" fill="none" stroke="#E2D2A0" stroke-width="1.3"/>')
        o.append(path("M-7.5,-9 C-9.5,-4 -9.5,3 -7.5,8", None, "#FFFFFF", 2.2, 0.55))
        o.append(circ(-4.5, -3, 1.4, "#E6D3A0")); o.append(circ(5, 5, 1.2, "#E6D3A0")); o.append(circ(1, 11, 1.1, "#E6D3A0"))
        if phase_done > 0:
            o.append(path("M-6,-7 L-2,-3 L-6,1 L-1,5", None, "#C9B27A", 1.5))
    elif form == 1:
        o.append('<g transform="scale(1.15)">')
        o.append(path("M6.5,0 C5.5,-2 2.2,-3 -1,-2.6 C-4.2,-2.2 -6.8,-1 -8.6,0 C-6.8,1 -4.2,2.2 -1,2.6 C2.2,3 5.5,2 6.5,0 Z", "#CFE8F4", "#9FC4D8", 0.5, 0.9))
        o.append(path("M-8.4,0 C-10.2,-1.6 -11.6,-2.2 -12.8,-2.2 C-11.9,-0.9 -11.6,0 -11.5,0 C-11.6,0 -11.9,0.9 -12.8,2.2 C-11.6,2.2 -10.2,1.6 -8.4,0 Z", "#B9DCEE", None, None, 0.85))
        o.append(path("M-7.5,0 L4,0", None, "#9FC4D8", 0.5, 0.8))
        o.append(circ(0.4, 1, 1.5, "#F5D9A8", 0.9))
        o.append(circ(3.9, -0.5, 1.15, "#17110B")); o.append(circ(3.6, -0.8, 0.4, "#FFFFFF"))
        o.append('</g>')
    elif form == 2:
        o.append('<g transform="scale(0.62)">' + koi({"grad": "url(#fishBlu)", "fin": "#6E9EBB", "line": "#4E6E86", "scaleCol": "#3E6E8E", "barbel": 1}) + '</g>')
    elif form == 3:
        o.append('<g transform="scale(0.8)">' + koi({"grad": "url(#fishBlu)", "fin": "#6E9EBB", "line": "#4E6E86", "scaleCol": "#3E6E8E", "barbel": 2, "patches": [["M9.6,0.6 C10.8,1.4 11.4,2.6 11.2,3.8 C10,4.2 8.8,3.8 8,2.8 C8.2,1.8 8.8,1 9.6,0.6 Z", "#D8503A", 0.7]]}) + '</g>')
    elif form == 4:
        o.append('<g transform="scale(0.94)">' + koi({"grad": "url(#fishWht)", "fin": "#D9DEE3", "line": "#9AA3AD", "scaleCol": "#B9C1C8", "barbel": 2, "patches": [
            ["M4,-7.6 C8.6,-7.2 12,-5 13.4,-2.4 C10.6,-0.8 6.8,-0.6 3.4,-2 C2,-4 2.4,-6.2 4,-7.6 Z", "#D8402C"],
            ["M-6,1.8 C-2.6,0.6 0.8,1.4 2.2,3.6 C0.6,6.2 -3,7.4 -6.4,6.6 C-7.6,5 -7.4,3.2 -6,1.8 Z", "#D8402C"],
            ["M-12.8,-3.4 C-10.4,-4.4 -8.2,-4 -7.2,-2.4 C-8.2,-0.8 -10.6,-0.4 -12.6,-1.2 C-13.2,-2 -13.2,-2.8 -12.8,-3.4 Z", "#D8402C"]]}) + '</g>')
        o.append(lstar(18, -13, 2.2, "#F7E9B0"))
    elif form == 5:
        o.append('<g transform="scale(1.02)">' + koi({"grad": "url(#fishWht)", "fin": "#E8C05A", "line": "#A98A3F", "scaleCol": "#B9C1C8", "barbel": 2, "flow": True, "patches": [
            ["M4,-7.6 C8.6,-7.2 12,-5 13.4,-2.4 C10.6,-0.8 6.8,-0.6 3.4,-2 C2,-4 2.4,-6.2 4,-7.6 Z", "#D8402C"],
            ["M-6,1.8 C-2.6,0.6 0.8,1.4 2.2,3.6 C0.6,6.2 -3,7.4 -6.4,6.6 C-7.6,5 -7.4,3.2 -6,1.8 Z", "#E8862B"],
            ["M-12.8,-3.4 C-10.4,-4.4 -8.2,-4 -7.2,-2.4 C-8.2,-0.8 -10.6,-0.4 -12.6,-1.2 C-13.2,-2 -13.2,-2.8 -12.8,-3.4 Z", "#D8402C"],
            ["M-2,-6.8 C0.4,-6.4 1.8,-5.2 1.6,-3.8 C0,-3.2 -2.4,-3.6 -3.6,-4.8 C-3.2,-5.8 -2.8,-6.4 -2,-6.8 Z", "#2E2A26", 0.85]]}) + '</g>')
        o.append(lstar(19, -14, 2.4, "#F7E9B0"))
    elif form == 6:
        o.append(path("M-19,-26 C-16,-10 -19,6 -15,22", None, "#BFE0F2", 6, 0.5))
        o.append(path("M-12,-28 C-10,-12 -12.6,4 -9.6,20", None, "#CFE8F6", 4.4, 0.45))
        for b in [(-15, 20, 3.2), (-8, 22.5, 2.5), (-19.5, 23, 2.1), (-3, 21, 1.7)]:
            o.append(circ(b[0], b[1], b[2], "#DDF0FA", 0.85))
        o.append('<g transform="rotate(-42) scale(0.9)">' + koi({"grad": "url(#fishGld)", "fin": "#D89020", "line": "#A9700F", "scaleCol": "#B87A12", "barbel": 2, "eyeAmber": True, "patches": [["M2,-7 C5.8,-6.6 8.6,-4.8 9.8,-2.6 C7.4,-1.2 4.2,-1 1.4,-2.2 C0.2,-3.9 0.6,-5.8 2,-7 Z", "#D8402C", 0.9]]}) + '</g>')
        o.append(lstar(17, -18, 2.6, "#FFF3D6"))
    elif form == 7:
        o.append('<g transform="rotate(-50) scale(0.9)">' + koi({"grad": "url(#fishGld)", "fin": "#D89020", "line": "#A9700F", "scaleCol": "#B87A12", "barbel": 2, "eyeAmber": True, "antlers": True, "patches": [["M2,-7 C5.8,-6.6 8.6,-4.8 9.8,-2.6 C7.4,-1.2 4.2,-1 1.4,-2.2 C0.2,-3.9 0.6,-5.8 2,-7 Z", "#D8402C", 0.9]]}) + '</g>')
        o.append(ell(-9, 21, 8, 2.8, "#FFFFFF", 0.5))
        o.append(lstar(15, -19, 2.4, "#FFF3D6")); o.append(lstar(-19, -6, 1.8, "#FFE7A0"))
    elif form == 8:
        o.append('<g transform="scale(0.86)">' + dragon(False) + '</g>')
    else:
        o.append('<g transform="scale(0.86)">' + dragon(True) + '</g>')

    if gen >= 2:
        ch = {0: (0, 3, 6.2), 1: (0, -6.5, 3), 2: (-1, 0, 4), 3: (-1, 0, 4.6), 4: (-1, 0, 5),
              5: (-1, 0, 5.2), 6: (3, 3, 4.6), 7: (4, 4, 4.6), 8: (-2, 6, 4.8), 9: (-2, 6, 4.8)}.get(form, (0, 0, 5))
        chx, chy, cr = ch
        o.append(circ(chx, chy, cr, "#7A3E00", 0.92))
        o.append(f'<circle cx="{chx}" cy="{chy}" r="{cr}" fill="none" stroke="#F2C230" stroke-width="0.9"/>')
        o.append(f'<text x="{chx}" y="{chy+cr*0.55:.1f}" text-anchor="middle" '
                 f'font-size="{cr*1.5:.1f}" font-weight="800" fill="#FFF3D6">{gen}</text>')
    if ready:
        o.insert(0, circ(0, 0, 25, "url(#glowG)", 0.9))
        o.append('<circle cx="0" cy="0" r="24" fill="none" stroke="#F7C531" stroke-width="1.4" stroke-dasharray="3 3" opacity="0.9"/>')
        o.append(lstar(0, -25, 3, "#FFD23F"))
        # ⬆（フォント非依存のため三角形で描画）
        o.append('<polygon points="0,-26.6 1.7,-24.6 0.6,-24.6 0.6,-23.2 -0.6,-23.2 -0.6,-24.6 -1.7,-24.6" fill="#B7791F"/>')
    return f'<g transform="translate({cx},{cy}) scale({s})">{"".join(o)}</g>'


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
        # ── 鯉→龍アバター用（studio SVG_DEFS と同一）
        '<linearGradient id="fishBlu" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#6FA9CC"/><stop offset="0.55" stop-color="#A8CFE3"/>'
        '<stop offset="1" stop-color="#E4F1F7"/></linearGradient>'
        '<linearGradient id="fishWht" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#FFFFFF"/><stop offset="0.6" stop-color="#F2F4F5"/>'
        '<stop offset="1" stop-color="#D8DFE4"/></linearGradient>'
        '<linearGradient id="fishGld" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#F8D25C"/><stop offset="0.55" stop-color="#EFAF2E"/>'
        '<stop offset="1" stop-color="#D88A15"/></linearGradient>'
        '<linearGradient id="drgBlu" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#55D0CF"/><stop offset="0.5" stop-color="#2E86C8"/>'
        '<stop offset="1" stop-color="#4353C0"/></linearGradient>'
        '<linearGradient id="drgGld" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#F9DC74"/><stop offset="0.45" stop-color="#EEB53A"/>'
        '<stop offset="1" stop-color="#B87A12"/></linearGradient>'
        '<linearGradient id="eggG" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#FFF9E8"/><stop offset="1" stop-color="#EFD9A8"/></linearGradient>'
        '<radialGradient id="glowG">'
        '<stop offset="0" stop-color="#FFE9A0" stop-opacity="0.75"/>'
        '<stop offset="1" stop-color="#FFE9A0" stop-opacity="0"/></radialGradient>'
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
                           g["cycle"] if g["cycle"] >= 2 else 0, g["ready"]))
    # アバターの姿＝本人のレベル。タスク消化は進化ゲージ（餌）＝満タンで判定待ち
    out.append(
        f'<text x="{tx}" y="{ccy-14}" font-size="13" font-weight="700" '
        f'fill="#B7791F">Lv.{g["level"]} {FORM_NAMES[g["form"]]}</text>'
    )
    out.append(
        f'<text x="{tx}" y="{ccy+3}" font-size="12" font-weight="700" '
        f'fill="{"#C2410C" if g["ready"] else C_INK}">進化ゲージ {g["rate"]}%</text>'
    )
    if g["ready"]:
        sub = "🔥 レベルアップ判定待ち！"
    elif g["level"] >= 9:
        sub = "最高到達 金龍"
    else:
        sub = f'あと{g["need"]}コで満タン（餌 {g["phaseDone"]}/{g["phaseTotal"]}）'
    out.append(
        f'<text x="{tx}" y="{ccy+20}" font-size="11" fill="{C_SUB}">{esc(sub)}</text>'
    )
    # 周回（今期ゴールを何度達成したか）— 2周目以降で表示
    if g["cycle"] >= 2:
        out.append(
            f'<text x="{tx}" y="{ccy+37}" font-size="11" font-weight="700" '
            f'fill="#B7791F">{g["cycle"]}周目・達成 {g["earned"]}回</text>'
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
    # ── 鯉→龍アバター用（studio SVG_DEFS と同一）
    '<linearGradient id="fishBlu" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#6FA9CC"/><stop offset="0.55" stop-color="#A8CFE3"/>'
    '<stop offset="1" stop-color="#E4F1F7"/></linearGradient>'
    '<linearGradient id="fishWht" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#FFFFFF"/><stop offset="0.6" stop-color="#F2F4F5"/>'
    '<stop offset="1" stop-color="#D8DFE4"/></linearGradient>'
    '<linearGradient id="fishGld" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#F8D25C"/><stop offset="0.55" stop-color="#EFAF2E"/>'
    '<stop offset="1" stop-color="#D88A15"/></linearGradient>'
    '<linearGradient id="drgBlu" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#55D0CF"/><stop offset="0.5" stop-color="#2E86C8"/>'
    '<stop offset="1" stop-color="#4353C0"/></linearGradient>'
    '<linearGradient id="drgGld" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#F9DC74"/><stop offset="0.45" stop-color="#EEB53A"/>'
    '<stop offset="1" stop-color="#B87A12"/></linearGradient>'
    '<linearGradient id="eggG" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#FFF9E8"/><stop offset="1" stop-color="#EFD9A8"/></linearGradient>'
    '<radialGradient id="glowG">'
    '<stop offset="0" stop-color="#FFE9A0" stop-opacity="0.75"/>'
    '<stop offset="1" stop-color="#FFE9A0" stop-opacity="0"/></radialGradient>'
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
    o.append(bird_markup(g["form"], acx, acy, 1.3, g["phaseDone"],
                         g["cycle"] if g["cycle"] >= 2 else 0, g["ready"]))
    cap = f'Lv.{g["level"]} {FORM_NAMES[g["form"]]}' + ("・判定待ち" if g["ready"] else "")
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
