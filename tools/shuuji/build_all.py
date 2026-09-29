"""使い方: python build_all.py 入力フォルダ 出力フォルダ
入力フォルダに members.csv / results.csv / config.csv を置く(環境変数 MEMBERS_URL / RESULTS_URL / CONFIG_URL があればGoogleスプレッドシートのCSVを優先)。
--strict: config の data_week_start が先週月曜でなければ中止(未更新データの誤送信防止)。
公開するのは out/public だけ(manifest.csv にはLINE IDが入るので公開しない)。
出力: 代理店ごとのPDF(推測できないランダム名) + manifest.csv(送信先一覧)"""
import csv, json, sys, secrets, os, io, datetime, calendar, urllib.request
from zoneinfo import ZoneInfo
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, KeepInFrame

FONT = next(p for p in ["/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"] if os.path.exists(p))
pdfmetrics.registerFont(TTFont("JPF", FONT)); fm.fontManager.addfont(FONT)
plt.rcParams["font.family"] = "IPAGothic"
NAVY = colors.HexColor("#1F3A5F"); LGRAY = colors.HexColor("#F2F4F7"); MID = colors.HexColor("#C9D1DC"); YEL = colors.HexColor("#FFF4CC")
def S(n, size=10, color=colors.black, **k): return ParagraphStyle(n, fontName="JPF", fontSize=size, leading=size*1.5, textColor=color, **k)
H1, H2, B, SM = S("h1", 18, NAVY), S("h2", 13, colors.white), S("b", 9.5), S("sm", 8, colors.grey)
TH, TD, TDL = S("th", 8.5, colors.white, alignment=1), S("td", 9, alignment=1), S("tdl", 9)
LAST = "JPF"
METRICS = ["訪問数", "アポ数", "商談作成数", "契約数", "売上(万円)"]

def band(t):
    x = Table([[Paragraph(t, H2)]], colWidths=[180*mm])
    x.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), NAVY), ("LEFTPADDING", (0,0), (-1,-1), 8), ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)])); return x
def grid(data, w, header=True, extra=None):
    x = Table(data, colWidths=w)
    st = [("GRID", (0,0), (-1,-1), 0.5, MID), ("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]
    if header: st.append(("BACKGROUND", (0,0), (-1,0), NAVY))
    x.setStyle(TableStyle(st + (extra or []))); return x
def blank(label, rows=3, h=11*mm):
    x = Table([[Paragraph(label, TDL)]] + [[""]]*rows, colWidths=[180*mm], rowHeights=[7*mm]+[h]*rows)
    x.setStyle(TableStyle([("BOX", (0,0), (-1,-1), 0.5, MID), ("LINEBELOW", (0,0), (-1,-1), 0.4, MID), ("BACKGROUND", (0,0), (-1,0), LGRAY)])); return x
def rate(a, b): return round(a/b*100, 1) if b else 0.0
def hdr(*c): return [Paragraph(x, TH) for x in c]
def pct_row(label, mine, avg, unit=""):
    p = round(mine/avg*100) if avg else 0
    return [Paragraph(label, TDL), Paragraph(f"{mine:g}{unit}", TD), Paragraph(f"{avg:g}{unit}", TD), Paragraph(f"<b>{p}%</b>", TD),
            Paragraph("▼ 要改善" if p < 90 else ("● 平均" if p < 110 else "▲ 好調"), TD)]
def plan_table(): return grid([hdr("いつ", "誰と", "何をやるか")] + [[""]*3]*3, [40*mm, 40*mm, 100*mm])
def goal_table(rows): return grid([hdr("項目", "先週実績", "今週目標", "水曜時点", "週末結果")] + [[Paragraph(l, TDL), Paragraph(v, TD), "", "", ""] for l, v in rows], [40*mm]+[35*mm]*4)

def bar(title, names, vals, fn, color):
    fig, ax = plt.subplots(figsize=(3.6, 2.0), dpi=200)
    o = sorted(range(len(vals)), key=lambda i: -vals[i]); n = [names[i] for i in o][::-1]; v = [vals[i] for i in o][::-1]
    cs = [color]*len(v); cs[-1] = "#E0A100"
    ax.barh(n, v, color=cs)
    for i, x in enumerate(v): ax.text(x, i, f" {x:,.1f}", va="center", fontsize=7)
    ax.set_title(title, fontsize=9, loc="left", fontweight="bold")
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax.tick_params(labelsize=7); ax.set_xlim(0, (max(v) or 1)*1.2); fig.tight_layout(); fig.savefig(fn); plt.close(fig)


def draw_progress(ax_m, ax_w, cfg, tot, fs=1.0):
    """左=月進捗、右=週進捗。契約数と売上を『目標に対する%』で並べる"""
    week_act = {"契約数": tot[3], "売上(万円)": tot[4]}
    for ax, title, tgt, act, mark in [(ax_m, f"月進捗（{cfg.get('month_label', '今月')}）", cfg["month_target"], cfg["month_actual"], cfg["month_elapsed"]),
                                      (ax_w, "週進捗（先週）", cfg["week_target"], week_act, None)]:
        ks = list(tgt); pcts = [act[k]/tgt[k]*100 if tgt[k] else 0 for k in ks]
        ys = [1, 0]
        ax.barh(ys, [100, 100], color="#E4E8EE", height=0.45)
        ax.barh(ys, pcts, color=["#1F3A5F", "#3B8EA5"], height=0.45)
        if mark is not None:
            ax.axvline(mark*100, color="#D64545", ls="--", lw=1.6*fs); ax.text(mark*100, 1.62, "現時点の目安", color="#D64545", fontsize=8*fs, ha="center")
        for y, k, p in zip(ys, ks, pcts):
            ax.text(0, y+0.36, f"{k}　{act[k]:,.0f} / {tgt[k]:,.0f}　（達成率 {p:.0f}%）", fontsize=9*fs, va="bottom", fontweight="bold")
        ax.set_xlim(0, max(120, max(pcts)*1.08)); ax.set_ylim(-0.4, 1.9); ax.set_yticks([]); ax.tick_params(labelsize=7*fs)
        ax.set_title(title, fontsize=11*fs, loc="left", fontweight="bold", pad=8)
        for sp in ("top", "right", "left"): ax.spines[sp].set_visible(False)

# ---------- 共通ページ ----------
def common_pages(res, mem, cfg, tmp):
    week = cfg["week"]
    ag = {}
    for name, r in res.items():
        a = mem[name]["agency"]; d = ag.setdefault(a, [0]*5 + [0])
        for i, k in enumerate(["visits", "appo", "meet", "win", "sales"]): d[i] += r[k]
        d[5] += 1
    tot = [sum(v[i] for v in ag.values()) for i in range(5)]
    p1 = [Paragraph("週次ミーティング PDCAシート", H1), Paragraph(week, B), Spacer(1, 6*mm), band("今日のゴール"), Spacer(1, 3*mm),
          Paragraph("全員が「今週やること（誰と・いつ・何を）」と「数字の目標」を決めて帰る。", B), Spacer(1, 5*mm), band("進行（60分）"), Spacer(1, 3*mm),
          grid([hdr("時間", "内容", "使うページ")] + [[Paragraph(a, TD), Paragraph(b, TDL), Paragraph(c, TD)] for a, b, c in [
              ("10分", "全体結果・月進捗・代理店ランキング発表", "共通ページ"),
              ("20分", "【アポインタ】平均比の確認 → 課題特定 → アクション・目標設定", "個人シート"),
              ("20分", "【クローザー】商談反省の共有 → 改善点特定 → アクション設定", "個人シート"),
              ("10分", "宣言（1人ずつ今週の目標を読み上げ）・先週アクションの確認", "最終ページ")]], [22*mm, 116*mm, 42*mm]),
          Spacer(1, 5*mm), band("PDCAの回し方（各自）"), Spacer(1, 3*mm),
          grid([[Paragraph(a, TH), Paragraph(b, TDL)] for a, b in [("Check", "先週の数字を平均と比べ、一番低い項目を1つ決める"), ("Action", "その項目を上げる行動を「いつ・誰と・何を」で書く"),
                ("Plan", "今週の定量目標を数字で置く"), ("Do", "水曜に進捗確認、来週の頭に振り返る")]], [22*mm, 158*mm], header=False, extra=[("BACKGROUND", (0,0), (0,-1), NAVY)])]
    fig, axs = plt.subplots(1, 2, figsize=(7.2, 2.4), dpi=200)
    draw_progress(axs[0], axs[1], cfg, tot); fig.tight_layout(); fig.savefig(f"{tmp}/month.png"); plt.close(fig)
    p2 = [Paragraph("① 全体結果と月・週の進捗", H1), Spacer(1, 3*mm), band("先週の全体結果"), Spacer(1, 3*mm),
          grid([hdr(*METRICS), [Paragraph(f"{x:,}", S("k", 14, NAVY, alignment=1)) for x in tot]], [36*mm]*5), Spacer(1, 4*mm),
          grid([hdr("訪問→アポ率", "商談→契約率"), [Paragraph(f"{rate(tot[1], tot[0])}%", TD), Paragraph(f"{rate(tot[3], tot[2])}%", TD)]], [90*mm]*2),
          Spacer(1, 5*mm), band("月の進捗と週の進捗（赤破線＝月の現時点の目安）"), Spacer(1, 3*mm), Image(f"{tmp}/month.png", 180*mm, 60*mm), Spacer(1, 4*mm), blank("ひとこと（先週の全体をどう見るか）", 2)]
    names = list(ag); labels = [f"{n}({ag[n][5]}名)" for n in names]; cols = ["#1F3A5F", "#2E6DA4", "#3B8EA5", "#5B7DB1", "#8C5E9E"]; imgs = []
    for i, m in enumerate(METRICS):
        fn = f"{tmp}/r{i}.png"; bar(f"1人当たり{m}", labels, [round(ag[n][i]/ag[n][5], 1) for n in names], fn, cols[i]); imgs.append(Image(fn, 88*mm, 49*mm))
    imgs.append(Paragraph("※ 金色＝1位　／　( )内は稼働人数", SM))
    p3 = [Paragraph("② 代理店ランキング（先週・1人当たり）", H1), Paragraph("稼働人数で割って比較", SM), Spacer(1, 2*mm), Table([imgs[0:2], imgs[2:4], imgs[4:6]], colWidths=[90*mm, 90*mm])]
    return [p1, p2, p3]

# ---------- 個人シート ----------
RED_BG = colors.HexColor("#F8D0D0")
def rows_spec(role):
    """(グループ, 表示名, 種類n=実数/r=率, 分子キー, 分母キー, 少ない方が良いか, 評価文の候補にするか)"""
    R = []; g = "活動量（実数）"
    for k, l in [("visits", "訪問数"), ("home", "在宅数"), ("face", "対面数"), ("target", "対象数"), ("talk", "対話数"), ("appo", "アポ数")]:
        R.append((g, l, "n", k, None, False, True))
    g = "商談・契約（実数）"
    pairs = [("meet_own", "商談数（自アポ）"), ("meet_other", "商談数（他アポ）"), ("win_own", "契約数（自アポ）"), ("win_other", "契約数（他アポ）")] if role == "closer" else [("meet", "商談数"), ("win", "契約数")]
    for k, l in pairs: R.append((g, l, "n", k, None, False, True))
    g = "結果（実数）"
    R += [(g, "クーリングオフ数", "n", "cooloff", None, True, False), (g, "審査落ち数", "n", "screen_fail", None, True, False), (g, "売上（万円）", "n", "sales", None, False, True)]
    g = "通過率（前の段階からの割合）"
    for a, b, l in [("home", "visits", "訪問→在宅率"), ("face", "home", "在宅→対面率"), ("target", "face", "対面→対象率"), ("talk", "target", "対象→対話率"), ("appo", "talk", "対話→アポ率")]:
        R.append((g, l, "r", a, b, False, True))
    R.append((g, "訪問→アポ率（通算）", "r", "appo", "visits", False, False))
    if role == "closer":
        R += [(g, "商談→契約率（自アポ）", "r", "win_own", "meet_own", False, True), (g, "商談→契約率（他アポ）", "r", "win_other", "meet_other", False, True), (g, "商談→契約率（計）", "r", "win", "meet", False, False)]
    else:
        R.append((g, "商談→契約率", "r", "win", "meet", False, True))
    R += [(g, "クーリングオフ率（対契約）", "r", "cooloff", "win", True, True), (g, "審査落ち率（対契約）", "r", "screen_fail", "win", True, True)]
    return R
def _val(r, sp): 
    if sp[2] == "n": return r[sp[3]]
    return r[sp[3]] / r[sp[4]] * 100 if r[sp[4]] > 0 else None
def _avg(grp, sp):
    if sp[2] == "n": return sum(r[sp[3]] for r in grp) / len(grp)
    d = sum(r[sp[4]] for r in grp); return sum(r[sp[3]] for r in grp) / d * 100 if d > 0 else None
def _fmt(v, sp):
    if v is None: return "-"
    if sp[2] == "r": return f"{v:.1f}%"
    return f"{v:.1f}" if (sp[3] == "sales" or v != int(v)) else f"{int(v)}"
def judge(v, avg, sp):
    """戻り値: (ratio or None, level, label)  level: severe/mild/good/ok/na"""
    bad = sp[5]
    if v is None or avg is None or avg == 0 or (bad and sp[2] == "n"): return None, "na", "参考" if (bad and sp[2] == "n") else "—"
    ratio = v / avg
    if not bad:
        if ratio < 0.7: return ratio, "severe", "▼▼ 大幅に低い"
        if ratio < 0.9: return ratio, "mild", "▼ やや低い"
        if ratio > 1.1: return ratio, "good", "▲ 好調"
        return ratio, "ok", "● 平均"
    if ratio > 1.5 and v > 0: return ratio, "severe", "▲▲ 多い（要注意）"
    if ratio > 1.2 and v > 0: return ratio, "mild", "▲ やや多い"
    if ratio < 0.8: return ratio, "good", "▼ 良好"
    return ratio, "ok", "● 平均"

def sheet(name, res, mem, cfg):
    """1人分 = 2ページ(①数字の比較と定量評価 / ②原因・アクション・目標)"""
    r, role, agency = res[name], mem[name]["role"], mem[name]["agency"]
    grp = [res[n] for n in res if mem[n]["role"] == role]
    tname = "アポインタ" if role == "appointer" else "クローザー"
    data = [hdr("項目", "自分", "全体平均", "平均比", "判定")]; style = []; found = []; last_g = None; TDs = S("tds", 8.5, alignment=1); TDLs = S("tdls", 8.5)
    for sp in rows_spec(role):
        if sp[0] != last_g:
            data.append([Paragraph(f"<b>{sp[0]}</b>", TDLs), "", "", "", ""]); i = len(data)-1
            style += [("SPAN", (0, i), (-1, i)), ("BACKGROUND", (0, i), (-1, i), LGRAY)]; last_g = sp[0]
        v, av = _val(r, sp), _avg(grp, sp); ratio, lv, lab = judge(v, av, sp)
        if sp[2] == "r" and r[sp[4]] < 3: ratio, lv, lab = None, "na", "母数少（参考）"
        data.append([Paragraph(sp[1], TDLs), Paragraph(_fmt(v, sp), TDs), Paragraph(_fmt(av, sp), TDs), Paragraph(f"<b>{ratio*100:.0f}%</b>" if ratio is not None else "-", TDs), Paragraph(lab, TDs)])
        i = len(data)-1
        if lv == "severe": style.append(("BACKGROUND", (0, i), (-1, i), RED_BG))
        elif lv == "mild": style.append(("BACKGROUND", (0, i), (-1, i), YEL))
        if lv in ("severe", "mild") and sp[6]: found.append((ratio, lv, sp, v, av))
    tbl = Table(data, colWidths=[62*mm, 26*mm, 26*mm, 26*mm, 40*mm], repeatRows=1)
    tbl.setStyle(TableStyle([("GRID", (0,0), (-1,-1), 0.4, MID), ("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("TOPPADDING", (0,0), (-1,-1), 2.2), ("BOTTOMPADDING", (0,0), (-1,-1), 2.2), ("BACKGROUND", (0,0), (-1,0), NAVY)] + style))
    # ---- 定量評価(自動) ----
    def key(x): return x[0] if not x[2][5] else -x[0]          # 低い順 / 多い順
    sev = sorted([x for x in found if x[1] == "severe"], key=key); mild = sorted([x for x in found if x[1] == "mild"], key=key)
    lines = []
    for ratio, lv, sp, v, av in sev[:3]:
        if sp[5]: lines.append(f"<b>{sp[1]}</b>：自分 {_fmt(v, sp)}／平均 {_fmt(av, sp)}（平均の{ratio*100:.0f}%）。平均の1.5倍を超えています。契約の質・説明の仕方を確認してください。")
        else: lines.append(f"<b>{sp[1]}</b>：自分 {_fmt(v, sp)}／平均 {_fmt(av, sp)}（平均の{ratio*100:.0f}%）。平均の70%未満で、極端に低い状態です。")
    if not sev:
        if mild: lines.append("平均の70%を下回る項目はありません。平均を下回る項目：" + "、".join(f"{x[2][1]}（平均の{x[0]*100:.0f}%）" for x in mild[:3]) + "。")
        else: lines.append("平均の70%を下回る項目も、平均の1.5倍を超える項目もありません。")
    top = (sev or mild)[:3]
    focus = f"最優先の改善項目：{top[0][2][1]}" if top else "最優先の改善項目：先週の数字を見て、自分で1つ決める"
    ev = [[Paragraph("<b>定量評価（自動）</b>", TDLs)], [Paragraph(focus, S("f", 10, NAVY))]] + [[Paragraph("・" + l, S("l", 8.5, lead=13))] for l in lines] + \
         [[Paragraph(f"※比較対象＝{tname}全員の平均。通過率は全員分を合算して計算。平均の70%未満＝極端に低い／90%未満＝やや低い。クーリングオフ・審査落ちは平均の1.5倍超で要注意。分母が3件未満の率は「母数少」として判定しない。", SM)]]
    evt = Table(ev, colWidths=[180*mm]); evt.setStyle(TableStyle([("BOX", (0,0), (-1,-1), 0.8, NAVY), ("BACKGROUND", (0,0), (-1,0), LGRAY), ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3)]))
    p1 = [Paragraph(f"③ {tname}個人シート（1/2）　数字の比較", H1), Paragraph(f"氏名：{name}　／　{agency}　／　{cfg['week']}", B), Spacer(1, 2*mm), tbl, Spacer(1, 3*mm), evt]
    # ---- 2ページ目 ----
    goals = [(x[2][1], _fmt(x[3], x[2])) for x in (sev or mild)[:3]]
    if not goals:
        goals = [(sp[1], _fmt(_val(r, sp), sp)) for sp in rows_spec(role) if sp[1] in ("訪問数", "アポ数", "契約数（自アポ）", "契約数")][:3]
    p2 = [Paragraph(f"③ {tname}個人シート（2/2）　改善アクション", H1), Paragraph(f"氏名：{name}　／　{agency}　／　{cfg['week']}", B), Spacer(1, 3*mm)]
    if role == "closer":
        p2 += [band("STEP1｜失注した商談の振り返り（商談反省報告から転記）"), Spacer(1, 2*mm), grid([hdr("お客様/案件", "決まらなかった理由", "契約にするには何をすべきだったか")] + [[""]*3]*3, [40*mm, 65*mm, 75*mm]),
               Spacer(1, 2*mm), Paragraph("理由は「価格」で止めず、「何が不安だったか／誰が決める人か／次の一手は何か」まで掘る。", SM), Spacer(1, 3*mm)]
    p2 += [blank("STEP2｜" + focus.replace("最優先の改善項目：", "") + " が低い原因（なぜ？）" if top else "STEP2｜一番低い項目とその原因", 2), Spacer(1, 3*mm),
           band("STEP3｜今週のアクションプラン（いつ・誰と・何を）"), Spacer(1, 2*mm), plan_table(), Spacer(1, 2*mm),
           Paragraph("例：ロープレ／同行／トークスクリプト見直し／エリア変更／失注先へのフォロー など", SM), Spacer(1, 3*mm),
           band("STEP4｜今週の定量目標（重点項目）"), Spacer(1, 2*mm), goal_table(goals)]
    return [p1, p2]

def _format_pdca_targets(data):
    """{"訪問数目標":80, "商談数目標_自アポ":5, "商談数目標_他アポ":3, ...} を
    「訪問80・アポ15・商談5+3・契約2+1」のような1行に圧縮する（アポインターは
    他アポがNoneなので単一の数字だけになる）。"""
    parts = []
    v = data.get("訪問数目標")
    if v is not None: parts.append(f"訪問{int(v)}")
    a = data.get("アポ数目標")
    if a is not None: parts.append(f"アポ{int(a)}")
    mo, mt = data.get("商談数目標_自アポ"), data.get("商談数目標_他アポ")
    if mo is not None or mt is not None:
        parts.append(f"商談{int(mo or 0)}+{int(mt)}" if mt is not None else f"商談{int(mo or 0)}")
    wo, wt = data.get("契約数目標_自アポ"), data.get("契約数目標_他アポ")
    if wo is not None or wt is not None:
        parts.append(f"契約{int(wo or 0)}+{int(wt)}" if wt is not None else f"契約{int(wo or 0)}")
    return "・".join(parts)

def review_page(members, pdca):
    """⑤先週アクションの振り返り。pdca（名前→前回のアクションプラン・数値目標）があれば
    「先週決めたアクション」列に、アクション文＋数値目標（【目標】訪問80・アポ15…）を
    自動転記する（週次PDCA記録DBからLINE返信を集めた結果。「結果（数字）」列は、数字は
    PDF内の他の表に既に出ているため置かない。ユーザー指示）"""
    rows = [hdr("メンバー", "先週決めたアクション", "やった？", "次にどうする")]
    for name in members:
        data = (pdca or {}).get(name)
        if not data:
            cell = "（記録なし）"
        else:
            targets = _format_pdca_targets(data)
            cell = data["action"] + (f"\n【目標】{targets}" if targets else "")
        rows.append([Paragraph(name, TDL), Paragraph(cell, TDL), "", ""])
    return [Paragraph("⑤ 先週アクションの振り返り（今日のMTで記入）", H1), Spacer(1, 3*mm),
            grid(rows, [22*mm, 78*mm, 18*mm, 62*mm]), Spacer(1, 4*mm), blank("気づき・共有したいこと", 3)]

def write_pdf(path, pages):
    story = []
    for i, p in enumerate(pages):
        if i: story.append(PageBreak())
        story.append(KeepInFrame(180*mm, 265*mm, p, mode='shrink'))
    SimpleDocTemplate(path, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm, topMargin=14*mm, bottomMargin=14*mm, title="週次ミーティング PDCAシート").build(story)

def load(inp, fname, env):
    """環境変数(URL)があればそこから、なければ入力フォルダから読む"""
    url = os.environ.get(env, "")
    if url: return urllib.request.urlopen(url, timeout=30).read().decode("utf-8-sig")
    p = os.path.join(inp, fname)
    return open(p, encoding="utf-8-sig").read() if os.path.exists(p) else None

def build_cfg(inp, strict):
    """config: key,value のCSV(またはconfig.json)。週ラベルと月経過率は自動計算"""
    now = datetime.datetime.now(ZoneInfo("Asia/Tokyo")).date()
    last_mon = now - datetime.timedelta(days=now.weekday() + 7)
    last_sun = last_mon + datetime.timedelta(days=6)
    week = f"{last_mon.month}/{last_mon.day}〜{last_sun.month}/{last_sun.day}（先週）"
    txt = load(inp, "config.csv", "CONFIG_URL")
    if txt is None:
        cfg = json.load(open(f"{inp}/config.json", encoding="utf-8")); cfg["week"] = week; cfg.setdefault("month_label", "今月"); cfg.setdefault("week_target", {k: v*7/30 for k, v in cfg["month_target"].items()}); return cfg
    kv = {r["key"]: r["value"] for r in csv.DictReader(io.StringIO(txt))}
    if strict and kv.get("data_week_start") != last_mon.isoformat():
        sys.exit(f"中止: 入力データの週が古い/未更新です (data_week_start={kv.get('data_week_start')} / 期待値={last_mon.isoformat()})。送信していません。")
    ndays = calendar.monthrange(now.year, now.month)[1]
    wt = lambda key, mkey: float(kv[key]) if kv.get(key) else float(kv[mkey]) * 7 / ndays   # 週目標が未入力なら月目標×7/日数
    return {"week": week, "month_elapsed": now.day / ndays, "month_label": f"{now.month}月",
            "week_target": {"契約数": wt("week_target_contracts", "target_contracts"), "売上(万円)": wt("week_target_sales", "target_sales")},
            "month_target": {"契約数": float(kv["target_contracts"]), "売上(万円)": float(kv["target_sales"])},
            "month_actual": {"契約数": float(kv["actual_contracts"]), "売上(万円)": float(kv["actual_sales"])}}

PDCA_TARGET_COLS = ["訪問数目標", "アポ数目標", "商談数目標_自アポ", "商談数目標_他アポ",
                     "契約数目標_自アポ", "契約数目標_他アポ"]

def load_pdca(inp):
    """pdca.csv（name,action,訪問数目標,...。週次PDCA記録DBの前回分。notion_to_csv.pyが作る）
    を読む。無ければ空辞書（新規プロジェクトやサンプル実行では「（記録なし）」表示になる
    だけで、エラーにはしない）"""
    txt = load(inp, "pdca.csv", "")
    if txt is None:
        return {}
    out = {}
    for r in csv.DictReader(io.StringIO(txt)):
        data = {"action": r.get("action", "")}
        for col in PDCA_TARGET_COLS:
            v = r.get(col, "")
            data[col] = float(v) if v not in (None, "") else None
        out[r["name"]] = data
    return out

def load_all(inp, strict=False):
    mem = {r["name"]: r for r in csv.DictReader(io.StringIO(load(inp, "members.csv", "MEMBERS_URL")))}
    res = {}
    for r in csv.DictReader(io.StringIO(load(inp, "results.csv", "RESULTS_URL"))):
        d = {k: float(v or 0) for k, v in r.items() if k != "name"}
        for k in ("home", "face", "target", "talk", "cooloff", "screen_fail", "meet_own", "meet_other", "win_own", "win_other"): d.setdefault(k, 0.0)
        d["meet"] = d["meet_own"] + d["meet_other"]; d["win"] = d["win_own"] + d["win_other"]   # 商談・契約の合計は自アポ+他アポ
        res[r["name"]] = d
    cfg = build_cfg(inp, strict)
    pdca = load_pdca(inp)
    missing = [n for n in mem if n not in res]
    if missing: sys.exit(f"results にいない人: {missing}")
    return mem, res, cfg, pdca

def main(inp, out, strict=False):
    os.makedirs(out, exist_ok=True); pub = os.path.join(out, "public"); os.makedirs(pub, exist_ok=True)
    tmp = os.path.join(out, "_tmp"); os.makedirs(tmp, exist_ok=True)
    mem, res, cfg, pdca = load_all(inp, strict)
    common = common_pages(res, mem, cfg, tmp)
    rows = []
    for agency in dict.fromkeys(m["agency"] for m in mem.values()):
        members = [n for n, m in mem.items() if m["agency"] == agency]
        members.sort(key=lambda n: (mem[n]["role"] != "appointer", mem[n]["is_manager"] != "yes"))  # アポインタ→クローザーの順
        pages = common + [pg for n in members for pg in sheet(n, res, mem, cfg)] + [review_page(members, pdca)]
        fn = f"{secrets.token_urlsafe(9)}.pdf"; write_pdf(os.path.join(pub, fn), pages)
        for n in members: rows.append([agency, n, mem[n]["line_user_id"], mem[n]["role"], mem[n]["is_manager"], fn, len(members)])
        print(f"{agency}: {len(members)}人分 → {fn}")
    with open(os.path.join(out, "manifest.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["agency", "name", "line_user_id", "role", "is_manager", "file", "sheets"]); w.writerows(rows)
    # config.csvを出力フォルダにもコピーしておく（deliver.pyがPDCAテンプレの対象週表示に使う）
    with open(os.path.join(out, "config.csv"), "w", encoding="utf-8-sig") as f:
        f.write(open(os.path.join(inp, "config.csv"), encoding="utf-8-sig").read())
    print(f"代理店{len(set(r[0] for r in rows))}社 / 送信先{len(rows)}人: {out}")
if __name__ == "__main__": main(sys.argv[1], sys.argv[2], "--strict" in sys.argv)
