"""クローザーの週次結果ページ（自アポ・他アポ別）。build_slides.py から呼ぶ。
今川さん指示 2026-11-17:「アポインターとしての結果しか記載できてない。クローザーは自アポ・他アポそれぞれ何件入って、
どれが契約でどれが保留かが一目で分かるように。その％も出して、直近の結果と比較して分かりやすく。全体として伸びているか落ちているかも分かるように」

入力: closer_deals.csv（notion_to_csv.pyが作る。先週＋その前4週の商談を1件1行で）
  week_start,date,closer,own(1=自アポ/0=他アポ),result,customer
ページ:
  1. クローザー結果（自アポ・他アポ別）… 全員を1枚の表に（件数・契約率・前週比・4週平均比・傾向）
  2. 全体の傾向 … 5週の契約率と商談数の推移、伸びているか落ちているかの判定
  3. 1人1枚 … 自アポ／他アポ別の内訳、契約・保留・失注のお客様名、5週の推移
契約率 = 契約 ÷ 商談数（保留・失注・クーリングオフなど契約にならなかった商談も分母に入れる＝即決率）。
"""
import csv
import datetime
import io
import os
from collections import Counter

import matplotlib.pyplot as plt

CATS = ["契約", "保留", "失注", "クーリングオフ", "その他"]
CAT_COLOR = {"契約": "#2E8B57", "保留": "#E8A33D", "失注": "#D64545", "クーリングオフ": "#8E5BB5", "その他": "#9AA5B1"}
MIN_N = 5          # 合計の商談数がこれ未満の週・人は「件数が少ないので参考」と表示する
UP, DOWN = 5.0, -5.0   # 4週平均より契約率が何pt上・下なら「上向き」「下向き」とみなすか


def load(inp):
    path = os.path.join(inp, "closer_deals.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def cat(result):
    return result if result in CATS[:4] else "その他"


def weeks_of(last_monday):
    """古い週→先週の5週（月曜の日付）"""
    return [(last_monday - datetime.timedelta(weeks=i)).isoformat() for i in range(4, -1, -1)]


def count(rows, week=None, closers=None, own=None):
    """条件に合う商談の結果別の件数 Counter（total=商談数）"""
    c = Counter()
    for r in rows:
        if week is not None and r["week_start"] not in ([week] if isinstance(week, str) else week):
            continue
        if closers is not None and r["closer"] not in closers:
            continue
        if own is not None and (r["own"] == "1") != own:
            continue
        c[cat(r["result"])] += 1
        c["total"] += 1
    return c


def rate(c):
    return c["契約"] / c["total"] * 100 if c["total"] else None


def fmt(v, d=0):
    return "—" if v is None else f"{v:.{d}f}%"


def diff(a, b):
    return None if a is None or b is None else a - b


def fdiff(v):
    return "—" if v is None else f"{v:+.0f}pt"


def trend(this_rate, base_rate, n_this, n_base):
    """先週の契約率を、その前4週の合計の契約率と比べて 傾向（ラベル, 色）を返す。
    差が±UPpt以上で「やや」、±2×UPpt以上で「上向き／下向き」。件数が少ない週は（参考）を付ける"""
    if this_rate is None:
        return "先週の商談なし", "#5A6472"
    if base_rate is None or n_base < MIN_N:
        return "比較なし", "#5A6472"
    d = this_rate - base_rate
    note = "（参考）" if n_this < MIN_N else ""
    if d >= 2 * UP:
        return f"▲ 上向き{note}", "#2E8B57"
    if d >= UP:
        return f"▲ やや上向き{note}", "#2E8B57"
    if d <= 2 * DOWN:
        return f"▼ 下向き{note}", "#D64545"
    if d <= DOWN:
        return f"▼ やや下向き{note}", "#D64545"
    return f"→ 横ばい{note}", "#1F3A5F"


def closers_of(mem, agency, all_label):
    return [n for n, m in mem.items() if m["role"] == "closer" and (agency == all_label or m["agency"] == agency)]


def pages(pdf, ctx, rows, agency, mem, last_monday, n):
    """ctx = build_slides モジュール（slide/card/save/色を借りる）。ページ数を足して次のページ番号を返す"""
    closers = closers_of(mem, agency, ctx.ALL)
    if not closers or not rows:
        return n
    wk = weeks_of(last_monday)
    last, prev, base = wk[-1], wk[-2], wk[:-1]
    # 先週に商談のあった人を先に、商談数の多い順
    order = sorted(closers, key=lambda c: (-count(rows, last, [c])["total"], c))
    n = summary(pdf, ctx, rows, order, wk, n)
    n = team_trend(pdf, ctx, rows, order, wk, agency, n)
    for c in order:
        if count(rows, last, [c])["total"]:
            n = one(pdf, ctx, rows, c, wk, n)
    return n


# ---------- 1. 全員の表 ----------
def summary(pdf, ctx, rows, order, wk, n):
    last, prev, base = wk[-1], wk[-2], wk[:-1]
    fig = ctx.slide(pdf, "⑥ クローザー結果　自アポ・他アポ別", ctx_week(ctx, last), n)
    cols = [  # (見出し, x, 幅)
        ("名前", 0.03, 0.07),
        ("商談", 0.105, 0.035), ("契約", 0.14, 0.035), ("保留", 0.175, 0.035), ("失注", 0.21, 0.035), ("契約率", 0.245, 0.05),
        ("商談", 0.325, 0.035), ("契約", 0.36, 0.035), ("保留", 0.395, 0.035), ("失注", 0.43, 0.035), ("契約率", 0.465, 0.05),
        ("商談", 0.545, 0.035), ("契約", 0.58, 0.035), ("保留", 0.615, 0.035), ("失注", 0.65, 0.035), ("契約率", 0.685, 0.05),
        ("前週比", 0.755, 0.05), ("4週平均比", 0.81, 0.06), ("傾向", 0.875, 0.1),
    ]
    y0 = 0.80
    for g, x, w in (("自アポ（自分で取ったアポ）", 0.105, 0.19), ("他アポ（他の人が取ったアポ）", 0.325, 0.19), ("合計", 0.545, 0.19),
                    ("先週の契約率を比べる", 0.755, 0.225)):
        ctx.card(fig, x - 0.005, y0 - 0.005, w + 0.005 if g != "先週の契約率を比べる" else w, 0.055, ctx.NAVY)
        fig.text(x + w / 2, y0 + 0.022, g, ha="center", va="center", color="white", fontsize=11, fontweight="bold")
    for lab, x, w in cols:
        fig.text(x + w / 2 if lab != "名前" else x, y0 - 0.025, lab, ha="center" if lab != "名前" else "left", fontsize=9.5, color=ctx.SUB)
    rowh = min(0.08, 0.52 / max(len(order), 1))
    tot_all = Counter(); tot_prev = Counter(); tot_base = Counter()
    for i, c in enumerate(order):
        y = y0 - 0.06 - (i + 1) * rowh + rowh * 0.3
        if i % 2 == 0:
            ctx.card(fig, 0.02, y - rowh * 0.25, 0.96, rowh * 0.95, ctx.BG)
        o, t, a = count(rows, last, [c], True), count(rows, last, [c], False), count(rows, last, [c])
        p, b = count(rows, prev, [c]), count(rows, base, [c])
        fig.text(0.03, y + rowh * 0.2, c, fontsize=13, fontweight="bold", va="center", color=ctx.INK)
        for x0, cc in ((0.105, o), (0.325, t), (0.545, a)):
            vals = [cc["total"], cc["契約"], cc["保留"], cc["失注"]]
            for j, v in enumerate(vals):
                col = {1: ctx.GREEN, 2: "#C98318", 3: ctx.RED}.get(j, ctx.INK)
                fig.text(x0 + j * 0.035 + 0.0175, y + rowh * 0.2, str(v) if cc["total"] else "—", ha="center", va="center", fontsize=13,
                         color=col if v else ctx.SUB, fontweight="bold" if j == 1 and v else "normal")
            fig.text(x0 + 4 * 0.035 + 0.025, y + rowh * 0.2, fmt(rate(cc)), ha="center", va="center", fontsize=13, fontweight="bold", color=ctx.NAVY)
        d_prev, d_base = diff(rate(a), rate(p)), diff(rate(a), rate(b))
        for x, d in ((0.78, d_prev), (0.84, d_base)):
            fig.text(x, y + rowh * 0.2, fdiff(d), ha="center", va="center", fontsize=13,
                     color=ctx.SUB if d is None else (ctx.GREEN if d >= UP else (ctx.RED if d <= DOWN else ctx.INK)), fontweight="bold")
        lab, col = trend(rate(a), rate(b), a["total"], b["total"])
        fig.text(0.87, y + rowh * 0.2, lab, va="center", fontsize=10, color=col, fontweight="bold")
        tot_all += a; tot_prev += p; tot_base += b
    ya = 0.1
    ctx.card(fig, 0.02, ya, 0.96, 0.07, "#E8EEF6")
    lab, col = trend(rate(tot_all), rate(tot_base), tot_all["total"], tot_base["total"])
    fig.text(0.03, ya + 0.035, f"全員合計　商談{tot_all['total']}件・契約{tot_all['契約']}件・保留{tot_all['保留']}件・失注{tot_all['失注']}件　契約率 {fmt(rate(tot_all))}",
             va="center", fontsize=13, fontweight="bold", color=ctx.NAVY)
    fig.text(0.97, ya + 0.035, f"前週 {fmt(rate(tot_prev))}／直近4週平均 {fmt(rate(tot_base))}　{lab}", va="center", ha="right", fontsize=12, color=col, fontweight="bold")
    for k, t in enumerate((
            "契約率＝契約÷商談数（保留・失注・クーリングオフも分母に含む）。クーリングオフ・審査落ち等は商談数に入るが契約には数えない。",
            f"「4週平均比」は先週の契約率を、その前4週（{short(wk[0])}〜{short(wk[-2])}の週）の合計の契約率と比べた差。±{UP:.0f}pt以上でやや、±{2*UP:.0f}pt以上で上向き・下向き。",
            f"商談数が{MIN_N}件に満たない週・人は偶然で上下しやすいので「参考」。")):
        fig.text(0.03, 0.068 - k * 0.022, t, fontsize=9, color=ctx.SUB)
    ctx.save(pdf, fig)
    return n + 1


# ---------- 2. 全体の傾向 ----------
def team_trend(pdf, ctx, rows, order, wk, agency, n):
    last, prev, base = wk[-1], wk[-2], wk[:-1]
    fig = ctx.slide(pdf, f"⑥ クローザー全体の傾向　{'全社' if agency == ctx.ALL else agency}", ctx_week(ctx, last), n)
    labels = [short(w) + "〜" for w in wk]
    # 左: 商談数の積み上げ（結果別）
    ax = fig.add_axes([0.06, 0.2, 0.42, 0.56])
    bottom = [0] * 5
    for c in CATS:
        vals = [count(rows, w, order)[c] for w in wk]
        ax.bar(labels, vals, bottom=bottom, color=CAT_COLOR[c], label=c, width=0.6)
        for i, v in enumerate(vals):
            if v >= 2:
                ax.text(i, bottom[i] + v / 2, str(v), ha="center", va="center", color="white", fontsize=10, fontweight="bold")
        bottom = [b + v for b, v in zip(bottom, vals)]
    for i, b in enumerate(bottom):
        ax.text(i, b + 0.3, f"{b}件", ha="center", fontsize=11, fontweight="bold", color=ctx.INK)
    ax.set_title("商談数と結果の内訳（週ごと・右が先週）", loc="left", fontsize=12, color=ctx.SUB)
    ax.legend(ncol=5, fontsize=9, frameon=False, loc="upper left", bbox_to_anchor=(0, -0.08))
    ax.set_ylim(0, max(bottom + [5]) * 1.18)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(labelsize=10)
    # 右: 契約率の推移（合計・自アポ・他アポ）
    ax2 = fig.add_axes([0.56, 0.2, 0.41, 0.56])
    for lab, own, col, lw in (("合計", None, ctx.NAVY, 3), ("自アポ", True, ctx.GREEN, 2), ("他アポ", False, "#E8A33D", 2)):
        ys = [rate(count(rows, w, order, own)) for w in wk]
        xs = [i for i, y in enumerate(ys) if y is not None]
        ax2.plot(xs, [ys[i] for i in xs], marker="o", color=col, lw=lw, label=lab)
        for i in xs:
            ax2.text(i, ys[i] + 2.5, f"{ys[i]:.0f}%", ha="center", fontsize=10 if own is None else 9, color=col, fontweight="bold")
    ax2.set_xticks(range(5)); ax2.set_xticklabels(labels, fontsize=10)
    ax2.set_ylim(0, 105); ax2.set_title("契約率の推移（契約÷商談数）", loc="left", fontsize=12, color=ctx.SUB)
    ax2.legend(ncol=3, fontsize=9, frameon=False, loc="upper left", bbox_to_anchor=(0, -0.08))
    for s in ("top", "right"):
        ax2.spines[s].set_visible(False)
    ax2.tick_params(labelsize=10)
    # 上: 判定
    a, b, p = count(rows, last, order), count(rows, base, order), count(rows, prev, order)
    lab, col = trend(rate(a), rate(b), a["total"], b["total"])
    ctx.card(fig, 0.03, 0.785, 0.94, 0.095, "#F3F5F8")
    n_base_avg = b["total"] / 4
    fig.text(0.04, 0.832, f"判定　{lab}", fontsize=19, fontweight="bold", color=col, va="center")
    own_l, own_b = rate(count(rows, last, order, True)), rate(count(rows, base, order, True))
    oth_l, oth_b = rate(count(rows, last, order, False)), rate(count(rows, base, order, False))
    fig.text(0.33, 0.857, f"契約率　先週{fmt(rate(a))}（前週{fmt(rate(p))}／直近4週平均{fmt(rate(b))}）　商談数　先週{a['total']}件（4週平均{n_base_avg:.1f}件）",
             fontsize=11, color=ctx.INK, va="center")
    fig.text(0.33, 0.810, f"自アポ　先週{fmt(own_l)}（直近4週平均{fmt(own_b)}）　　他アポ　先週{fmt(oth_l)}（直近4週平均{fmt(oth_b)}）",
             fontsize=11, color=ctx.INK, va="center")
    r3 = [rate(count(rows, w, order)) for w in wk[-3:]]
    if None in r3:
        shape = "商談のない週あり"
    elif r3[2] > r3[1] > r3[0]:
        shape = "3週連続で上昇"
    elif r3[2] < r3[1] < r3[0]:
        shape = "3週連続で低下"
    elif max(r3) - min(r3) <= 5:
        shape = "ほぼ横ばい"
    else:
        shape = "上下あり"
    fig.text(0.03, 0.065, "直近3週の契約率：" + " → ".join(fmt(v) for v in r3) + f"　（{shape}）", fontsize=11, color=ctx.INK, fontweight="bold")
    fig.text(0.03, 0.035, "グラフの右端が先週。商談数が少ない週は契約率が大きく上下するので、率だけでなく件数も一緒に見る。", fontsize=9, color=ctx.SUB)
    ctx.save(pdf, fig)
    return n + 1


# ---------- 3. 1人1枚 ----------
def one(pdf, ctx, rows, name, wk, n):
    last, prev, base = wk[-1], wk[-2], wk[:-1]
    fig = ctx.slide(pdf, f"⑥ {name}　クローザー結果", ctx_week(ctx, last), n)
    a = count(rows, last, [name])
    lab, col = trend(rate(a), rate(count(rows, base, [name])), a["total"], count(rows, base, [name])["total"])
    fig.text(0.04, 0.862, f"傾向　{lab}", fontsize=14, fontweight="bold", color=col, va="center")
    # 左: 自アポ／他アポ／合計の件数と率の比較表
    heads = [("商談", 0.17), ("契約", 0.22), ("保留", 0.27), ("失注", 0.32), ("契約率", 0.385), ("前週", 0.445), ("4週平均", 0.50)]
    for h, x in heads:
        fig.text(x, 0.835, h, fontsize=10.5, color=ctx.SUB, ha="center")
    for i, (lab_, own) in enumerate((("自アポ", True), ("他アポ", False), ("合計", None))):
        y = 0.74 - i * 0.095
        ctx.card(fig, 0.03, y - 0.03, 0.51, 0.08, ctx.BG if own is not None else "#E8EEF6")
        c = count(rows, last, [name], own)
        fig.text(0.04, y + 0.01, lab_, fontsize=14, fontweight="bold", color=ctx.NAVY, va="center")
        for (h, x), v in zip(heads[:4], [c["total"], c["契約"], c["保留"], c["失注"]]):
            fig.text(x, y + 0.01, str(v), fontsize=17, fontweight="bold", ha="center", va="center",
                     color={"契約": ctx.GREEN, "保留": "#C98318", "失注": ctx.RED}.get(h, ctx.INK) if v else ctx.SUB)
        fig.text(0.385, y + 0.01, fmt(rate(c)), fontsize=17, fontweight="bold", ha="center", va="center", color=ctx.NAVY)
        pr, br = rate(count(rows, prev, [name], own)), rate(count(rows, base, [name], own))
        for x, v, ref in ((0.445, pr, rate(c)), (0.50, br, rate(c))):
            d = diff(ref, v)
            fig.text(x, y + 0.025, fmt(v), fontsize=11, ha="center", va="center", color=ctx.INK)
            fig.text(x, y - 0.005, fdiff(d) if d is not None else "", fontsize=9.5, ha="center", va="center",
                     color=ctx.GREEN if d is not None and d >= UP else (ctx.RED if d is not None and d <= DOWN else ctx.SUB), fontweight="bold")
    fig.text(0.04, 0.395, "※「前週」「4週平均」は契約率。下の±は先週との差", fontsize=9, color=ctx.SUB)
    # 左下: お客様の一覧（自アポ／他アポ × 契約・保留・失注…）
    fig.text(0.04, 0.35, "どれがどの結果か（お客様名）", fontsize=12, fontweight="bold", color=ctx.INK)
    y = 0.31
    for lab_, own in (("自アポ", True), ("他アポ", False)):
        for ct in CATS:
            names = [r["customer"] for r in rows if r["week_start"] == last and r["closer"] == name and (r["own"] == "1") == own and cat(r["result"]) == ct]
            if not names:
                continue
            ctx.card(fig, 0.03, y - 0.012, 0.51, 0.04, "white", CAT_COLOR[ct], 1.2)
            fig.text(0.04, y + 0.008, f"{lab_}　{ct}", fontsize=10.5, fontweight="bold", color=CAT_COLOR[ct], va="center")
            fig.text(0.19, y + 0.008, "　".join(names), fontsize=10.5, color=ctx.INK, va="center")
            y -= 0.048
    if y > 0.3:
        fig.text(0.04, 0.3, "先週の商談はありません", fontsize=11, color=ctx.SUB)
    # 右: 5週の推移（自アポ・他アポ）
    for k, (lab_, own) in enumerate((("自アポ", True), ("他アポ", False))):
        ax = fig.add_axes([0.60 + 0.0 * k, 0.50 - k * 0.38 + 0.0, 0.37, 0.27])
        bottom = [0] * 5
        for c in CATS:
            vals = [count(rows, w, [name], own)[c] for w in wk]
            ax.bar(range(5), vals, bottom=bottom, color=CAT_COLOR[c], width=0.6, label=c if k == 0 else None)
            bottom = [b + v for b, v in zip(bottom, vals)]
        for i, w in enumerate(wk):
            cc = count(rows, w, [name], own)
            ax.text(i, bottom[i] + 0.15, f"{cc['total']}件" if cc["total"] else "", ha="center", fontsize=9, color=ctx.INK)
            ax.text(i, -0.18 * max(max(bottom), 3) / 3 - 0.05, fmt(rate(cc)) if cc["total"] else "—", ha="center", va="top", fontsize=10,
                    fontweight="bold", color=ctx.NAVY)
        ax.set_xticks(range(5)); ax.set_xticklabels([short(w) + "〜" for w in wk], fontsize=9)
        ax.tick_params(axis="x", pad=18)
        ax.set_ylim(0, max(max(bottom), 3) * 1.25); ax.set_yticks([])
        ax.set_title(f"{lab_}の推移（棒＝商談数、下の％＝契約率）", loc="left", fontsize=11, color=ctx.SUB)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        if k == 0:
            ax.legend(ncol=5, fontsize=8.5, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.12))
    ctx.save(pdf, fig)
    return n + 1


def short(iso, _=0):
    d = datetime.date.fromisoformat(iso)
    return f"{d.month}/{d.day}"


def ctx_week(ctx, last):
    d = datetime.date.fromisoformat(last)
    e = d + datetime.timedelta(days=6)
    return f"{d.month}/{d.day}〜{e.month}/{e.day}（先週）"
