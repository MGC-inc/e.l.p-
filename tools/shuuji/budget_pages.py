"""「予算の達成状況」ページ（週次MTスライド）。build_slides.py から呼ぶ。
今川さん指示 2026-11-18:「各自に任された予算を達成できているメンバーかどうかを明記。あと何件やるべきか、ラスト1週でどれだけやるべきか明確に」
入力: budget_status.csv（notion_to_csv.py。name,role,budget,sales,price,deal_rate,weeks_left,month_end）
  達成 = 月初〜先週日曜の実質売上 ≥ 月予算
  必要数: クローザー＝残り売上÷平均契約単価（契約件数）と、契約率で割り戻した商談件数 ／ アポインター＝残り売上÷7.5万円（1アポあたり。月目標と同じ）
  今週・ラスト1週: 月末までの必要数を残り週数で割り（切り上げ）、今週ぶん＝1週あたり、ラスト1週＝残り全部−今週ぶん（残り1週しかなければ全部）
"""
import csv
import math
import os

PER_PAGE = 8
MAN_PER_APO = 7.5   # scripts/monthly_budget_floor.py と同じ


def load(inp):
    path = os.path.join(inp, "budget_status.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def need(r, fallback_rate):
    """(月末まで必要な主な数, 単位, 補助の数, 補助の単位) を返す。達成済みは0"""
    rem = max(0.0, float(r["budget"]) - float(r["sales"]))
    if rem <= 0:
        return 0, "", 0, ""
    if r["role"] == "closer":
        n = math.ceil(rem / float(r["price"]))
        rate = float(r["deal_rate"]) if r["deal_rate"] else fallback_rate
        return n, "契約", (math.ceil(n / (rate / 100)) if rate else 0), "商談"
    return math.ceil(rem / MAN_PER_APO), "アポ", 0, ""


def split(n, weeks):
    """月末までの必要数 n を (今週, ラスト1週) に分ける。残り1週なら今週は無し"""
    if weeks <= 1:
        return None, n
    per = math.ceil(n / weeks)
    return per, max(0, n - per * (weeks - 1))


def pages(pdf, ctx, rows, agency, mem, n):
    rows = [r for r in rows if r["name"] in mem and (agency == ctx.ALL or mem[r["name"]]["agency"] == agency)]
    if not rows:
        return n
    rates = [float(r["deal_rate"]) for r in rows if r["role"] == "closer" and r["deal_rate"]]
    fb = sum(rates) / len(rates) if rates else None
    rows.sort(key=lambda r: float(r["sales"]) / float(r["budget"]))
    ok = [r for r in rows if float(r["sales"]) >= float(r["budget"])]
    chunks = [rows[i:i + PER_PAGE] for i in range(0, len(rows), PER_PAGE)]
    for ci, chunk in enumerate(chunks):
        fig = ctx.slide(pdf, "⑤ 予算の達成状況", f"{ctx.ALL if agency == ctx.ALL else agency}　{ci + 1}/{len(chunks)}", n)
        weeks = int(rows[0]["weeks_left"]); me = rows[0]["month_end"][5:].replace("-", "/").lstrip("0")
        fig.text(0.03, 0.855, f"達成 {len(ok)}人　／　未達 {len(rows) - len(ok)}人　　（月初〜先週日曜の実質売上と、月の予算を比べる。月末{me}まであと{weeks}週）",
                 fontsize=13, fontweight="bold", color=ctx.NAVY, va="center")
        heads = [("名前", 0.03, "left"), ("予算", 0.20, "center"), ("実質売上", 0.275, "center"), ("達成率", 0.40, "center"), ("判定", 0.545, "center"),
                 ("残り売上", 0.625, "center"), ("月末まで必要", 0.735, "center"), ("今週", 0.84, "center"), ("ラスト1週", 0.93, "center")]
        for h, x, ha in heads:
            fig.text(x, 0.805, h, fontsize=10.5, color=ctx.SUB, ha=ha)
        rowh = 0.088
        for i, r in enumerate(chunk):
            y = 0.735 - i * rowh
            b, s = float(r["budget"]), float(r["sales"])
            done = s >= b
            if i % 2 == 0:
                ctx.card(fig, 0.02, y - 0.03, 0.96, rowh - 0.006, ctx.BG)
            role = "クローザー" if r["role"] == "closer" else "アポインター"
            fig.text(0.03, y + 0.012, r["name"], fontsize=15, fontweight="bold", color=ctx.INK, va="center")
            fig.text(0.03, y - 0.017, role, fontsize=8.5, color=ctx.SUB, va="center")
            fig.text(0.20, y, f"{b:,.0f}万", fontsize=13, ha="center", va="center", color=ctx.INK)
            fig.text(0.275, y, f"{s:,.0f}万", fontsize=14, ha="center", va="center", fontweight="bold", color=ctx.NAVY)
            pct = s / b * 100
            ax = fig.add_axes([0.335, y - 0.012, 0.115, 0.024]); ax.set_xlim(0, 100); ax.set_ylim(-0.5, 0.5); ax.axis("off")
            ax.barh([0], [100], color="#E3E8EF", height=1)
            ax.barh([0], [min(pct, 100)], color=ctx.GREEN if done else (ctx.GOLD if pct >= 60 else ctx.RED), height=1)
            fig.text(0.456, y, f"{pct:.0f}%", fontsize=13, ha="left", va="center", fontweight="bold", color=ctx.GREEN if done else ctx.INK)
            card_c = "#E3F2EA" if done else "#FBE3E3"
            ctx.card(fig, 0.50, y - 0.02, 0.09, 0.04, card_c)
            fig.text(0.545, y, "✓ 達成" if done else "未達", fontsize=13, ha="center", va="center", fontweight="bold", color=ctx.GREEN if done else ctx.RED)
            if done:
                fig.text(0.625, y, "—", fontsize=13, ha="center", va="center", color=ctx.SUB)
                fig.text(0.735, y, f"予算超え（+{s - b:,.0f}万）", fontsize=11, ha="center", va="center", color=ctx.GREEN, fontweight="bold")
                continue
            fig.text(0.625, y, f"{b - s:,.0f}万", fontsize=14, ha="center", va="center", fontweight="bold", color=ctx.RED)
            nn, unit, sub, sunit = need(r, fb)
            fig.text(0.735, y + (0.012 if sub else 0), f"{unit}{nn}件", fontsize=15, ha="center", va="center", fontweight="bold", color=ctx.INK)
            if sub:
                fig.text(0.735, y - 0.017, f"（{sunit}{sub}件）", fontsize=9.5, ha="center", va="center", color=ctx.SUB)
            per, last = split(nn, int(r["weeks_left"]))
            subper, sublast = split(sub, int(r["weeks_left"])) if sub else (None, 0)
            for x, v, sv in ((0.84, per, subper), (0.93, last, sublast)):
                if v is None:
                    fig.text(x, y, "—", fontsize=13, ha="center", va="center", color=ctx.SUB)
                    continue
                fig.text(x, y + (0.012 if sub else 0), f"{unit}{v}件", fontsize=13, ha="center", va="center", fontweight="bold",
                         color=ctx.RED if x > 0.9 else ctx.INK)
                if sub and sv is not None:
                    fig.text(x, y - 0.017, f"（{sunit}{sv}件）", fontsize=9.5, ha="center", va="center", color=ctx.SUB)
        fig.text(0.03, 0.058, "契約・アポの必要数は、残り売上を割って切り上げ。クローザー＝平均契約単価で割った契約件数（カッコは契約率で割り戻した商談件数）、アポインター＝1アポ7.5万円で割ったアポ件数。", fontsize=8.5, color=ctx.SUB)
        fig.text(0.03, 0.032, "今週＝残りを週数で割った1週ぶん、ラスト1週＝そこから今週ぶんを引いた最後の週の必要数。売上は、アポとクローザーで50:50に分けた後の本人取り分。",
                 fontsize=8.5, color=ctx.SUB)
        ctx.save(pdf, fig)
        n += 1
    return n
