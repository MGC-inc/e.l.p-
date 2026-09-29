"""会議で画面共有する「全社ダッシュボード」(16:9・横向き)を作る。
使い方: python build_meeting.py 入力フォルダ 出力PDF [--strict]"""
import sys, csv, io
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import build_all as B
plt.rcParams["pdf.fonttype"] = 42
NAVY, GOLD, GRAY, RED = "#1F3A5F", "#E0A100", "#C9D1DC", "#D64545"
COLS = ["#1F3A5F", "#2E6DA4", "#3B8EA5", "#5B7DB1", "#8C5E9E"]

def header(fig, title, sub):
    fig.patches.append(plt.Rectangle((0, 0.9), 1, 0.1, transform=fig.transFigure, color=NAVY))
    fig.text(0.03, 0.945, title, color="white", fontsize=22, fontweight="bold", va="center")
    fig.text(0.97, 0.945, sub, color="white", fontsize=14, ha="right", va="center")

def clean(ax):
    for s in ("top", "right"): ax.spines[s].set_visible(False)

def main(inp, outpdf, strict=False):
    mem, res, cfg, _pdca = B.load_all(inp, strict)
    ag = {}
    for n, r in res.items():
        d = ag.setdefault(mem[n]["agency"], [0]*6)
        for i, k in enumerate(["visits", "appo", "meet", "win", "sales"]): d[i] += r[k]
        d[5] += 1
    names = list(ag); labels = [f"{n}（{ag[n][5]}名）" for n in names]
    tot = [sum(v[i] for v in ag.values()) for i in range(5)]
    with PdfPages(outpdf) as pdf:
        # ---- 1枚目: 全社の結果と月進捗 ----
        fig = plt.figure(figsize=(13.33, 7.5)); header(fig, "先週の全社結果と月・週の進捗", cfg["week"])
        gs = fig.add_gridspec(3, 5, left=0.05, right=0.95, top=0.84, bottom=0.08, hspace=0.6, wspace=0.25, height_ratios=[1, 0.6, 1.6])
        for i, m in enumerate(B.METRICS):
            ax = fig.add_subplot(gs[0, i]); ax.axis("off")
            ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes, color="#F2F4F7"))
            ax.text(0.5, 0.72, m, ha="center", fontsize=14, color="#555", transform=ax.transAxes)
            ax.text(0.5, 0.28, f"{int(tot[i]):,}", ha="center", fontsize=32, fontweight="bold", color=NAVY, transform=ax.transAxes)
        rates = [("訪問→アポ率", B.rate(tot[1], tot[0])), ("商談→契約率", B.rate(tot[3], tot[2]))]
        for i, (l, v) in enumerate(rates):
            ax = fig.add_subplot(gs[1, i]); ax.axis("off")
            ax.text(0.5, 0.5, f"{l}  {v}%", ha="center", va="center", fontsize=17, color=NAVY, transform=ax.transAxes)
        axm = fig.add_subplot(gs[2, 0:2]); axw = fig.add_subplot(gs[2, 3:5])
        B.draw_progress(axm, axw, cfg, tot, fs=1.6)
        pdf.savefig(fig); plt.close(fig)
        # ---- 2枚目: 代理店ランキング(1人当たり) ----
        fig = plt.figure(figsize=(13.33, 7.5)); header(fig, "代理店ランキング（1人当たり）", cfg["week"])
        gs = fig.add_gridspec(2, 3, left=0.11, right=0.97, top=0.84, bottom=0.07, hspace=0.5, wspace=0.55)
        for i, m in enumerate(B.METRICS):
            ax = fig.add_subplot(gs[i//3, i%3]); vals = [round(ag[n][i]/ag[n][5], 1) for n in names]
            o = sorted(range(len(vals)), key=lambda x: -vals[x]); nn = [labels[x] for x in o][::-1]; vv = [vals[x] for x in o][::-1]
            cs = [COLS[i]]*len(vv); cs[-1] = GOLD
            ax.barh(nn, vv, color=cs)
            for y, x in enumerate(vv): ax.text(x, y, f" {x:,.1f}", va="center", fontsize=12, fontweight="bold")
            ax.set_title(f"1人当たり{m}", fontsize=15, loc="left", fontweight="bold"); ax.set_xlim(0, max(vv)*1.25); ax.tick_params(labelsize=11); clean(ax)
        ax = fig.add_subplot(gs[1, 2]); ax.axis("off"); ax.set_title("率の比較", fontsize=15, loc="left", fontweight="bold")
        rows = [[n, f"{B.rate(ag[n][1], ag[n][0])}%", f"{B.rate(ag[n][3], ag[n][2])}%"] for n in names]
        tb = ax.table(cellText=rows, colLabels=["代理店", "訪問\n→アポ", "商談\n→契約"], bbox=[0, 0.05, 1, 0.8], cellLoc="center"); tb.auto_set_font_size(False); tb.set_fontsize(11)
        for (r, c), cell in tb.get_celld().items():
            cell.set_edgecolor(GRAY)
            if r == 0: cell.set_facecolor(NAVY); cell.get_text().set_color("white")
        fig.text(0.97, 0.02, "金色＝1位　／　（）内は稼働人数", ha="right", fontsize=11, color="#777")
        pdf.savefig(fig); plt.close(fig)
    print("出力:", outpdf)
if __name__ == "__main__": main(sys.argv[1], sys.argv[2], "--strict" in sys.argv)
