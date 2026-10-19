"""週次MTで画面に映すスライド（16:9・代理店ごと）を作る。マニュアル/週次MT.md の進行順に並べる。
使い方: python build_slides.py 入力フォルダ 出力フォルダ [--strict]
入力は build_all.py と同じ（members/results/config/pdca の4CSV。notion_to_csv.pyが作る）。
出力: 出力フォルダ/public/<ランダム名>.pdf（代理店ごと）＋ slides_manifest.csv（代理店→ファイル名）

毎週変わるのは数字と、各自がLINEで送った宿題・学び（pdca.csv）だけ。並び・デザインは固定。
  1. 表紙と今日の進行
  2. 先週の宿題の答え合わせ（宣言した目標と実績を自動で突き合わせ、◎○×は当日記入）
  3. チームの数字（代理店の先週実績＋全社の月・週進捗）
  4. 全社ランキング（部門別の表彰台）
  5. 宣言達成者（LINEで宣言した数字目標を全部クリアした人）＋代理店ランキング
  6. 個人スライド（1人1枚。平均比と最優先の改善項目）
  7. 先週MTで出たみんなの学び（全社）
  8. MTの最後にBOTへ送る内容（記入例つき）
  9. 今週の動き方（水:MT／木:アポ中心／金:クロ中心）
"""
import csv, os, sys, secrets, textwrap
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch, Circle
import matplotlib.patheffects as pe
from matplotlib.text import Text
import build_all as B

plt.rcParams["pdf.fonttype"] = 42
NAVY, BLUE, TEAL, GOLD, SILVER, BRONZE = "#1F3A5F", "#2E6DA4", "#3B8EA5", "#E0A100", "#9AA5B1", "#B87333"
INK, SUB, LINE, BG, GREEN, RED, YEL = "#1B1F24", "#5A6472", "#D5DBE3", "#F3F5F8", "#2E8B57", "#D64545", "#FFF4CC"
MEDALS = [(GOLD, "1位"), (SILVER, "2位"), (BRONZE, "3位")]
W, H = 13.33, 7.5
ALL = "全社"

# LINEの返信テンプレ③と同じ並び。(表示名, PDCA目標列, 実績キー[自アポ側], 実績キー[他アポ側 or None])
TARGETS = {
    "closer": [("訪問", "訪問数目標", "visits", None), ("アポ", "アポ数目標", "appo", None),
               ("商談", "商談数目標_自アポ", "meet_own", "meet_other"), ("契約", "契約数目標_自アポ", "win_own", "win_other")],
    "appointer": [("訪問", "訪問数目標", "visits", None), ("アポ", "アポ数目標", "appo", None),
                  ("商談", "商談数目標_自アポ", "meet", None), ("契約", "契約数目標_自アポ", "win", None)],
}


def save(pdf, fig):
    """IPAゴシックには太字が無いため、太字指定の文字は同色の縁取りで太く見せてから保存する。"""
    for t in fig.findobj(Text):
        if t.get_fontweight() in ("bold", 700) and t.get_text():
            t.set_path_effects([pe.withStroke(linewidth=t.get_fontsize() / 22, foreground=t.get_color())])
    pdf.savefig(fig); plt.close(fig)


def wrap(text, width, max_lines=None):
    lines = textwrap.wrap(text.replace("\n", " "), width) if text else []
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]; lines[-1] = lines[-1][:-1] + "…"
    return "\n".join(lines)


SOURCE = ""  # 出典の脚注（build_deckで設定）


def slide(pdf_pages, title, sub, num):
    fig = plt.figure(figsize=(W, H))
    if SOURCE:
        fig.text(0.5, 0.008, SOURCE, color=SUB, fontsize=8, ha="center")
    fig.patch.set_facecolor("white")
    fig.patches.append(plt.Rectangle((0, 0.89), 1, 0.11, transform=fig.transFigure, color=NAVY))
    fig.text(0.035, 0.945, title, color="white", fontsize=26, fontweight="bold", va="center")
    fig.text(0.965, 0.945, sub, color="#C9D6E8", fontsize=13, ha="right", va="center")
    fig.text(0.965, 0.02, str(num), color=SUB, fontsize=10, ha="right")
    return fig


def card(fig, x, y, w, h, color=BG, edge=None, lw=0):
    fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.012",
                                      transform=fig.transFigure, facecolor=color, edgecolor=edge or color, linewidth=lw, zorder=-10))


def target_check(role, plan, r):
    """宣言した数字目標と実績の突き合わせ。[(表示名, 目標, 実績, 達成?)]"""
    out = []
    for label, col, own, other in TARGETS[role]:
        t = plan.get(col)
        if role == "closer" and other:
            t_other = plan.get(col.replace("_自アポ", "_他アポ"))
            if t is None and t_other is None:
                continue
            t, act = (t or 0) + (t_other or 0), r[own] + r[other]
        else:
            if t is None:
                continue
            act = r[own]
        out.append((label, int(t), int(act), act >= t))
    return out


# ---------- 1. 表紙 ----------
def cover(pdf, agency, cfg, n):
    fig = plt.figure(figsize=(W, H)); fig.patch.set_facecolor(NAVY)
    fig.text(0.06, 0.74, "週次MT", color="white", fontsize=54, fontweight="bold")
    fig.text(0.06, 0.64, f"{agency}　｜　対象：{cfg['week']}", color="#C9D6E8", fontsize=20)
    fig.text(0.06, 0.55, "今日のゴール：全員が「今週の宿題1つ」と「数字の目標」を決めて、BOTに送って帰る",
             color=GOLD, fontsize=17, fontweight="bold")
    steps = [("10分", "先週の宿題の\n答え合わせ"), ("5分", "チームの数字"), ("5分", "ランキング\n宣言達成者"),
             ("20分", "1人ずつ振り返り\n宿題決め"), ("5分", "BOTに送信\n宣言")]
    x0, w = 0.06, 0.168
    for i, (t, s) in enumerate(steps):
        x = x0 + i * (w + 0.012)
        card(fig, x, 0.13, w, 0.3, "#2A4A73")
        fig.text(x + 0.015, 0.385, f"STEP {i+1}", color=GOLD, fontsize=12, fontweight="bold")
        fig.text(x + 0.015, 0.29, s, color="white", fontsize=16, fontweight="bold", va="center", linespacing=1.4)
        fig.text(x + 0.015, 0.16, t, color="#C9D6E8", fontsize=14)
    fig.text(0.965, 0.03, str(n), color="#C9D6E8", fontsize=10, ha="right")
    save(pdf, fig)


# ---------- 2. 宿題の答え合わせ ----------
def review(pdf, members, mem, res, pdca, cfg, n):
    pages = [members[i:i+5] for i in range(0, len(members), 5)] or [[]]
    for pi, chunk in enumerate(pages):
        fig = slide(pdf, "① 先週の宿題の答え合わせ", cfg["week"] + (f"　{pi+1}/{len(pages)}" if len(pages) > 1 else ""), n); n += 1
        top = 0.84
        fig.text(0.035, top, "名前", fontsize=12, color=SUB); fig.text(0.16, top, "先週決めた宿題", fontsize=12, color=SUB)
        fig.text(0.56, top, "宣言した数字 → 実績（緑＝達成）", fontsize=12, color=SUB); fig.text(0.905, top, "評価", fontsize=12, color=SUB)
        rh = 0.148
        for i, name in enumerate(chunk):
            y = top - 0.03 - (i + 1) * rh
            card(fig, 0.03, y + 0.008, 0.94, rh - 0.016, BG)
            fig.text(0.04, y + rh / 2 + (0.015 if (pdca.get(name) or {}).get("last_eval") else 0), name,
                     fontsize=17, fontweight="bold", va="center", color=INK)
            plan = pdca.get(name)
            if plan and plan.get("last_eval"):
                fig.text(0.04, y + rh / 2 - 0.035, f"前回の評価 {plan['last_eval']}", fontsize=11, va="center",
                         color=RED if plan["last_eval"] == "×" else SUB)
            if not plan or not plan.get("action"):
                why = (plan or {}).get("missing_reason") or "記録なし"
                fig.text(0.16, y + rh / 2, f"データがありません\n理由：{why}\n→ MTの最初に口頭で先週の宿題を確認", fontsize=11.5,
                         color=RED, va="center", linespacing=1.4)
            else:
                fig.text(0.16, y + rh / 2, wrap(plan["action"], 24, 3), fontsize=12, va="center", color=INK, linespacing=1.35)
                for j, (lab, t, a, ok) in enumerate(target_check(mem[name]["role"], plan, res[name])):
                    x = 0.56 + j * 0.083
                    card(fig, x, y + 0.03, 0.077, rh - 0.06, "#E3F2EA" if ok else "#FBE3E3")
                    fig.text(x + 0.0385, y + rh / 2 + 0.018, lab, fontsize=10, ha="center", color=SUB)
                    fig.text(x + 0.0385, y + rh / 2 - 0.022, f"{t}→{a}", fontsize=13, ha="center", fontweight="bold",
                             color=GREEN if ok else RED)
            fig.text(0.92, y + rh / 2, "◎ ○ ×", fontsize=16, ha="center", va="center", color=LINE)
        fig.text(0.035, 0.03, "◎ やった＋数字が良くなった　○ やったが数字はまだ　× やらなかった（2週続けて×ならリーダーと1対1）",
                 fontsize=12, color=SUB)
        save(pdf, fig)
    return n


# ---------- 3. チームの数字 ----------
def team(pdf, agency, members, mem, res, cfg, all_tot, n):
    fig = slide(pdf, "② チームの数字", cfg["week"], n)
    ag, _ = B.agency_totals(res, mem, cfg)
    tot = all_tot if agency == ALL else ag.get(agency, [0] * 6)[:5]
    fig.text(0.035, 0.83, f"{agency}の先週の実績（{len(members)}名）", fontsize=15, fontweight="bold", color=INK)
    for i, (lab, v) in enumerate(zip(B.METRICS, tot)):
        x = 0.035 + i * 0.19
        card(fig, x, 0.6, 0.175, 0.2, BG)  # 売上は実質売上（キャンセル差し引き後）
        fig.text(x + 0.0875, 0.75, lab, fontsize=13, ha="center", color=SUB)
        fig.text(x + 0.0875, 0.645, f"{v:,.0f}" if lab != "売上(万円)" else f"{v:,.1f}", fontsize=34,
                 ha="center", fontweight="bold", color=NAVY)
    fig.text(0.035, 0.535, f"訪問→アポ率 {B.rate(tot[1], tot[0])}%　　商談→契約率 {B.rate(tot[3], tot[2])}%",
             fontsize=15, color=BLUE, fontweight="bold")
    axm = fig.add_axes([0.05, 0.08, 0.4, 0.33]); axw = fig.add_axes([0.55, 0.08, 0.4, 0.33])
    B.draw_progress(axm, axw, cfg, all_tot, fs=1.5)
    fig.text(0.035, 0.46, "全社の進捗（赤い線＝今日の時点で届いていたい位置）", fontsize=13, color=SUB)
    save(pdf, fig)
    return n + 1


# ---------- 4. 全社ランキング（表彰台） ----------
def ranking_categories(mem, res):
    field = [n for n in res if mem[n].get("is_manager") != "yes"]
    appointers = [n for n in field if mem[n]["role"] == "appointer"]
    closers = [n for n in res if mem[n]["role"] == "closer"]
    return [
        ("アポ数", "件", [(n, res[n]["appo"]) for n in field]),
        ("訪問→アポ率", "%", [(n, round(res[n]["appo"] / res[n]["visits"] * 100, 1)) for n in appointers if res[n]["visits"] >= 30]),
        ("契約数", "件", [(n, res[n]["win"]) for n in closers]),
        ("売上", "万円", [(n, round(res[n]["sales"], 1)) for n in res]),
    ]


def podium(fig, x, y, w, h, title, unit, rows, mem, agency):
    card(fig, x, y, w, h, BG)
    fig.text(x + 0.015, y + h - 0.05, title, fontsize=18, fontweight="bold", color=NAVY)
    rows = sorted([r for r in rows if r[1] > 0], key=lambda r: -r[1])
    if not rows:
        fig.text(x + w / 2, y + h / 2, "該当者なし", fontsize=14, color=SUB, ha="center"); return
    # 同点は同順位。3位までに入る人を全員出す（最大5名）
    ranked, rank = [], 0
    for i, (nm, v) in enumerate(rows):
        if i == 0 or v != rows[i - 1][1]:
            rank = i
        if rank >= 3 or len(ranked) >= 5:
            break
        ranked.append((rank, nm, v))
    lh = (h - 0.09) / max(3, len(ranked))
    for i, (rk, nm, v) in enumerate(ranked):
        cy = y + h - 0.1 - i * lh - lh / 2 + 0.02
        col, lab = MEDALS[rk]
        ax = fig.add_axes([x + 0.012, cy - 0.032, 0.036, 0.064]); ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        ax.add_patch(Circle((0.5, 0.5), 0.48, color=col)); ax.text(0.5, 0.5, str(rk + 1), ha="center", va="center", fontsize=17, color="white", fontweight="bold")
        size = (24 if rk == 0 else 19) if len(ranked) <= 3 else 16
        fig.text(x + 0.058, cy, nm, fontsize=size, fontweight="bold", va="center", color=INK)
        fig.text(x + 0.19, cy, mem[nm]["agency"], fontsize=11, va="center",
                 color=GOLD if mem[nm]["agency"] == agency else SUB, fontweight="bold" if mem[nm]["agency"] == agency else "normal")
        fig.text(x + w - 0.015, cy, f"{v:,.1f}{unit}" if unit != "件" else f"{int(v)}{unit}", fontsize=size, ha="right",
                 va="center", fontweight="bold", color=col if rk == 0 else NAVY)


def ranking(pdf, mem, res, agency, cfg, n):
    fig = slide(pdf, "③ 全社ランキング", cfg["week"] + "　全代理店の個人", n)
    cats = ranking_categories(mem, res)
    for i, (t, u, rows) in enumerate(cats):
        podium(fig, 0.035 + (i % 2) * 0.47, 0.08 + (1 - i // 2) * 0.4, 0.455, 0.38, t, u, rows, mem, agency)
    fig.text(0.035, 0.03, "訪問→アポ率は訪問30件以上のアポインターが対象　／　金色の代理店名＝自社のメンバー", fontsize=11, color=SUB)
    save(pdf, fig)
    return n + 1


# ---------- 5. 宣言達成者＋代理店ランキング ----------
def achievers(pdf, mem, res, pdca, cfg, n):
    fig = slide(pdf, "④ 宣言達成者　＆　代理店ランキング", cfg["week"], n)
    done = []
    for nm, plan in pdca.items():
        if nm not in res:
            continue
        chk = target_check(mem[nm]["role"], plan, res[nm])
        if chk and all(c[3] for c in chk):
            done.append(nm)
    card(fig, 0.035, 0.08, 0.44, 0.74, "#FFF8E1")
    fig.text(0.055, 0.76, "宣言した数字を全部クリア", fontsize=19, fontweight="bold", color=GOLD)
    fig.text(0.055, 0.715, "先週のMTで宣言した目標（訪問・アポ・商談・契約）を全部達成した人", fontsize=11, color=SUB)
    if done:
        for i, nm in enumerate(done[:10]):
            fig.text(0.07 + (i % 2) * 0.2, 0.62 - (i // 2) * 0.1, nm, fontsize=22, fontweight="bold", color=INK)
            fig.text(0.07 + (i % 2) * 0.2, 0.585 - (i // 2) * 0.1, mem[nm]["agency"], fontsize=10, color=SUB)
    else:
        fig.text(0.255, 0.45, "今週はまだいません\n次に名前が載るのは誰？", fontsize=18, ha="center", color=SUB, linespacing=1.6)
    ag, _ = B.agency_totals(res, mem, cfg)
    names = list(ag)
    for i, (idx, lab) in enumerate([(3, "1人当たり契約数"), (4, "1人当たり売上（万円）")]):
        ax = fig.add_axes([0.62, 0.5 - i * 0.4, 0.34, 0.27])
        vals = sorted(((round(ag[a][idx] / ag[a][5], 1), a) for a in names))
        ax.barh([f"{a}（{ag[a][5]}名）" for _, a in vals], [v for v, _ in vals],
                color=[BLUE] * (len(vals) - 1) + [GOLD])
        for y, (v, _) in enumerate(vals):
            ax.text(v, y, f" {v:,.1f}", va="center", fontsize=13, fontweight="bold")
        ax.set_title(lab, fontsize=15, loc="left", fontweight="bold")
        ax.set_xlim(0, (max(v for v, _ in vals) or 1) * 1.3); ax.tick_params(labelsize=12)
        for s in ("top", "right"): ax.spines[s].set_visible(False)
    save(pdf, fig)
    return n + 1


# ---------- 6. 個人スライド ----------
def person(pdf, name, mem, res, pdca, cfg, n):
    role = mem[name]["role"]; is_mgr = mem[name].get("is_manager") == "yes"
    tname = "アポインター" if role == "appointer" else "クローザー"
    fig = slide(pdf, f"⑤ {name}　（{tname}）", cfg["week"], n)
    r = res[name]
    if r.get("perf_days", 7) == 0:
        card(fig, 0.035, 0.3, 0.93, 0.45, "#FBE3E3")
        fig.text(0.5, 0.62, "データがありません", fontsize=30, fontweight="bold", color=RED, ha="center")
        fig.text(0.5, 0.48, f"理由：{cfg['week']}の実績DB（日報）に{name}さんの行が1日分もありません（日報が未入力）",
                 fontsize=15, color=INK, ha="center")
        fig.text(0.5, 0.39, "→ 日報を入力してもらい、MTでは口頭で先週の動きを確認する", fontsize=14, color=SUB, ha="center")
        save(pdf, fig)
        return n + 1
    grp_all = [res[m] for m in res if mem[m]["role"] == role]
    grp_act = [res[m] for m in res if mem[m]["role"] == role and mem[m].get("is_manager") != "yes"] or grp_all
    rows, worst = [], None
    for sp in B.rows_spec(role, is_mgr):
        if sp[2] != "r" or sp[5] or not sp[6]:
            continue
        grp = grp_act if sp[0] == "活動量（実数）" or sp[1].startswith(("訪問", "在宅", "対面", "対象", "対話")) else grp_all
        v, av = B._val(r, sp), B._avg(grp, sp)
        if v is None or not av or r[sp[4]] < 3:
            continue
        rows.append((sp[1], v, av))
        if worst is None or v / av < worst[1] / worst[2]:
            worst = (sp[1], v, av)
    # 左: 平均を100とした通過率のバー
    ax = fig.add_axes([0.19, 0.12, 0.36, 0.66])
    if rows:
        labs = [x[0] for x in rows][::-1]; ratios = [x[1] / x[2] * 100 for x in rows][::-1]
        cols = [RED if v < 70 else ("#E8A33D" if v < 90 else (GREEN if v > 110 else BLUE)) for v in ratios]
        ax.barh(labs, ratios, color=cols, height=0.6)
        ax.axvline(100, color=INK, lw=1.2, ls="--"); ax.text(100, -0.75, "全体平均", fontsize=10, ha="center", va="top")
        for y, (v, rr) in enumerate(zip(ratios, [x for x in rows][::-1])):
            ax.text(v + 2, y, f"{rr[1]:.1f}%（平均{rr[2]:.1f}%）", va="center", fontsize=11)
        ax.set_xlim(0, max(160, max(ratios) * 1.45)); ax.set_ylim(-1.2, max(len(labs), 5) - 0.4)
        ax.set_xticks([]); ax.tick_params(labelsize=12)
    else:
        ax.axis("off"); ax.text(0.5, 0.5, "母数が少なく比較できません", ha="center", fontsize=14, color=SUB)
    ax.set_title("通過率（点線＝全体平均）　赤＝平均の70%未満　黄＝90%未満", fontsize=13, loc="left", color=SUB, pad=12)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    # 右上: 実数
    keys = ([("訪問", "visits"), ("アポ", "appo")] if not is_mgr else []) + [("商談", "meet"), ("契約", "win"), ("売上", "sales")]
    for i, (lab, k) in enumerate(keys):
        x = 0.6 + i * (0.365 / len(keys))
        card(fig, x, 0.66, 0.365 / len(keys) - 0.008, 0.16, BG)
        fig.text(x + 0.365 / len(keys) / 2 - 0.004, 0.78, lab, fontsize=12, ha="center", color=SUB)
        fig.text(x + 0.365 / len(keys) / 2 - 0.004, 0.69, f"{r[k]:,.0f}" if k != "sales" else f"{r[k]:,.1f}",
                 fontsize=24, ha="center", fontweight="bold", color=NAVY)
    # 右中: 最優先の改善項目（平均の90%未満が無い人は「伸ばすならここ」）
    levers = B.growth_levers(name, res, mem) if not (worst and worst[1] / worst[2] < 0.9) else []
    card(fig, 0.6, 0.38, 0.365, 0.24, "#FBE3E3" if worst and worst[1] / worst[2] < 0.9 else "#E3F2EA")
    if levers:
        lv = levers[0]
        fig.text(0.615, 0.575, "改善項目なし ▶ 伸ばすならここ", fontsize=13, color=GREEN, fontweight="bold")
        fig.text(0.615, 0.5, lv[0], fontsize=24, fontweight="bold", color=INK)
        if lv[6] == "率":
            fig.text(0.615, 0.45, f"{lv[1]:.1f}% → トップ {lv[2]:.1f}%（{lv[3]}）", fontsize=13, color=INK)
        elif lv[6] == "量":
            fig.text(0.615, 0.45, f"{lv[1]:.0f}件 → トップ {lv[2]:.0f}件（{lv[3]}）", fontsize=13, color=INK)
        else:
            fig.text(0.615, 0.45, f"率も量も全社トップ。1割増：{lv[1]:.0f}→{lv[2]:.0f}件", fontsize=13, color=INK)
        fig.text(0.615, 0.405, f"先週の数字なら {lv[5]} +{lv[4]:.1f}件", fontsize=14, fontweight="bold", color=GREEN)
    elif worst:
        fig.text(0.615, 0.575, "最優先の改善項目", fontsize=13, color=SUB)
        fig.text(0.615, 0.48, worst[0], fontsize=26, fontweight="bold", color=INK)
        fig.text(0.615, 0.415, f"自分 {worst[1]:.1f}%　／　平均 {worst[2]:.1f}%（平均の{worst[1]/worst[2]*100:.0f}%）", fontsize=13, color=INK)
    else:
        fig.text(0.615, 0.575, "最優先の改善項目", fontsize=13, color=SUB)
        fig.text(0.615, 0.47, "数字を見て、自分で1つ決める", fontsize=18, fontweight="bold", color=INK)
    # 右下: リーダーの4つの質問
    card(fig, 0.6, 0.08, 0.365, 0.27, "white", LINE, 1.2)
    qs = (["1. 平均より高い項目は、何が効いている？", "2. 「伸ばすならここ」を上げるには何を変える？", "3. 今週、何を変える？（いつ・誰と・何を）", "4. 数字の目標は？"]
          if levers else
          ["1. 一番低い項目はどれ？", "2. なぜ低いと思う？（価格で止めない）", "3. 今週、何を変える？（いつ・誰と・何を）", "4. 数字の目標は？"])
    fig.text(0.615, 0.31, "この順番で聞く", fontsize=12, color=SUB)
    for i, q in enumerate(qs):
        fig.text(0.615, 0.255 - i * 0.045, q, fontsize=14, color=INK)
    save(pdf, fig)
    return n + 1


# ---------- 7. みんなの学び ----------
def learnings(pdf, mem, pdca, cfg, n):
    items = [(nm, d.get("cause", "")) for nm, d in pdca.items() if d.get("cause")]
    if not items:
        return n
    pages = [items[i:i+6] for i in range(0, len(items), 6)]
    for pi, chunk in enumerate(pages):
        fig = slide(pdf, "⑥ 先週のMTで出たみんなの学び", "全社" + (f"　{pi+1}/{len(pages)}" if len(pages) > 1 else ""), n); n += 1
        for i, (nm, txt) in enumerate(chunk):
            x, y = 0.035 + (i % 3) * 0.315, 0.47 - (i // 3) * 0.39
            card(fig, x, y, 0.3, 0.36, BG)
            fig.text(x + 0.015, y + 0.31, nm, fontsize=16, fontweight="bold", color=NAVY)
            fig.text(x + 0.015, y + 0.275, mem[nm]["agency"], fontsize=10, color=SUB)
            fig.text(x + 0.015, y + 0.245, wrap(txt, 19, 8), fontsize=12, va="top", color=INK, linespacing=1.45)
        save(pdf, fig)
    return n


# ---------- 8. BOTへ送る内容 ----------
def send_guide(pdf, cfg, n):
    fig = slide(pdf, "⑦ MTの最後に、BOTへ送る内容", "今朝LINEに届いた記入テンプレをコピーして書く", n)
    blocks = [
        ("①", "先週の振り返り・学び", "うまくいったこと／いかなかったことと、その理由を1〜2行で",
         "例：価格を聞かれてすぐ答えたら、そこで話が終わった。先に「何と比べていますか」と聞けば続いた"),
        ("②", "今週の宿題（1つだけ）", "いつ・誰と・何を。来週のMTで「やった／やってない」を答えられる形で",
         "例：木曜と金曜の朝、〇〇さんと価格質問の切り返しロープレを10分ずつ"),
        ("③", "今週の数字の目標（日曜まで）", "テンプレに並んでいる項目に数字だけ入れる",
         "例：訪問数：60　アポ数：6　商談数：3　契約数：1"),
    ]
    for i, (no, t, d, ex) in enumerate(blocks):
        y = 0.62 - i * 0.235
        card(fig, 0.035, y, 0.93, 0.21, BG)
        fig.text(0.055, y + 0.105, no, fontsize=40, fontweight="bold", color=GOLD, va="center")
        fig.text(0.115, y + 0.15, t, fontsize=21, fontweight="bold", color=NAVY, va="center")
        fig.text(0.115, y + 0.098, d, fontsize=14, color=INK, va="center")
        fig.text(0.115, y + 0.045, ex, fontsize=13, color=BLUE, va="center")
    fig.text(0.035, 0.035, "送ったら「記録しました」と返ってくればOK。送り忘れた人には、水曜18時に1回だけリマインドが届きます。",
             fontsize=13, color=SUB)
    save(pdf, fig)
    return n + 1


# ---------- 9. 今週の動き方 ----------
def week_flow(pdf, cfg, n):
    fig = slide(pdf, "⑧ 今週の動き方", "毎週同じリズムで回す", n)
    days = [
        ("水", "週次MT", NAVY, ["朝イチに週次MT", "宿題と数字の目標を決める", "MTの最後にBOTへ送る"]),
        ("木", "アポ中心の日", TEAL, ["アポインターの宿題を実行", "ロープレ・同行はこの日", "夕方にリーダーが訪問とアポ数を確認"]),
        ("金", "クロ中心の日", BLUE, ["週末の商談に向けて準備", "商談分析の断り文句と転換点を見直す", "切り返しロープレを1本"]),
        ("土日", "商談・訪問", "#5B7DB1", ["宿題を実行しながら本番", "商談は録音してBOTへ送る", "実績は毎日いつもどおり報告"]),
        ("月火", "資料づくり", SUB, ["今川が実績を締めて資料を作る", "水曜の朝にLINEで届く", ""]),
    ]
    w = 0.178
    for i, (d, t, c, lines) in enumerate(days):
        x = 0.035 + i * (w + 0.01)
        card(fig, x, 0.62, w, 0.2, c)
        fig.text(x + w / 2, 0.75, d, fontsize=30, fontweight="bold", color="white", ha="center", va="center")
        fig.text(x + w / 2, 0.66, t, fontsize=15, color="white", ha="center", va="center")
        card(fig, x, 0.1, w, 0.49, BG)
        for j, l in enumerate(lines):
            fig.text(x + 0.012, 0.53 - j * 0.12, wrap(l, 12), fontsize=13, color=INK, va="top", linespacing=1.4)
    save(pdf, fig)
    return n + 1


def build_deck(path, agency, members, mem, res, pdca, cfg):
    global SOURCE
    SOURCE = ("出典：Notion 実績DB（訪問・アポ・売上＝キャンセルを引いた実質売上。他アポは半額ずつ）／商談一覧（商談・契約＝1件1回、クーリングオフ除く）"
              + (f"　{cfg['data_as_of']}時点" if cfg.get("data_as_of") else "")
              + (f"　2つのDBの突き合わせ：{cfg['check_status']}" if cfg.get("check_status") else ""))
    _, all_tot = B.agency_totals(res, mem, cfg)
    with PdfPages(path) as pdf:
        n = 1
        cover(pdf, agency, cfg, n); n += 1
        n = review(pdf, members, mem, res, pdca, cfg, n)
        n = team(pdf, agency, members, mem, res, cfg, all_tot, n)
        n = ranking(pdf, mem, res, agency, cfg, n)
        n = achievers(pdf, mem, res, pdca, cfg, n)
        for m in members:
            n = person(pdf, m, mem, res, pdca, cfg, n)
        n = learnings(pdf, mem, pdca, cfg, n)
        n = send_guide(pdf, cfg, n)
        week_flow(pdf, cfg, n)


def main(inp, out, strict=False):
    mem, res, cfg, pdca = B.load_all(inp, strict)
    pub = os.path.join(out, "public"); os.makedirs(pub, exist_ok=True)
    rows = []
    for agency in dict.fromkeys(m["agency"] for m in mem.values()):
        members = [n for n, m in mem.items() if m["agency"] == agency]
        members.sort(key=lambda n: (mem[n]["role"] != "appointer", mem[n].get("is_manager") == "yes"))
        fn = f"{secrets.token_urlsafe(9)}.pdf"
        build_deck(os.path.join(pub, fn), agency, members, mem, res, pdca, cfg)
        rows.append([agency, fn]); print(f"スライド {agency}: {len(members)}人 → {fn}")
    # 全社版（全員分を1つに。リーダー会議・今川さん用。deliver.pyは代理店名でしか引かないので配信先には混ざらない）
    everyone = sorted(mem, key=lambda n: (mem[n]["agency"], mem[n]["role"] != "appointer", mem[n].get("is_manager") == "yes"))
    fn = f"{secrets.token_urlsafe(9)}.pdf"
    build_deck(os.path.join(pub, fn), ALL, everyone, mem, res, pdca, cfg)
    rows.append([ALL, fn]); print(f"スライド {ALL}: {len(everyone)}人 → {fn}")
    with open(os.path.join(out, "slides_manifest.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["agency", "file"]); w.writerows(rows)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--strict" in sys.argv)
