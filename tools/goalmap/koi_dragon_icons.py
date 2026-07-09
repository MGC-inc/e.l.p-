#!/usr/bin/env python3
"""鯉→龍 成長アイコン・ジェネレーター（9段階）

育成ロードマップ（鯉→龍）の成長ステージ Lv.1〜Lv.9 を、1匹の生き物が
稚鯉 → 錦鯉 → 滝登り → 若龍 → 天龍 と進化していくSVG/PNGで出力する。
generate_goalmap.py と同じく標準ライブラリ＋cairosvg(任意)で動く。

使い方:
    python3 tools/goalmap/koi_dragon_icons.py          # out/koi_dragon/ に9枚のSVG(.png)
    python3 tools/goalmap/koi_dragon_icons.py --sheet  # 一覧シートも出す

デザイン方針（HANDOFF）:
    Lv1-5 は鯉（魚体）。数=大鯉→質=錦鯉（模様が付く）→滝登り（斜めに跳ねる）。
    Lv6-9 は龍（蛇体＋角＋髭）。若龍→青龍→金龍→天龍（王冠＋瑞雲＋光背）。
    「持ち物」ではなく本体が育つ／一目でレベルが上がったと分かる、が要件。
"""
from __future__ import annotations

import argparse
from pathlib import Path

# 色（goalmap-studio.html / generate_goalmap.py のパレットに合わせる）
INK = "#1F2933"
SUB = "#67707A"
WATER = "#3DA0E8"
KOI_RED = "#E0352B"
KOI_ORANGE = "#F2870D"
GOLD = "#F2C230"
GOLD_DEEP = "#C8901A"

VIEW = 128

# ステージ定義（レベル, ラベル, 主色グラデ）
STAGES = [
    (1, "稚鯉", ["#C7CCD2", "#AEB4BC"]),
    (2, "若鯉", ["#7FC4F0", "#3DA0E8"]),
    (3, "大鯉", ["#4AA6E6", "#2E7BC4"]),
    (4, "錦鯉", ["#FFFFFF", "#F2F4F6"]),
    (5, "滝登り", ["#FF9A3D", "#F2870D"]),
    (6, "若龍", ["#37C9B0", "#2E8FD0"]),
    (7, "青龍", ["#3DA0E8", "#2E4BB0"]),
    (8, "金龍", ["#FFE259", "#F2A000"]),
    (9, "天龍", ["#FFF0A8", "#C8901A"]),
]


def _grad(gid: str, c0: str, c1: str) -> str:
    return (
        f'<linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{c0}"/>'
        f'<stop offset="1" stop-color="{c1}"/></linearGradient>'
    )


def _eye(cx: float, cy: float, r: float = 3.4) -> str:
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#FFFFFF"/>'
        f'<circle cx="{cx+0.5}" cy="{cy}" r="{r*0.62}" fill="{INK}"/>'
    )


def koi(level: int, gid: str, patterned: bool, leaping: bool) -> str:
    """鯉（魚体）を描く。level で体を少しずつ大きくする。"""
    scale = 0.72 + level * 0.05
    o = [f'<g transform="translate(64,66) scale({scale:.3f}) translate(-64,-64)">']
    if leaping:
        o = [f'<g transform="translate(64,64) rotate(-32) scale({scale:.3f}) translate(-64,-64)">']

    # 尾びれ（左）
    o.append(
        f'<path d="M44,64 C30,50 24,54 22,64 C24,74 30,78 44,64 Z" '
        f'fill="url(#{gid})" opacity="0.95"/>'
    )
    # 背びれ・腹びれ
    o.append(f'<path d="M70,44 C82,30 92,34 84,50 Z" fill="url(#{gid})" opacity="0.85"/>')
    o.append(f'<path d="M72,84 C82,96 92,92 86,78 Z" fill="url(#{gid})" opacity="0.85"/>')
    # 胴体（右に頭）
    o.append(
        f'<path d="M44,64 C52,44 78,40 96,52 C106,58 106,70 96,76 '
        f'C78,88 52,84 44,64 Z" fill="url(#{gid})" stroke="{SUB}" stroke-width="1"/>'
    )
    # ひげ（鯉のシンボル・成長するほど長く）
    wl = 4 + level * 1.5
    o.append(
        f'<path d="M99,58 q{wl},-2 {wl+3},3" fill="none" stroke="{SUB}" '
        f'stroke-width="1.4" stroke-linecap="round"/>'
    )
    # 錦鯉の模様（紅白）
    if patterned:
        o.append(f'<ellipse cx="66" cy="58" rx="8" ry="6" fill="{KOI_RED}" opacity="0.9"/>')
        o.append(f'<ellipse cx="82" cy="66" rx="6" ry="5" fill="{KOI_ORANGE}" opacity="0.9"/>')
        o.append(f'<circle cx="74" cy="70" r="3" fill="{KOI_RED}" opacity="0.8"/>')
    # 目
    o.append(_eye(93, 61))
    o.append("</g>")

    splash = ""
    if leaping:
        # 滝しぶき
        splash = (
            f'<g opacity="0.55" fill="{WATER}">'
            f'<circle cx="40" cy="98" r="5"/><circle cx="52" cy="104" r="4"/>'
            f'<circle cx="30" cy="106" r="3.5"/><circle cx="62" cy="100" r="3"/>'
            f'<path d="M24,120 q40,-14 80,0 v8 h-80 Z"/></g>'
        )
    return splash + "".join(o)


def dragon(level: int, gid: str, gold: bool, crown: bool, aura: bool) -> str:
    """龍（蛇体＋角＋髭）を描く。level が上がるほど装飾が増える。"""
    o = []
    if aura:
        # 光背（放射）＋瑞雲
        rays = []
        import math
        for i in range(12):
            a = i * math.pi / 6
            x2 = 64 + math.cos(a) * 58
            y2 = 64 + math.sin(a) * 58
            w = 2.4 if i % 2 == 0 else 1.2
            rays.append(
                f'<line x1="64" y1="64" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{GOLD}" stroke-width="{w}" stroke-linecap="round" opacity="0.28"/>'
            )
        o.append("<g>" + "".join(rays) + "</g>")
        o.append(f'<circle cx="64" cy="64" r="50" fill="{GOLD}" opacity="0.08"/>')
        # 瑞雲
        o.append(
            f'<g fill="#FFFFFF" opacity="0.5">'
            f'<ellipse cx="34" cy="104" rx="14" ry="6"/>'
            f'<ellipse cx="92" cy="106" rx="12" ry="5"/></g>'
        )

    # 蛇体（S字の太い帯）。level が高いほど長く見えるようカーブを深く。
    body = (
        f'<path d="M30,96 C24,78 44,74 52,80 C64,88 60,58 46,54 '
        f'C34,50 40,32 58,34 C76,36 82,50 96,44" '
        f'fill="none" stroke="url(#{gid})" stroke-width="13" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    o.append(body)
    # 背のたてがみ（ギザギザ）
    mane_col = GOLD if gold else "#FF6B3D"
    o.append(
        f'<path d="M52,80 l-4,-8 6,3 -3,-9 6,4 -2,-9 6,5" '
        f'fill="none" stroke="{mane_col}" stroke-width="2" '
        f'stroke-linejoin="round" opacity="0.9"/>'
    )
    # 頭（右上）
    hx, hy = 98, 42
    o.append(f'<circle cx="{hx}" cy="{hy}" r="12" fill="url(#{gid})" stroke="{SUB}" stroke-width="1"/>')
    # 角（2本・level>=7で枝角）
    o.append(
        f'<path d="M{hx-4},{hy-10} q-3,-10 -8,-12" fill="none" '
        f'stroke="{mane_col}" stroke-width="2.4" stroke-linecap="round"/>'
    )
    o.append(
        f'<path d="M{hx+3},{hy-11} q1,-10 6,-13" fill="none" '
        f'stroke="{mane_col}" stroke-width="2.4" stroke-linecap="round"/>'
    )
    if level >= 7:
        o.append(
            f'<path d="M{hx-9},{hy-19} q-4,-2 -7,-1 M{hx+11},{hy-20} q4,-2 7,0" '
            f'fill="none" stroke="{mane_col}" stroke-width="1.6" stroke-linecap="round"/>'
        )
    # 髭（長い2本）
    o.append(
        f'<path d="M{hx+9},{hy+2} q16,0 20,8 M{hx+9},{hy+5} q14,4 16,12" '
        f'fill="none" stroke="{SUB}" stroke-width="1.5" stroke-linecap="round"/>'
    )
    # 目
    o.append(_eye(hx + 3, hy - 1, 3.0))
    # 爪足（1本・level>=7で2本）
    o.append(
        f'<path d="M50,82 l-6,10 m0,-10 l0,11 m6,-11 l4,10" fill="none" '
        f'stroke="url(#{gid})" stroke-width="2.2" stroke-linecap="round"/>'
    )
    if level >= 7:
        o.append(
            f'<path d="M58,36 l-4,10 m4,-10 l1,11 m3,-11 l5,9" fill="none" '
            f'stroke="url(#{gid})" stroke-width="2" stroke-linecap="round"/>'
        )
    # 尾の炎風フレア
    o.append(f'<path d="M30,96 q-10,4 -14,-2 q8,0 10,-6 Z" fill="{mane_col}" opacity="0.8"/>')

    if crown:
        # 王冠（頭上）
        o.append(
            f'<path d="M{hx-9},{hy-9} l3,-8 3,5 3,-7 3,7 3,-5 3,8 Z" '
            f'fill="{GOLD}" stroke="{GOLD_DEEP}" stroke-width="1" stroke-linejoin="round"/>'
        )
        o.append(f'<circle cx="{hx}" cy="{hy-13}" r="1.6" fill="{KOI_RED}"/>')
    return "".join(o)


def build_stage_svg(level: int, label: str, colors: list[str]) -> str:
    gid = f"g{level}"
    defs = f"<defs>{_grad(gid, colors[0], colors[1])}</defs>"

    if level <= 5:
        body = koi(level, gid, patterned=(level >= 4), leaping=(level == 5))
    else:
        body = dragon(
            level, gid,
            gold=(level >= 8),
            crown=(level == 9),
            aura=(level >= 8),
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW} {VIEW}" '
        f'width="{VIEW}" height="{VIEW}">{defs}'
        f'<rect x="1" y="1" width="{VIEW-2}" height="{VIEW-2}" rx="20" '
        f'fill="#FFFFFF" stroke="#E3E7EB" stroke-width="2"/>'
        f'{body}'
        f'<text x="{VIEW/2}" y="{VIEW-9}" text-anchor="middle" '
        f'font-family="IPAGothic, Hiragino Sans, Noto Sans CJK JP, sans-serif" '
        f'font-size="12" font-weight="700" fill="{INK}">Lv.{level} {label}</text>'
        f'</svg>'
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sheet", action="store_true", help="9段階の一覧シートも出力")
    args = ap.parse_args()

    out = Path(__file__).resolve().parent / "out" / "koi_dragon"
    out.mkdir(parents=True, exist_ok=True)

    try:
        import cairosvg
    except ImportError:
        cairosvg = None

    for level, label, colors in STAGES:
        svg = build_stage_svg(level, label, colors)
        base = out / f"lv{level}_{label}"
        base.with_suffix(".svg").write_text(svg, encoding="utf-8")
        if cairosvg:
            cairosvg.svg2png(
                bytestring=svg.encode("utf-8"),
                write_to=str(base.with_suffix(".png")),
                output_width=256, output_height=256,
            )
        print(f"wrote lv{level}_{label}")

    if args.sheet:
        cols = 9
        cell = VIEW + 8
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {cell*cols} {cell}" width="{cell*cols}" height="{cell}">'
        ]
        for i, (level, label, colors) in enumerate(STAGES):
            inner = build_stage_svg(level, label, colors)
            inner = inner.split(">", 1)[1].rsplit("</svg>", 1)[0]
            parts.append(
                f'<g transform="translate({i*cell+4},4)"><svg viewBox="0 0 {VIEW} {VIEW}" '
                f'width="{VIEW}" height="{VIEW}">{inner}</svg></g>'
            )
        parts.append("</svg>")
        sheet = "".join(parts)
        (out / "_sheet.svg").write_text(sheet, encoding="utf-8")
        if cairosvg:
            cairosvg.svg2png(
                bytestring=sheet.encode("utf-8"),
                write_to=str(out / "_sheet.png"),
                output_width=cell * cols * 2, output_height=cell * 2,
            )
        print("wrote _sheet")


if __name__ == "__main__":
    main()
