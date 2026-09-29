"""成果物B: 全社ミーティング画面共有用PDF（16:9横×2枚。今川さんのみに送る）。

1枚目: 全社結果＋月進捗（現時点の目安線）＋週進捗
2枚目: 代理店ランキング（1人当たり）＋率の比較表
文字は大きく（Google Meet共有で読めることを優先）。
"""
from __future__ import annotations

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

from .aggregate import build_ranking
from .models import MemberWeekStats
from .pdf_common import GOLD, GRAY, INK, LIGHT_BG, PAGE_WIDE_16_9, register_fonts

MARGIN = 20 * mm


def _big_styles() -> dict[str, ParagraphStyle]:
    register_fonts()
    return {
        "title": ParagraphStyle("mtitle", fontName="HeiseiKakuGo-W5", fontSize=30, leading=36, textColor=INK),
        "h2": ParagraphStyle("mh2", fontName="HeiseiKakuGo-W5", fontSize=20, leading=26, textColor=INK,
                              spaceBefore=6, spaceAfter=8),
        "body": ParagraphStyle("mbody", fontName="HeiseiMin-W3", fontSize=16, leading=22, textColor=INK),
    }


def _table_style(header_rows: int = 1) -> TableStyle:
    return TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "HeiseiMin-W3"),
        ("FONTSIZE", (0, 0), (-1, -1), 15),
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), INK),
        ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), colors.white),
        ("FONTNAME", (0, 0), (-1, header_rows - 1), "HeiseiKakuGo-W5"),
        ("GRID", (0, 0), (-1, -1), 0.75, GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ])


def _page1(styles, week_start: str, week_end: str, all_members: list[MemberWeekStats],
           month_target_revenue: float | None, week_target_revenue: float | None) -> list:
    flow = [Paragraph(f"全社週次MT（{week_start}〜{week_end}）", styles["title"]), Spacer(1, 8 * mm)]

    month_revenue = sum(m.month_revenue for m in all_members)
    week_revenue = sum(m.revenue for m in all_members)
    month_win = sum(m.month_win for m in all_members)
    week_win = sum(m.win for m in all_members)
    week_appt = sum(m.appt for m in all_members)

    rows = [["区分", "実績", "目標", "達成率"]]
    if month_target_revenue:
        pct = 100 * month_revenue / month_target_revenue
        rows.append(["月実績（実質売上・目安線）", f"{month_revenue:.0f}万円", f"{month_target_revenue:.0f}万円",
                     f"{pct:.0f}%"])
    if week_target_revenue:
        pct = 100 * week_revenue / week_target_revenue
        rows.append(["週実績（実質売上）", f"{week_revenue:.0f}万円", f"{week_target_revenue:.0f}万円",
                     f"{pct:.0f}%"])
    rows.append(["週アポ数", f"{week_appt:.0f}件", "-", "-"])
    rows.append(["週契約数", f"{week_win}件", "-", "-"])
    rows.append(["月契約数（MTD）", f"{month_win}件", "-", "-"])

    t = Table(rows, colWidths=[75 * mm, 55 * mm, 55 * mm, 45 * mm])
    t.setStyle(_table_style())
    flow.append(t)
    flow.append(PageBreak())
    return flow


def _page2(styles, all_members: list[MemberWeekStats]) -> list:
    flow = [Paragraph("代理店ランキング（1人当たり）", styles["title"]), Spacer(1, 6 * mm)]

    working_by_agency: dict[str, int] = {}
    for m in all_members:
        working_by_agency.setdefault(m.agency, 0)
        if m.is_working_this_week:
            working_by_agency[m.agency] += 1

    rows = [["代理店", "契約(1人当たり)", "アポ(1人当たり)", "稼働人数"]]
    win_ranking = {a: v for _, a, v in build_ranking(all_members, "win")}
    appt_ranking = {a: v for _, a, v in build_ranking(all_members, "appt")}
    ranked_by_win = build_ranking(all_members, "win")
    for rank, agency, value in ranked_by_win:
        # CID日本語フォントに絵文字グリフが無いため🥇等は使わない。1位は金色背景のみで示す
        rows.append([agency, f"{value:.1f}", f"{appt_ranking.get(agency, 0):.1f}",
                     str(working_by_agency.get(agency, 0))])

    t = Table(rows, colWidths=[75 * mm, 55 * mm, 55 * mm, 45 * mm])
    style = _table_style()
    for i, (rank, _agency, _value) in enumerate(ranked_by_win, start=1):
        if rank == 1:
            style.add("BACKGROUND", (0, i), (-1, i), GOLD)
    t.setStyle(style)
    flow.append(t)
    return flow


def build_meeting_pdf(
    out_path: str,
    week_start: str,
    week_end: str,
    all_members: list[MemberWeekStats],
    month_target_revenue: float | None = None,
    week_target_revenue: float | None = None,
) -> int:
    """今川さん専用の全社MT用PDFを生成し、ページ数を返す（想定=2）。"""
    styles = _big_styles()
    doc = SimpleDocTemplate(out_path, pagesize=PAGE_WIDE_16_9,
                             leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN)
    flow: list = []
    flow += _page1(styles, week_start, week_end, all_members, month_target_revenue, week_target_revenue)
    flow += _page2(styles, all_members)
    doc.build(flow)
    return doc.page
