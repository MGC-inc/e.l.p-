"""成果物A: 代理店別配信PDF（A4縦）。

構成（依頼4章のとおり）:
  表紙・進行 → ①全体結果と月・週の進捗 → ②代理店ランキング（全社分）
  → 個人シート（1人2ページ、アポインタ→クローザーの順） → 振り返りページ

共通ページ4枚（表紙／進捗／ランキング／振り返り）＋人数×2ページ、で必ず構成する
（振り返りは代理店1枚に全員分をまとめる。個人ごとに増やすとページ数の検算がずれるため）。
"""
from __future__ import annotations

from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.lib import colors

from .aggregate import build_auto_comment, build_metrics, build_ranking
from .models import ROLE_APPOINTER, ROLE_CLOSER, MemberWeekStats
from .pdf_common import GOLD, GRAY, INK, LIGHT_BG, PAGE_A4_PORTRAIT, base_styles, judgment_color

MARGIN = 16 * mm


def _table_style(header_rows: int = 1) -> TableStyle:
    return TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "HeiseiMin-W3"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), INK),
        ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), colors.white),
        ("FONTNAME", (0, 0), (-1, header_rows - 1), "HeiseiKakuGo-W5"),
        ("GRID", (0, 0), (-1, -1), 0.5, GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [colors.white, LIGHT_BG]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])


def _cover_page(styles, agency: str, week_start: str, week_end: str) -> list:
    return [
        Spacer(1, 60 * mm),
        Paragraph(f"週次MTスライド｜{agency}", styles["title"]),
        Spacer(1, 6 * mm),
        Paragraph(f"対象週: {week_start} 〜 {week_end}（実績日ベース）", styles["body"]),
        Spacer(1, 20 * mm),
        Paragraph("このMTの目的: 先週の実績を振り返り、今週やることを決める。", styles["body"]),
        PageBreak(),
    ]


def _judged_row(label: str, mv) -> list:
    if mv is None:
        return [label, "-", "-", "-", "-"]
    ratio_txt = f"{mv.ratio_pct:.0f}%" if mv.ratio_pct is not None else "-"
    judge_txt = mv.judgment or "-"
    return [label, f"{mv.self_value:.1f}{mv.unit}",
            f"{mv.baseline:.1f}{mv.unit}" if mv.baseline is not None else "-",
            ratio_txt, judge_txt]


def _progress_page(styles, all_members: list[MemberWeekStats], month_target_revenue: float | None,
                    week_target_revenue: float | None) -> list:
    flow = [Paragraph("① 全体結果と月・週の進捗", styles["h2"])]

    month_revenue = sum(m.month_revenue for m in all_members)
    week_revenue = sum(m.revenue for m in all_members)
    month_win = sum(m.month_win for m in all_members)
    week_win = sum(m.win for m in all_members)

    rows = [["区分", "実績", "目標", "達成率"]]
    if month_target_revenue:
        pct = 100 * month_revenue / month_target_revenue if month_target_revenue else 0
        rows.append(["月実績（実質売上）", f"{month_revenue:.0f}万円", f"{month_target_revenue:.0f}万円",
                     f"{pct:.0f}%"])
    if week_target_revenue:
        pct = 100 * week_revenue / week_target_revenue if week_target_revenue else 0
        rows.append(["週実績（実質売上）", f"{week_revenue:.0f}万円", f"{week_target_revenue:.0f}万円",
                     f"{pct:.0f}%"])
    rows.append(["月契約数", f"{month_win}件", "-", "-"])
    rows.append(["週契約数", f"{week_win}件", "-", "-"])

    t = Table(rows, colWidths=[45 * mm, 40 * mm, 40 * mm, 30 * mm])
    t.setStyle(_table_style())
    flow.append(t)
    flow.append(PageBreak())
    return flow


def _ranking_page(styles, all_members_all_agencies: list[MemberWeekStats]) -> list:
    flow = [Paragraph("② 代理店ランキング（1人当たり・全社分）", styles["h2"])]

    for label, metric in [("契約数", "win"), ("アポ数", "appt"), ("実質売上（万円）", "revenue")]:
        flow.append(Paragraph(label, styles["body"]))
        ranking = build_ranking(all_members_all_agencies, metric)
        rows = [["順位", "代理店", "稼働人数", "1人当たり"]]
        working_by_agency = {}
        for m in all_members_all_agencies:
            working_by_agency.setdefault(m.agency, 0)
            if m.is_working_this_week:
                working_by_agency[m.agency] += 1
        for rank, agency, value in ranking:
            # CID日本語フォントに絵文字グリフが無いため🥇等は使わない。1位は金色背景のみで示す
            mark = str(rank)
            rows.append([mark, agency, str(working_by_agency.get(agency, 0)), f"{value:.1f}"])
        t = Table(rows, colWidths=[15 * mm, 55 * mm, 25 * mm, 30 * mm])
        style = _table_style()
        for i, (rank, _agency, _value) in enumerate(ranking, start=1):
            if rank == 1:
                style.add("BACKGROUND", (0, i), (-1, i), GOLD)
        t.setStyle(style)
        flow.append(t)
        flow.append(Spacer(1, 4 * mm))

    flow.append(PageBreak())
    return flow


def _member_sheets(styles, member: MemberWeekStats, peers: list[MemberWeekStats]) -> list:
    metrics = build_metrics(member, peers)
    by_label = {mv.label: mv for mv in metrics}
    comments = build_auto_comment(metrics)

    # --- 1/2: 項目/自分/全体平均/平均比/判定 ---
    flow = [Paragraph(f"{member.member}（{member.agency}）", styles["h2"]),
            Paragraph("1/2 実績と判定", styles["small"])]

    activity_labels = ["訪問数", "在宅数", "対面数", "対象数", "対話数", "アポ数"]
    if member.role == ROLE_CLOSER:
        deal_labels = ["商談(自アポ)", "商談(他アポ)", "商談(計)", "契約(自アポ)", "契約(他アポ)", "契約(計)"]
    else:
        deal_labels = ["商談(計)", "契約(計)"]
    result_labels = ["クーリングオフ数", "審査落ち数", "実質売上"]
    rate_labels = ["在宅率(訪問→在宅)", "対面率(在宅→対面)", "対象率(対面→対象)", "対話率(対象→対話)",
                   "アポ率(対話→アポ)", "訪問→アポ通算", "商談→契約率(計)", "クーリングオフ率", "審査落ち率"]

    header = ["項目", "自分", "全体平均", "平均比", "判定"]
    for title, labels in [("活動量", activity_labels), ("商談・契約", deal_labels),
                           ("結果", result_labels), ("通過率", rate_labels)]:
        flow.append(Paragraph(title, styles["body"]))
        rows = [header] + [_judged_row(lbl, by_label.get(lbl)) for lbl in labels]
        t = Table(rows, colWidths=[45 * mm, 28 * mm, 28 * mm, 22 * mm, 27 * mm])
        style = _table_style()
        for r, lbl in enumerate(labels, start=1):
            mv = by_label.get(lbl)
            if mv is not None and mv.judgment is not None:
                style.add("TEXTCOLOR", (4, r), (4, r), judgment_color(mv.judgment))
        t.setStyle(style)
        flow.append(t)
        flow.append(Spacer(1, 2 * mm))

    flow.append(Paragraph("定量評価（自動）", styles["body"]))
    if comments:
        for c in comments:
            flow.append(Paragraph(c, styles["small"]))
    else:
        flow.append(Paragraph("特に低い項目はありません。", styles["small"]))
    flow.append(PageBreak())

    # --- 2/2: 失注振り返り＋アクションプラン＋今週の目標 ---
    flow.append(Paragraph(f"{member.member}（{member.agency}）", styles["h2"]))
    flow.append(Paragraph("2/2 振り返りとアクションプラン", styles["small"]))

    if member.role == ROLE_CLOSER:
        flow.append(Paragraph("直近の失注・保留の振り返り", styles["body"]))
        if member.recent_neck_reasons:
            rows = [["理由"]] + [[r] for r in member.recent_neck_reasons[:5]]
            t = Table(rows, colWidths=[150 * mm])
            t.setStyle(_table_style())
            flow.append(t)
        else:
            flow.append(Paragraph("対象なし。", styles["small"]))
        flow.append(Spacer(1, 4 * mm))

    flow.append(Paragraph("原因", styles["body"]))
    flow.append(Paragraph("（MTで記入）", styles["small"]))
    flow.append(Spacer(1, 3 * mm))
    flow.append(Paragraph("アクションプラン（いつ・誰と・何を）", styles["body"]))
    flow.append(Paragraph("（MTで記入）", styles["small"]))
    flow.append(Spacer(1, 3 * mm))
    flow.append(Paragraph("今週の定量目標", styles["body"]))
    flow.append(Paragraph("（MTで記入。重点項目は先週実績を参考に設定）", styles["small"]))
    flow.append(PageBreak())

    return flow


def _review_page(styles, members: list[MemberWeekStats], pdca_rows: list[dict] | None) -> list:
    flow = [Paragraph("振り返りページ（先週のアクション確認）", styles["h2"])]
    rows = [["メンバー", "先週の宣言", "今週目標", "週末結果", "差"]]
    pdca_by_member = {r["member"]: r for r in (pdca_rows or [])}
    for m in members:
        r = pdca_by_member.get(m.member)
        if r:
            rows.append([m.member, r.get("declaration", "-"), r.get("target", "-"),
                        r.get("result", "-"), r.get("gap", "-")])
        else:
            rows.append([m.member, "（記録なし）", "-", "-", "-"])
    t = Table(rows, colWidths=[25 * mm, 40 * mm, 30 * mm, 30 * mm, 25 * mm])
    t.setStyle(_table_style())
    flow.append(t)
    return flow


def build_agency_pdf(
    out_path: str,
    agency: str,
    week_start: str,
    week_end: str,
    agency_members: list[MemberWeekStats],
    all_members_all_agencies: list[MemberWeekStats],
    peers_by_role: dict[str, list[MemberWeekStats]],
    month_target_revenue: float | None = None,
    week_target_revenue: float | None = None,
    pdca_rows: list[dict] | None = None,
) -> int:
    """1代理店分のPDFを生成し、生成したページ数を返す。"""
    styles = base_styles()
    doc = SimpleDocTemplate(out_path, pagesize=PAGE_A4_PORTRAIT,
                             leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN)

    flow: list = []
    flow += _cover_page(styles, agency, week_start, week_end)
    flow += _progress_page(styles, agency_members, month_target_revenue, week_target_revenue)
    flow += _ranking_page(styles, all_members_all_agencies)

    ordered = (
        [m for m in agency_members if m.role == ROLE_APPOINTER]
        + [m for m in agency_members if m.role == ROLE_CLOSER]
    )
    for m in ordered:
        flow += _member_sheets(styles, m, peers_by_role[m.role])

    flow += _review_page(styles, agency_members, pdca_rows)

    doc.build(flow)
    return doc.page
