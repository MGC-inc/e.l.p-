#!/usr/bin/env python3
"""鯉→龍 成長アイコン・ジェネレーター（10段階・Lv0〜Lv9）

育成ロードマップ（鯉→龍）の成長ステージ Lv.0〜Lv.9 を、1匹の生き物が
たまご → 稚鯉 → 錦鯉 → 滝登り → 化龍 → 金龍 と進化していくSVG/PNGで出力する。

**アバターの絵は goalmap-studio.html / generate_goalmap.py と完全に同一**
（generate_goalmap.bird_markup を再利用）。ここではその1体を枠付きカードに
並べるだけなので、デザインを変えたい時は studio 側の birdMarkup を直せば全部に反映される。

使い方:
    python3 tools/goalmap/koi_dragon_icons.py          # out/koi_dragon/ に10枚のSVG(.png)
    python3 tools/goalmap/koi_dragon_icons.py --sheet  # 一覧シートも出す
"""
from __future__ import annotations

import argparse
from pathlib import Path

from generate_goalmap import GRAD_DEFS, FORM_NAMES, bird_markup

INK = "#1F2933"
VIEW = 128


def build_stage_svg(level: int) -> str:
    label = FORM_NAMES[level]
    # 龍（Lv8-9）は縦に大きいので少し小さめ・少し上に置く
    scale = 1.15 if level < 8 else 1.05
    cy = VIEW / 2 + (6 if level < 8 else 2)
    avatar = bird_markup(level, VIEW / 2, cy, scale, 1, 0, False)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW} {VIEW}" '
        f'width="{VIEW}" height="{VIEW}" '
        f'font-family="IPAGothic, Hiragino Sans, Noto Sans CJK JP, sans-serif">'
        f'{GRAD_DEFS}'
        f'<rect x="1" y="1" width="{VIEW-2}" height="{VIEW-2}" rx="20" '
        f'fill="#FFFFFF" stroke="#E3E7EB" stroke-width="2"/>'
        f'{avatar}'
        f'<text x="{VIEW/2}" y="{VIEW-9}" text-anchor="middle" '
        f'font-size="12" font-weight="700" fill="{INK}">Lv.{level} {label}</text>'
        f'</svg>'
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sheet", action="store_true", help="10段階の一覧シートも出力")
    args = ap.parse_args()

    out = Path(__file__).resolve().parent / "out" / "koi_dragon"
    out.mkdir(parents=True, exist_ok=True)

    try:
        import cairosvg
    except ImportError:
        cairosvg = None

    for level in range(10):
        label = FORM_NAMES[level]
        svg = build_stage_svg(level)
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
        cols = 5
        rows = 2
        cell = VIEW + 8
        W, H = cell * cols, cell * rows
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'font-family="IPAGothic, Hiragino Sans, Noto Sans CJK JP, sans-serif">'
        ]
        for level in range(10):
            r, c = divmod(level, cols)
            inner = build_stage_svg(level)
            inner = inner.split(">", 1)[1].rsplit("</svg>", 1)[0]
            parts.append(
                f'<g transform="translate({c*cell+4},{r*cell+4})"><svg viewBox="0 0 {VIEW} {VIEW}" '
                f'width="{VIEW}" height="{VIEW}" '
                f'font-family="IPAGothic, Hiragino Sans, Noto Sans CJK JP, sans-serif">{inner}</svg></g>'
            )
        parts.append("</svg>")
        sheet = "".join(parts)
        (out / "_sheet.svg").write_text(sheet, encoding="utf-8")
        if cairosvg:
            cairosvg.svg2png(
                bytestring=sheet.encode("utf-8"),
                write_to=str(out / "_sheet.png"),
                output_width=W * 2, output_height=H * 2,
            )
        print("wrote _sheet")


if __name__ == "__main__":
    main()
