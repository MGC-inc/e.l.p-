#!/usr/bin/env python3
"""週次の1枚カードを全員分まとめて生成（LINE等の一括共有用）。

members/*.json（_template を除く）から、スマホ縦サイズの「1枚カード」
（ゴール・小アバター・現在地・今週の最優先・全タスク✓）を
out/summary/<名前>.{svg,png} に書き出す。

    python tools/goalmap/summary_cards.py            # 全員
    python tools/goalmap/summary_cards.py 岡野 宮腰    # 指定メンバーのみ

studio(goalmap-studio.html) の「📱 1枚表示 → PNG出力」と同じ1枚。
PNGは cairosvg があれば出力（pip install cairosvg / fonts-noto-cjk）。
"""
import sys
from pathlib import Path

import generate_goalmap as G

HERE = Path(__file__).parent
MEMBERS = HERE / "members"
OUT = HERE / "out" / "summary"


def main(argv: list[str]) -> int:
    names = [a for a in argv[1:] if not a.startswith("-")]
    files = ([MEMBERS / f"{n}.json" for n in names] if names
             else sorted(p for p in MEMBERS.glob("*.json") if not p.name.startswith("_")))
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        import cairosvg  # type: ignore
    except Exception:  # noqa: BLE001
        cairosvg = None

    for f in files:
        if not f.exists():
            print(f"(skip: {f} が見つかりません)")
            continue
        d = G.load_member_data(str(f))
        stem = d.get("name", f.stem)
        base = OUT / stem
        base.with_suffix(".svg").write_text(G.build_summary_svg(d), encoding="utf-8")
        print(f"wrote {base}.svg")
        if cairosvg:
            cairosvg.svg2png(bytestring=G.build_summary_svg(d, font=G.FONT_RASTER).encode("utf-8"),
                             write_to=str(base.with_suffix(".png")), scale=2)
            print(f"wrote {base}.png")
    print(f"\n→ out/summary/ に {len(files)} 名分。LINE等でそのまま共有できます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
