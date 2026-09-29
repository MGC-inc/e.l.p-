"""PDF生成の共通部品（フォント登録・色・判定ラベルの色分け）。

CJKフォントはreportlab組み込みのCIDフォントを使う（外部フォントファイル不要。
GitHub Actions等どの環境でも`pip install reportlab`だけで日本語が出る）。
"""
from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

FONT_GOTHIC = "HeiseiKakuGo-W5"  # 見出し向け（ゴシック体）
FONT_MINCHO = "HeiseiMin-W3"     # 本文向け（明朝体。日本語PDFで読みやすい）

_REGISTERED = False


def register_fonts() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    pdfmetrics.registerFont(UnicodeCIDFont(FONT_GOTHIC))
    pdfmetrics.registerFont(UnicodeCIDFont(FONT_MINCHO))
    _REGISTERED = True


# 成果物A（代理店別配信PDF）: A4縦
PAGE_A4_PORTRAIT = A4
# 成果物B（全社MT画面共有用）: 16:9横（PowerPointワイド画面と同じ13.33in x 7.5in）
PAGE_WIDE_16_9 = (960, 540)

GOLD = colors.HexColor("#C9A227")
INK = colors.HexColor("#1F2933")
SUB = colors.HexColor("#67707A")
RED = colors.HexColor("#D64545")
YELLOW = colors.HexColor("#E8A83C")
GREEN = colors.HexColor("#2E9E6D")
GRAY = colors.HexColor("#9AA5B1")
LIGHT_BG = colors.HexColor("#F2F4F6")

# aggregate.py の判定ラベル文字列 → 表示色
JUDGMENT_COLOR = {
    "大幅に低い": RED,
    "やや低い": YELLOW,
    "平均": SUB,
    "好調": GREEN,
    "母数少(参考)": GRAY,
    "要注意": RED,
    "やや多い": YELLOW,
    "通常": SUB,
}


def judgment_color(label: str | None):
    if label is None:
        return GRAY
    return JUDGMENT_COLOR.get(label, SUB)


def base_styles() -> dict[str, ParagraphStyle]:
    register_fonts()
    return {
        "title": ParagraphStyle("title", fontName=FONT_GOTHIC, fontSize=20, leading=26, textColor=INK),
        "h2": ParagraphStyle("h2", fontName=FONT_GOTHIC, fontSize=14, leading=18, textColor=INK,
                              spaceBefore=6, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=FONT_MINCHO, fontSize=9.5, leading=13, textColor=INK),
        "small": ParagraphStyle("small", fontName=FONT_MINCHO, fontSize=8, leading=11, textColor=SUB),
        "cell": ParagraphStyle("cell", fontName=FONT_MINCHO, fontSize=8.5, leading=11, textColor=INK),
        "cell_head": ParagraphStyle("cell_head", fontName=FONT_GOTHIC, fontSize=8.5, leading=11,
                                     textColor=colors.white),
    }
