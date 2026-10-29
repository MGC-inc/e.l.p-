"""月次MTで画面に映すスライド（16:9・全社1冊）を作る。
使い方: python build_monthly_slides.py 計画JSON 出力PDF
計画JSONは scripts/monthly_plan.py --out で作る（MT前日は --lock で確定済みの予算・目標を固定し、実績だけ最新にする）。

  1. 表紙と今日のゴール
  2. 先月の全社結果（予算・契約・キャンセル・実質売上と、個人ごとの予算と実質売上）
  3. 全員が目標どおりやると何万か／予算との差を、どこの改善で埋めるか（全社の合計）
  4. 今月の予算の配分
  5. 今月の目標（先月×110%。稼働日と平日・土日祝を加味）
  6. 個人スライド（1人1枚。目標・見込み・改善ポイントを数字で。月初からの実績も）
  7. アクションプランの決め方とBOTへの登録方法（前日・当日のリマインドつき）
"""
import json, os, sys, textwrap
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_all as B  # noqa: F401  日本語フォントの登録
from build_slides import (NAVY, BLUE, TEAL, GOLD, INK, SUB, LINE, BG, GREEN, RED, YEL, W, H,  # noqa: E402
                          save, slide, card)

KEY_TEXT = {"対面率": "訪問→対面", "対話率": "対面→対話", "アポ率": "対話→アポ", "アポ→契約率": "アポ1件あたりの契約",
            "商談→契約率": "商談→契約", "残る率": "契約が残る率（クーリングオフ・キャンセルされない）"}
TGT_NAMES = [("訪問数", "訪問"), ("アポ数", "アポ"), ("有効商談数", "商談"), ("契約数", "契約")]


def mlabel(ym):
    return f"{int(ym[5:])}月"


def pct(x):
    return f"{x * 100:.1f}%" if isinstance(x, (int, float)) else "-"


def man(x):
    return f"{x:,.0f}万"


def cover(pdf, plan, n):
    fig = plt.figure(figsize=(W, H)); fig.patch.set_facecolor(NAVY)
    fig.text(0.06, 0.74, "月次MT", color="white", fontsize=54, fontweight="bold")
    fig.text(0.06, 0.64, f"{mlabel(plan['prev'])}の振り返りと{mlabel(plan['month'])}の目標　｜　実績は{plan['asof']}まで",
             color="#C9D6E8", fontsize=20)
    fig.text(0.06, 0.55, f"今日のゴール：全員が{mlabel(plan['month'])}の予算・目標と「どこを改善するか」を数字でつかみ、"
             "アクションプランをBOTに登録して帰る", color=GOLD, fontsize=16, fontweight="bold")
    steps = [("5分", f"{mlabel(plan['prev'])}の\n全社結果"), ("5分", "予算との差を\nどこで埋めるか"), ("5分", f"{mlabel(plan['month'])}の\n予算と目標"),
             ("25分", "1人ずつ\n改善ポイント"), ("5分", "アクションプランを\nBOTに登録")]
    x0, w = 0.06, 0.168
    for i, (t, s) in enumerate(steps):
        x = x0 + i * (w + 0.012)
        card(fig, x, 0.13, w, 0.3, "#2A4A73")
        fig.text(x + 0.015, 0.385, f"STEP {i+1}", color=GOLD, fontsize=12, fontweight="bold")
        fig.text(x + 0.015, 0.29, s, color="white", fontsize=16, fontweight="bold", va="center", linespacing=1.4)
        fig.text(x + 0.015, 0.16, t, color="#C9D6E8", fontsize=14)
    fig.text(0.965, 0.03, str(n), color="#C9D6E8", fontsize=10, ha="right")
    save(pdf, fig)


def tile(fig, x, y, w, h, label, value, color=NAVY, note=""):
    card(fig, x, y, w, h)
    fig.text(x + 0.015, y + h - 0.035, label, color=SUB, fontsize=12)
    fig.text(x + 0.015, y + (0.045 if note else 0.03), value, color=color, fontsize=26, fontweight="bold")
    if note:
        fig.text(x + 0.015, y + 0.012, note, color=SUB, fontsize=10)


def prev_result(pdf, plan, n):
    ms = plan["members"]
    budget = sum(m["budget_prev"] for m in ms)
    gross = sum(m["prev"]["契約金額（万円）"] for m in ms)
    cancel = sum(m["prev"]["キャンセル金額（万円）"] for m in ms)
    real = sum(m["prev"]["実質売上（万円）"] for m in ms)
    fig = slide(pdf, f"{mlabel(plan['prev'])}の全社結果", f"対象{len(ms)}名（実績は{plan['asof']}まで）", n)
    vals = [("予算（合計）", man(budget), NAVY, ""), ("契約金額", man(gross), BLUE, "クローザー・アポインター折半後の合計"),
            ("キャンセル金額", man(cancel), RED, f"契約金額の{pct(cancel / gross) if gross else '-'}が消えた"),
            ("実質売上", man(real), GREEN if real >= budget else RED, f"予算の{pct(real / budget) if budget else '-'}")]
    for i, (lab, v, c, note) in enumerate(vals):
        tile(fig, 0.035 + i * 0.237, 0.70, 0.225, 0.15, lab, v, c, note)
    ax = fig.add_axes([0.10, 0.07, 0.86, 0.57])
    rows = sorted(ms, key=lambda m: -m["budget_prev"])
    ys = range(len(rows))[::-1]
    ax.barh([y + 0.2 for y in ys], [m["budget_prev"] for m in rows], 0.38, color=LINE, label="予算")
    ax.barh([y - 0.2 for y in ys], [m["prev"]["実質売上（万円）"] for m in rows], 0.38,
            color=[GREEN if m["prev"]["実質売上（万円）"] >= m["budget_prev"] else BLUE for m in rows], label="実質売上")
    ax.set_yticks(list(ys)); ax.set_yticklabels([m["name"] for m in rows], fontsize=10)
    ax.tick_params(axis="x", labelsize=9); ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="lower right", fontsize=10, frameon=False); ax.set_xlabel("万円", fontsize=9)
    save(pdf, fig)


def gap_plan(pdf, plan, n):
    ms = plan["members"]
    proj = sum(m["proj_sales"] for m in ms)
    total = plan["total"]
    gains = {}
    for m in ms:
        for lv in m.get("levers") or []:
            gains[lv["key"]] = gains.get(lv["key"], 0) + lv["gain_sales"]
    fig = slide(pdf, "予算との差を、どこの改善で埋めるか", f"{mlabel(plan['month'])}の全社予算 {man(total)}", n)
    tile(fig, 0.035, 0.70, 0.30, 0.15, "全員が目標（先月×110%）どおりだと", man(proj), BLUE)
    tile(fig, 0.35, 0.70, 0.30, 0.15, f"{mlabel(plan['month'])}の予算", man(total), NAVY)
    tile(fig, 0.665, 0.70, 0.30, 0.15, "足りない分（改善で埋める）", man(max(0, total - proj)), RED)
    fig.text(0.035, 0.63, "各工程の率を「できてる人の水準」（同じ役割で件数が十分ある人の上位25%）まで上げると、全社でこれだけ増えます",
             color=INK, fontsize=13)
    ax = fig.add_axes([0.30, 0.10, 0.62, 0.48])
    items = sorted(gains.items(), key=lambda x: x[1])
    ax.barh(range(len(items)), [v for _, v in items], color=TEAL)
    ax.set_yticks(range(len(items)))
    ax.set_yticklabels([f"{KEY_TEXT.get(k, k)}\n（できてる人の水準 {pct(plan['benchmarks'].get(k))}）" for k, _ in items], fontsize=10)
    for i, (_, v) in enumerate(items):
        ax.text(v, i, f"  +{v:,.0f}万", va="center", fontsize=11, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False); ax.tick_params(axis="x", labelsize=9)
    c = plan["company"]
    fig.text(0.035, 0.05, f"全社の直近3か月：契約1件あたり{c['price']:.1f}万（折半後）・キャンセル率{pct(c['cancel'])}・"
             f"アポ1件あたりの契約{pct(c['apo2con'])}・商談→契約{pct(c['close'])}", color=SUB, fontsize=10)
    save(pdf, fig)


def table(pdf, plan, n, title, sub, header, rows, widths):
    fig = slide(pdf, title, sub, n)
    top, rh = 0.84, min(0.045, 0.78 / (len(rows) + 1))
    xs = [0.035]
    for w in widths[:-1]:
        xs.append(xs[-1] + w)
    for x, h in zip(xs, header):
        fig.text(x + 0.005, top, h, color=SUB, fontsize=11, fontweight="bold", va="center")
    for i, r in enumerate(rows):
        y = top - (i + 1) * rh
        if i % 2 == 0:
            card(fig, 0.03, y - rh / 2, 0.94, rh, BG)
        for j, (x, v) in enumerate(zip(xs, r)):
            fig.text(x + 0.005, y, v, color=INK, fontsize=12 if len(rows) <= 15 else 11,
                     fontweight="bold" if j in (0, len(r) - 1) and title.endswith("配分") else "normal", va="center")
    save(pdf, fig)


def budgets(pdf, plan, n):
    ms = sorted(plan["members"], key=lambda m: -m["budget"])
    rows = [[m["name"], m["agency"] or "-", man(m["budget_prev"]), man(m["prev"]["実質売上（万円）"]),
             man(m["perf_avg3"]), man(m["budget"])] for m in ms]
    rows.append(["合計", "", man(sum(m["budget_prev"] for m in ms)), man(sum(m["prev"]["実質売上（万円）"] for m in ms)),
                 "", man(sum(m["budget"] for m in ms))])
    table(pdf, plan, n, f"{mlabel(plan['month'])}の予算の配分", f"全社 {man(plan['total'])}",
          ["名前", "代理店", f"{mlabel(plan['prev'])}予算", f"{mlabel(plan['prev'])}実質売上", "直近3か月平均", f"{mlabel(plan['month'])}予算"],
          rows, [0.13, 0.20, 0.15, 0.17, 0.15, 0.15])


def targets(pdf, plan, n):
    ms = sorted(plan["members"], key=lambda m: -m["budget"])
    rows = []
    for m in ms:
        t, d = m["targets"], m["days"]
        rows.append([m["name"], f"{d['total']:.0f}日（土日祝{d['heavy']:.0f}・平日{d['light']:.0f}）"]
                    + [str(t[k]) if k in t else "-" for k, _ in TGT_NAMES] + [man(m["proj_sales"])])
    c = plan["company"]["days"]
    table(pdf, plan, n, f"{mlabel(plan['month'])}の目標（先月×110%）",
          f"会社の稼働日 土日祝{c['heavy']}日・平日{c['light']}日／1日あたりの先月実績×見込み稼働日×1.1",
          ["名前", "稼働見込み", "訪問", "アポ", "商談", "契約", "この目標での売上見込み"],
          rows, [0.12, 0.25, 0.10, 0.10, 0.10, 0.10, 0.20])


def person(pdf, plan, m, n):
    fig = slide(pdf, f"{m['name']}さん　{mlabel(plan['month'])}の目標と改善ポイント", f"{m['role']}｜{m['agency']}", n)
    card(fig, 0.035, 0.52, 0.30, 0.33)
    fig.text(0.05, 0.80, f"{mlabel(plan['month'])}の予算", color=SUB, fontsize=12)
    fig.text(0.05, 0.71, man(m["budget"]), color=NAVY, fontsize=34, fontweight="bold")
    fig.text(0.05, 0.665, f"{mlabel(plan['prev'])}：予算{man(m['budget_prev'])}／実質売上{man(m['prev']['実質売上（万円）'])}",
             color=SUB, fontsize=11)
    fig.text(0.05, 0.60, f"この目標どおりだと 約{man(m['proj_sales'])}", color=INK, fontsize=14, fontweight="bold")
    fig.text(0.05, 0.555, (f"予算まで あと{man(m['gap'])}" if m["gap"] > 0 else "予算に届く見込み"),
             color=RED if m["gap"] > 0 else GREEN, fontsize=14, fontweight="bold")
    t, cur = m["targets"], m.get("current") or {}
    cur_map = {"訪問数": "訪問数", "アポ数": "アポ数", "有効商談数": "有効商談数", "契約数": "契約"}
    keys = [(k, lab) for k, lab in TGT_NAMES if k in t]
    w = 0.30 / max(1, len(keys)) - 0.008
    for i, (k, lab) in enumerate(keys):
        x = 0.035 + i * (w + 0.008)
        card(fig, x, 0.33, w, 0.16)
        fig.text(x + 0.01, 0.455, lab, color=SUB, fontsize=11)
        fig.text(x + 0.01, 0.385, str(t[k]), color=NAVY, fontsize=24, fontweight="bold")
        if cur.get("稼働日"):
            fig.text(x + 0.01, 0.345, f"いま {cur.get(cur_map[k], 0):.0f}", color=SUB, fontsize=10)
    d = m["days"]
    fig.text(0.035, 0.29, f"稼働見込み {d['total']:.0f}日（土日祝{d['heavy']:.0f}・平日{d['light']:.0f}）"
             + (f"／{plan['asof'][5:].replace('-', '/')}までの実績 {cur['稼働日']:.0f}日" if cur.get("稼働日") else ""),
             color=SUB, fontsize=11)

    fig.text(0.37, 0.82, "🔑 ここを改善すると数字が伸びます".replace("🔑 ", ""), color=INK, fontsize=16, fontweight="bold")
    y = 0.73
    for i, lv in enumerate(m.get("levers") or [], 1):
        card(fig, 0.37, y - 0.075, 0.595, 0.115, YEL if i == 1 else BG)
        fig.text(0.385, y + 0.012, f"{i}. {KEY_TEXT.get(lv['key'], lv['key'])}", color=INK, fontsize=14, fontweight="bold")
        apo = f"アポ +{lv['gain_apo']:.0f}件 → " if lv.get("gain_apo") else ""
        fig.text(0.385, y - 0.045, f"{pct(lv['now'])} → {pct(lv['bench'])}（できてる人の水準）で {apo}実質売上 +{lv['gain_sales']:.0f}万",
                 color=INK, fontsize=12)
        if lv.get("need_for_budget"):
            fig.text(0.955, y + 0.012, f"これ1つで予算まで：{pct(lv['need_for_budget'])}", color=RED, fontsize=11, ha="right")
        y -= 0.13
    if not m.get("levers"):
        fig.text(0.385, y, "どの工程も、できてる人の水準に届いています。件数で伸ばしましょう。", color=INK, fontsize=13)
        y -= 0.08
    vol = m.get("volume")
    if vol and vol.get("add"):
        fig.text(0.37, y + 0.02, f"率を変えずに件数で埋めるなら：{'商談' if vol['key'] == '有効商談数' else 'アポ'} あと +{vol['add']:.0f}件",
                 color=SUB, fontsize=12)
    card(fig, 0.035, 0.05, 0.93, 0.19, "white", edge=NAVY, lw=1.5)
    fig.text(0.05, 0.205, "アクションプラン（改善のために、いつ・誰と・何をするか）→ MTのあとBOTで登録", color=NAVY,
             fontsize=13, fontweight="bold")
    for i in range(3):
        fig.text(0.05, 0.155 - i * 0.045, f"{i + 1}.　いつ：＿＿＿＿　誰と：＿＿＿＿＿＿　何を：＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿",
                 color=SUB, fontsize=12)
    save(pdf, fig)


def how_to(pdf, plan, n):
    fig = slide(pdf, "アクションプランの登録（MTの最後に）", "BOTの「今月のアクションプランを登録する」", n)
    steps = [("1", "ボタンを押す", "「今月のアクションプランを登録する」を押すと、自分の予算・目標と改善ポイント（何%を何%にすると何万増えるか）が届く"),
             ("2", "どこを改善するか選ぶ", "改善ポイントのボタンから1つ選ぶ（件数で埋める場合は「件数を増やす」）"),
             ("3", "何をするか決める", "例のボタンから選ぶか、自分の言葉で送る（例：木曜に岡野さんの商談に同行して、クロージングを見て学ぶ）"),
             ("4", "いつ・誰と", "実行日をボタンで選ぶ（「11/20」のように送ってもOK）。相手がいれば名前を送る"),
             ("5", "最大3つまで → やり切る！", "内容を確認して「やり切る！」で登録完了")]
    y = 0.81
    for no, head, body in steps:
        card(fig, 0.035, y - 0.095, 0.60, 0.13)
        fig.text(0.05, y, f"STEP {no}　{head}", color=NAVY, fontsize=14, fontweight="bold")
        fig.text(0.05, y - 0.022, textwrap.fill(body, 40), color=INK, fontsize=11, va="top", linespacing=1.4)
        y -= 0.145
    card(fig, 0.665, 0.12, 0.30, 0.73, YEL)
    fig.text(0.68, 0.80, "登録したあとは", color=INK, fontsize=15, fontweight="bold")
    msgs = ["前日18時：「明日は、改善のために立てた\nアクションプランの実行日です」",
            "相手がいるときは：「再度確認で○○さんに\n予約のLINE入れて、実行にこだわって\n徹底しましょう！」",
            "当日の朝：「今日は、改善のために立てた\nアクションプランの実行日です」"]
    y = 0.72
    for t_ in msgs:
        fig.text(0.68, y, t_, color=INK, fontsize=12, va="top", linespacing=1.5)
        y -= 0.19
    save(pdf, fig)


def main(src, out):
    plan = json.load(open(src, encoding="utf-8"))
    with PdfPages(out) as pdf:
        n = 1
        cover(pdf, plan, n); n += 1
        prev_result(pdf, plan, n); n += 1
        gap_plan(pdf, plan, n); n += 1
        budgets(pdf, plan, n); n += 1
        targets(pdf, plan, n); n += 1
        for m in sorted(plan["members"], key=lambda m: -m["budget"]):
            person(pdf, plan, m, n); n += 1
        how_to(pdf, plan, n)
    print(f"{out}（{n}ページ）")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
