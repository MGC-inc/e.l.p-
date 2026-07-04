#!/usr/bin/env python3
"""チーム達成率の棒グラフ（横棒）を1枚のPNG/SVGで出力する。

members/*.json（_template除く）から各人の達成率（完了タスク数÷全タスク数）を
計算し、達成率の高い順に横棒グラフで描く。MT投影・LINE共有・Notion貼付用。

    python tools/goalmap/team_chart.py            # 全員 → out/チーム達成率.png
    python tools/goalmap/team_chart.py 岡野 宮腰    # 指定メンバーのみ

Notionの自動計算列(rollup)はAPIから棒グラフ化できないため、図解と同じ
レンダラ方針で画像化している（毎週作り直せる）。
"""
import sys
from datetime import date
from pathlib import Path

import generate_goalmap as G

HERE = Path(__file__).parent
MEMBERS = HERE / "members"


def member_rate(d: dict) -> int:
    tasks = [t for p in d.get("phases", []) for t in p.get("tasks", [])]
    if not tasks:
        return 0
    return round(sum(1 for t in tasks if t.get("done")) / len(tasks) * 100)


# 現在ステージ→帯の色（①灰→⑤緑）。達成率0%でも段階が分かるよう色分け
STAGE_COLOR = {1: "#9AA3AD", 2: "#3DA0E8", 3: "#EF9F27", 4: "#7A5BE0", 5: "#1D9E75"}


def build_chart_svg(rows: list[dict], font: str = G.FONT) -> str:
    W = 720
    top, rowH, gap = 64, 30, 14
    label_w, pad = 78, 24
    bar_x = pad + label_w + 10
    bar_w = W - bar_x - 70
    H = top + len(rows) * (rowH + gap) + 24
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'font-family="{font}" width="{W}" height="{H}">']
    o.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#fff"/>')
    o.append(f'<text x="{pad}" y="34" font-size="20" font-weight="700" '
             f'fill="{G.C_INK}">チーム達成率</text>')
    o.append(f'<text x="{W-pad}" y="34" text-anchor="end" font-size="13" '
             f'fill="{G.C_SUB}">{date.today().isoformat()}</text>')
    # 目盛り（0/50/100）
    for frac in (0, 0.5, 1.0):
        gx = bar_x + bar_w * frac
        o.append(f'<line x1="{gx:.1f}" y1="{top-8}" x2="{gx:.1f}" y2="{H-16}" '
                 f'stroke="#EAEDF0" stroke-width="1"/>')
        o.append(f'<text x="{gx:.1f}" y="{top-12}" text-anchor="middle" '
                 f'font-size="10" fill="{G.C_SUB}">{int(frac*100)}</text>')
    y = top
    for r in rows:
        rate, stage = r["rate"], r["stage"]
        cy = y + rowH / 2
        color = STAGE_COLOR.get(stage, G.C_DONE)
        o.append(f'<text x="{pad+label_w}" y="{cy+5}" text-anchor="end" '
                 f'font-size="14" font-weight="700" fill="{G.C_INK}">{G.esc(r["name"])}</text>')
        o.append(f'<rect x="{bar_x}" y="{y+4}" width="{bar_w}" height="{rowH-8}" '
                 f'rx="6" fill="{G.C_FUTURE_BG}"/>')
        fillw = max(2, round(bar_w * rate / 100)) if rate else 0
        if fillw:
            o.append(f'<rect x="{bar_x}" y="{y+4}" width="{fillw}" height="{rowH-8}" '
                     f'rx="6" fill="{color}"/>')
        o.append(f'<text x="{bar_x+bar_w+10}" y="{cy+5}" font-size="14" '
                 f'font-weight="700" fill="{color}">{rate}%</text>')
        o.append(f'<text x="{bar_x+6}" y="{cy+4}" font-size="10.5" '
                 f'fill="#fff" opacity="{0.95 if fillw>40 else 0}">{G.esc(r["stageName"])}</text>')
        y += rowH + gap
    o.append("</svg>")
    return "".join(o)


def main(argv: list[str]) -> int:
    names = [a for a in argv[1:] if not a.startswith("-")]
    files = ([MEMBERS / f"{n}.json" for n in names] if names
             else sorted(p for p in MEMBERS.glob("*.json") if not p.name.startswith("_")))
    rows = []
    for f in files:
        if not f.exists():
            print(f"(skip: {f})")
            continue
        d = G.load_member_data(str(f))
        st = int(d.get("currentStage", 1))
        rows.append({"name": d.get("name", f.stem), "rate": member_rate(d),
                     "stage": st, "stageName": G.STAGE_NAMES[st - 1]})
    rows.sort(key=lambda r: r["rate"], reverse=True)

    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)
    base = out_dir / "チーム達成率"
    base.with_suffix(".svg").write_text(build_chart_svg(rows), encoding="utf-8")
    print(f"wrote {base}.svg")
    try:
        import cairosvg  # type: ignore
        cairosvg.svg2png(bytestring=build_chart_svg(rows, font=G.FONT_RASTER).encode("utf-8"),
                         write_to=str(base.with_suffix(".png")), scale=3)
        print(f"wrote {base}.png")
    except Exception as e:  # noqa: BLE001
        print(f"(PNG skipped: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
