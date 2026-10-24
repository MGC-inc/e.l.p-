#!/usr/bin/env python3
"""毎朝（稼働日）の「今日の任された数字」カードを、各メンバーにLINEで送る。

■ねらい
  なんとなく1日をやり過ごす営業マンをなくす。月の目標に対する「稼働日ペース」と本人の数字を
  1枚の絵で並べ、遅れていれば「今日いくつで間に合うか」を具体的な数字で見せて、任された数字として
  やり切る責任を感じてもらう。文章を読まなくても、色と大きな数字で状況が分かるカードにする。

■カードに入れるもの（行動科学の知見のうち、裏付けが比較的しっかりしたものだけ）
  ・今日の必達を1つだけ大きく（一度に処理できる情報量には限りがあるため）
  ・月のゲージに「理想ペースの線」と本人の実績を重ねる（自分の基準との比較。他人との順位は出さない）
  ・遅れは赤・先行は緑（色は一瞬で伝わる）。遅れは「今日◯件で月末に間に合う」という行動できる数字に直す
  ・ゴールまでの「あと◯件・残り◯稼働日」（ゴールが近いほど人は動く＝目標勾配効果）
  ・このペースで月末に着地する見込み（現状維持のままだと何が起きるかを見せる）
  ・稼働日の並び（過去の日の達成・未達を色で。連続達成は継続したくなる）
  ・会社の月予算のうち、この数字がどれだけを占めるか（任された数字である実感）
  ・「今日やる！」ボタン（宣言すると守りたくなる＝一貫性）

■指標
  基本はアポ数。クローザーは、今日の商談の件数が多い日ほどアポの目標を軽くする
  （商談0件=そのまま／1件=半分／2件以上=4分の1。MEETING_FACTORで調整）。
■配信日
  水〜日と、祝日の月火。それ以外（定休の月火）は送らない。
■配信
  LINEのFlexメッセージ1通（上にカード画像、下にボタン）。1人1日1通。画像はSupabaseの公開バケットに置く。

使い方:
  python3 scripts/daily_mission.py                 # 今日（JST）の分を全員に送信
  python3 scripts/daily_mission.py --dry-run       # 送信せず、画像を --out に作って内容を表示
  python3 scripts/daily_mission.py --only 福本 --date 2026-10-28 --dry-run --out /tmp/dm
  python3 scripts/daily_mission.py --force         # 定休日でも送る（テスト用）

環境変数: NOTION_TOKEN, ELP_SUPABASE_URL, ELP_SUPABASE_SERVICE_ROLE_KEY, DEAL_LINE_CHANNEL_ACCESS_TOKEN,
IMAGAWA_LINE_USER_ID（失敗の報告先）。日本語フォント IPAGothic が必要（apt: fonts-ipafont-gothic）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import (BUDGET_DB, DEAL_DB, JST, KPI_DB, NOTION_VERSION_V1, NOTION_VERSION_V2,  # noqa: E402
                                  PERFORMANCE_DB, load_env, prop_num, prop_text, query_all)

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
from matplotlib.patches import Circle, FancyBboxPatch  # noqa: E402
from matplotlib.text import Text  # noqa: E402

try:
    import jpholiday
except ImportError:  # 祝日が分からないと、祝日の月火を休みとして扱う
    jpholiday = None

plt.rcParams["font.family"] = "IPAGothic"

BUCKET = "weekly-pdca"
FOLDER = "daily-mission"
NO_CARD = {"藤江"}  # 週次の対象外（tools/shuuji/notion_to_csv.py の WEEKLY_HIDDEN と同じ）
MEETING_FACTOR = {0: 1.0, 1: 0.5}  # クローザーの今日の商談数 → アポ目標にかける割合。2件以上は MEETING_FACTOR_MANY
MEETING_FACTOR_MANY = 0.25

NAVY, BLUE, GOLD = "#1F3A5F", "#2E6DA4", "#E0A100"
INK, SUB, LINE, BG = "#1B1F24", "#5A6472", "#D5DBE3", "#F3F5F8"
GREEN, RED, AMBER, GRAY = "#2E8B57", "#D64545", "#E08A00", "#C3CAD4"
WD = "月火水木金土日"

# 日替わりの一言。「なぜこの仕事をやるのか」を毎日違う角度で思い出させる（同じ文面は慣れて読まれなくなるため）。
# 責める言葉は使わない。自分で選んでここにいる、という感覚（自分で決めている感）を支える。
LINES = [
    "あなたの1件が、お客様の暮らしの安心と、会社の来月をつくる。",
    "今日の1時間は、月末の自分への贈り物になる。",
    "任された数字は、信頼されている証拠。",
    "ペースは「今日」で決まる。最初の1件を、午前中に。",
    "数字の向こうに、必ずお客様がいる。",
    "昨日の自分より、1件前へ。",
    "やり切った夜は、いちばん気持ちがいい。",
    "「できるかな」より「どう取るか」を考える日にする。",
    "チームの目標は、あなたの1件でできている。",
    "うまくいかない日も、1件のヒントになる。数えて、直して、また行く。",
    "今日の行動が、1か月後の結果をつくる。",
    "迷ったら、まず訪問する。動いた人にだけチャンスが来る。",
    "今日の自分に、胸を張れる1日にしよう。",
    "あと少しのところで、人は止まりやすい。そこが勝負どころ。",
]


def is_workday(d: dt.date) -> bool:
    """水〜日と、祝日の月火。"""
    if d.weekday() >= 2:
        return True
    return bool(jpholiday and jpholiday.is_holiday(d))


def month_days(d: dt.date) -> list[dt.date]:
    first = d.replace(day=1)
    nxt = first.replace(year=first.year + first.month // 12, month=first.month % 12 + 1)
    return [first + dt.timedelta(days=i) for i in range((nxt - first).days)]


# ------------------------------------------------------------------ データ
def _perf_rows(start: dt.date, end: dt.date) -> list[dict]:
    return query_all(f"/data_sources/{PERFORMANCE_DB}/query", {"and": [
        {"property": "実績日", "date": {"on_or_after": start.isoformat()}},
        {"property": "実績日", "date": {"on_or_before": end.isoformat()}},
    ]}, NOTION_VERSION_V2)


def perf_by_member_day(rows: list[dict]) -> tuple[dict, float]:
    """(メンバー→日→{訪問数, アポ数, 売上, 出勤}, 全社の売上合計)。
    売上＝契約金額−キャンセル金額（他アポは半額ずつ本人取り分。週次MT資料 tools/shuuji/notion_to_csv.py と同じ定義）。"""
    out: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    team = 0.0
    for r in rows:
        sales = (prop_num(r, "契約金額（万円）") or 0) - (prop_num(r, "キャンセル金額（万円）") or 0)
        team += sales
        name = prop_text(r, "メンバー")
        d = ((r["properties"].get("実績日") or {}).get("date") or {}).get("start")
        if not name or not d:
            continue
        rec = out[name][dt.date.fromisoformat(d[:10])]
        rec["訪問数"] += prop_num(r, "訪問数") or 0
        rec["アポ数"] += prop_num(r, "アポ数") or 0
        rec["売上"] += sales
        if ((r["properties"].get("出勤状況") or {}).get("select") or {}).get("name") == "出勤":
            rec["出勤"] = 1.0
    return out, team


def month_budgets(month: str) -> tuple[dict[str, float], float]:
    """💰予算と達成率の当月の全行。(メンバー→予算万円, 全社予算＝全行の合計)。"""
    rows = query_all(f"/data_sources/{BUDGET_DB}/query",
                     {"property": "年月（YYYY-MM）", "rich_text": {"equals": month}}, NOTION_VERSION_V2)
    per = {prop_text(r, "メンバー"): prop_num(r, "予算（万円）") or 0 for r in rows if prop_text(r, "メンバー")}
    return per, sum(prop_num(r, "予算（万円）") or 0 for r in rows)


def meetings_today(day: dt.date) -> dict[str, int]:
    rows = query_all(f"/data_sources/{DEAL_DB}/query", {"and": [
        {"property": "商談日時", "date": {"on_or_after": day.isoformat()}},
        {"property": "商談日時", "date": {"on_or_before": day.isoformat()}},
    ]}, NOTION_VERSION_V2)
    out: dict[str, int] = defaultdict(int)
    for r in rows:
        who = prop_text(r, "クローザー")
        if who:
            out[who] += 1
    return out


def rollup_status(page: dict, name: str) -> str | None:
    for item in ((page["properties"].get(name) or {}).get("rollup") or {}).get("array", []):
        sel = item.get("select")
        if sel and sel.get("name"):
            return sel["name"]
    return None


# ------------------------------------------------------------------ 計算
# 稼働して0件の日が続いたアポインターだけ、必達を「まず1件」に下げて、執念のこもった言葉で奮い立たせる。
# それ以外のアポインターの必達は、少なくとも APPOINTER_MIN 件（1件は低すぎるため）。
APPOINTER_MIN = 2
ZERO_STREAK_FOR_ONE = 2

# 「今日はアポ◯件。やり切ろう。」の言い換え。重みのある、任せた・頼んだ、という言葉にする（日替わり）
HEADLINES = [
    "今日はアポ{n}件。任せた。必ず取ってこい。",
    "アポ{n}件。この数字は、君にしか背負えない。頼んだぞ。",
    "今日のアポ{n}件。取るまで、止まるな。頼んだ。",
    "アポ{n}件。今日が、今月を決める。任せた。",
    "アポ{n}件、必ず。信じて待っている。",
    "今日はアポ{n}件。言い訳は要らない。結果で見せてくれ。",
]
ZERO_HEADLINE = "まず1件。なんとしても、もぎ取ってこい！"
ZERO_REASON = "{k}日連続で、動いたのにアポ0件。ここで止まると、次の1件がいちばん遠くなる。だから今日は、最初の1件に全部を賭けろ。1件取れれば、流れは変わる。"


def week_window(today: dt.date) -> tuple[dt.date, dt.date]:
    start = today - dt.timedelta(days=(today.weekday() - 2) % 7)  # 水曜始まり（週次の定義と同じ）
    return start, start + dt.timedelta(days=6)


def build_mission(page: dict, today: dt.date, days: list[dt.date], by_day: dict, n_meet: int,
                  budgets: dict[str, float], team: dict) -> dict | None:
    name = prop_text(page, "メンバー")
    role = prop_text(page, "役割")
    target = int(prop_num(page, "今月アポ目標概算") or 0)
    if not target:  # クローザーは契約目標から逆算（契約目標 × 1契約に必要なアポ数）
        target = math.ceil((prop_num(page, "今月契約目標") or 0) * (prop_num(page, "1契約に必要なアポ数") or 0))
    if not name or role not in ("クローザー", "アポインター") or target <= 0:
        return None
    work = [d for d in days if is_workday(d)]
    total = len(work)
    past = [d for d in work if d < today]
    left = total - len(past)  # 今日を含む残り稼働日
    mine = by_day.get(name, {})
    done = int(sum(v.get("アポ数", 0) for v in mine.values()))
    visits_month = int(sum(v.get("訪問数", 0) for v in mine.values()))
    ideal = target * len(past) / total if total else 0
    gap = done - ideal
    remaining = max(0, target - done)
    base = math.ceil(remaining / left) if left > 0 and remaining else 0
    avg = done / len(past) if past else 0
    stretch = False
    if not base and avg:  # 月の目標は達成済み → 自分の平均ペースを今日の必達にする（自己ベスト基準）
        base, stretch = math.ceil(avg), True
    factor, note = 1.0, ""
    if role == "クローザー":
        factor = MEETING_FACTOR.get(n_meet, MEETING_FACTOR_MANY) if n_meet else 1.0
        if n_meet:
            note = f"今日は商談{n_meet}件。商談に集中する分、アポ目標を調整"
    today_target = max(1, math.ceil(base * factor)) if base else 0

    # 稼働して（出勤して）アポ0件の日が何日続いているか（出勤していない日は数えず、途切れさせない）
    zero_streak = 0
    for d in reversed(past):
        rec = mine.get(d)
        if not rec or not rec.get("出勤"):
            continue
        if rec.get("アポ数", 0) > 0:
            break
        zero_streak += 1
    fierce = role == "アポインター" and zero_streak >= ZERO_STREAK_FOR_ONE
    if role == "アポインター":
        today_target = 1 if fierce else max(APPOINTER_MIN, today_target)

    forecast = round(avg * total) if past else None
    # 稼働日ごとの達成（月目標÷稼働日数 = 1日あたりの目安を超えたら緑、1件以上で黄、0件は赤）
    per_day = target / total if total else 0
    strip = []
    for d in work:
        if d >= today:
            strip.append("today" if d == today else "future")
        else:
            n = mine.get(d, {}).get("アポ数", 0)
            strip.append("ok" if n >= per_day and n > 0 else "some" if n > 0 else "zero")
    streak = 0
    for st in reversed([st for st, d in zip(strip, work) if d < today]):
        if st == "ok":
            streak += 1
        else:
            break
    yday = today - dt.timedelta(days=1)
    missing_yday = is_workday(yday) and yday.month == today.month and yday not in mine

    # 売上（クローザーは個人の月予算に対して。全員に、全社の月予算に対しての残りを見せる）
    money = None
    budget_p = budgets.get(name)
    if role == "クローザー" and budget_p:
        sales_m = sum(v.get("売上", 0) for v in mine.values())
        rem_p = max(0.0, budget_p - sales_m)
        wk_start, wk_end = week_window(today)
        month_end = days[-1]
        week_left = len([d for d in work if today <= d <= min(wk_end, month_end)])
        sales_wk = sum(v.get("売上", 0) for d, v in mine.items() if d >= wk_start)
        money = {"budget": budget_p, "sales": sales_m, "remaining": rem_p,
                 "ideal": budget_p * len(past) / total if total else 0,
                 "week_aim": math.ceil(rem_p * week_left / left) if left and rem_p else 0,
                 "week_left": week_left, "week_sales": sales_wk}
    company = {"budget": team["budget"], "sales": team["sales"], "remaining": max(0.0, team["budget"] - team["sales"]),
               "per_day": max(0.0, team["budget"] - team["sales"]) / left if left else 0, "left": left}
    return {"name": name, "role": role, "target": target, "done": done, "ideal": ideal, "gap": gap,
            "left": left, "total": total, "elapsed": len(past), "today_target": today_target,
            "forecast": forecast, "note": note, "stretch": stretch, "n_meet": n_meet, "strip": strip,
            "streak": streak, "zero_streak": zero_streak, "fierce": fierce, "missing_yday": missing_yday,
            "visit_today": int(prop_num(page, "今日やるべき訪問数") or 0), "visits_month": visits_month,
            "budget": prop_num(page, "今月予算万円") or 0, "week_apo": int(prop_num(page, "今週の必要アポ数") or 0),
            "week_visit": int(prop_num(page, "今週の必要訪問数") or 0), "money": money, "company": company}


def headline(m: dict, today: dt.date) -> str:
    if m["fierce"]:
        return ZERO_HEADLINE
    if not m["today_target"]:
        return "今日も1件、積み上げよう。"
    return HEADLINES[(today.toordinal() + sum(map(ord, m["name"]))) % len(HEADLINES)].format(n=m["today_target"])


def status_of(m: dict) -> tuple[str, str]:
    """(色, ひとこと)。0.5件未満のずれは「ペース通り」。"""
    g = m["gap"]
    if m["elapsed"] == 0:
        return BLUE, "月のスタート。最初の1件を、今日。"
    if g >= 0.5:
        return GREEN, f"理想ペースより {g:.0f}件 先行"
    if g > -0.5:
        return GREEN, "理想ペースどおり"
    if g > -2.5:
        return AMBER, f"理想ペースより {-g:.0f}件 遅れ"
    return RED, f"理想ペースより {-g:.0f}件 遅れ"


# ------------------------------------------------------------------ カード画像
PX_PER_UNIT = 10.4  # 横100単位＝1040px


def man(v: float) -> str:
    return f"{v:,.0f}万" if abs(v - round(v)) < 0.05 else f"{v:,.1f}万"


def render_card(m: dict, today: dt.date, path: str, budget_share: float | None) -> int:
    """カード画像を作り、その高さ（px）を返す。縦は中身に合わせて決める（上から順に積んで、最後に切る）。"""
    Hu = 260
    fig = plt.figure(figsize=(10.4, Hu * PX_PER_UNIT / 100), dpi=100, facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(0, Hu); ax.axis("off")
    color, status = status_of(m)

    def box(x, y, w, h, fc, ec="none", r=1.6, lw=0):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw))

    def gauge(y, h, value, goal, ideal, col, ideal_label):
        box(5, y, 90, h, "#E6EAF0", r=1.2)
        fill = min(1.0, value / goal) if goal else 0
        if fill > 0:
            box(5, y, max(90 * fill, 2.4), h, col, r=1.2)
        ix = 5 + 90 * min(1.0, ideal / goal) if goal else 5
        ax.plot([ix, ix], [y - 1.6, y + h + 1.6], color=INK, lw=3, ls=(0, (3, 2)))
        ax.text(min(max(ix, 18), 82), y + h + 3.4, ideal_label, color=INK, fontsize=15, ha="center", va="center")

    y = Hu
    # 見出し
    box(0, y - 17, 100, 17, NAVY, r=0)
    ax.text(5, y - 6, f"{m['name']}さん", color="white", fontsize=30, fontweight="bold", va="center")
    ax.text(95, y - 6, f"{today.month}/{today.day}（{WD[today.weekday()]}）", color="#C9D6E8", fontsize=22, ha="right", va="center")
    ax.text(5, y - 13.5, "今日、あなたが任された数字", color=GOLD, fontsize=21, fontweight="bold", va="center")
    y -= 20

    # 今日の必達（いちばん大きく）
    box(4, y - 29, 92, 29, "#FDECEC" if m["fierce"] else BG)
    if m["today_target"]:
        ax.text(50, y - 5, "今日の必達 アポ", color=SUB, fontsize=22, ha="center", va="center")
        ax.text(46, y - 14, f"{m['today_target']}", color=RED if m["fierce"] else (color if color != GREEN else INK),
                fontsize=96, fontweight="bold", ha="right", va="center")
        ax.text(48, y - 16, "件", color=INK, fontsize=36, fontweight="bold", ha="left", va="center")
        if m["fierce"]:
            sub = f"{m['zero_streak']}日連続アポ0件。まず1件、なんとしても"
        elif m["note"]:
            sub = m["note"]
        elif today.weekday() == 2 and m["week_apo"]:  # 水曜は週次の「今週任された数字」を入れる
            sub = f"今週の数字：アポ{m['week_apo']}件／訪問{m['week_visit']}件"
        elif m["stretch"]:
            sub = "月の目標は達成済み。自分の平均ペースを守ろう"
        else:
            sub = f"残り{m['target'] - m['done']}件 ÷ 残り{m['left']}稼働日"
        ax.text(50, y - 24, sub, color=RED if m["fierce"] else SUB, fontsize=15, ha="center", va="center",
                fontweight="bold" if m["fierce"] else "normal")
    else:
        ax.text(50, y - 12, "今月のアポ目標 達成！", color=GREEN, fontsize=34, fontweight="bold", ha="center", va="center")
        ax.text(50, y - 21, "ここからの1件が、さらに上積み", color=SUB, fontsize=18, ha="center", va="center")
    y -= 35

    # 月ゲージ: 理想ペースの線と実績
    ax.text(5, y, "今月のアポ", color=INK, fontsize=22, fontweight="bold", va="center")
    ax.text(95, y, f"目標 {m['target']}件", color=SUB, fontsize=18, ha="right", va="center")
    gauge(y - 15, 7, m["done"], m["target"], m["ideal"], color, f"理想ペース {m['ideal']:.0f}件")
    ax.text(6, y - 21, f"実績 {m['done']}件", color=color, fontsize=24, fontweight="bold", va="center")
    ax.text(95, y - 21, status, color=color, fontsize=19, fontweight="bold", ha="right", va="center")
    y -= 29
    if m["missing_yday"]:
        ax.text(5, y, "※昨日の日報がまだ届いていません。入力すると数字が正しくなります", color=AMBER, fontsize=13.5, va="center")
        y -= 4.5

    # 稼働日ストリップ
    ax.text(5, y, f"稼働日  {m['elapsed']}日経過 ／ 残り{m['left']}日（今日を含む）", color=INK, fontsize=17, va="center")
    n = len(m["strip"])
    cols = min(n, 16)
    rows = math.ceil(n / cols)
    step = 90 / cols
    for i, st in enumerate(m["strip"]):
        cx = 5 + step * (i % cols) + step / 2
        cy = y - 6.5 - 4.6 * (i // cols)
        face = {"ok": GREEN, "some": AMBER, "zero": RED, "today": "white", "future": "white"}[st]
        edge = {"today": GOLD, "future": GRAY}.get(st, face)
        ax.add_patch(Circle((cx, cy), step * 0.36, fc=face, ec=edge, lw=4 if st == "today" else 1.6))
    ly = y - 6.5 - 4.6 * (rows - 1) - 5.5
    lx = 6.0
    for face, edge, label in ((GREEN, GREEN, "目安達成"), (AMBER, AMBER, "1件以上"), (RED, RED, "0件"), ("white", GOLD, "今日")):
        ax.add_patch(Circle((lx, ly), 1.1, fc=face, ec=edge, lw=2))
        ax.text(lx + 2, ly, label, color=SUB, fontsize=12.5, va="center")
        lx += 8 + 1.6 * len(label)
    if m["streak"] >= 2:
        ax.text(95, ly, f"{m['streak']}日連続で目安達成", color=GREEN, fontsize=15, fontweight="bold", ha="right", va="center")
    y = ly - 6

    # 着地見込み
    box(4, y - 14, 92, 14, "#FFF8E6" if color != RED else "#FDECEC")
    if m["forecast"] is not None:
        pct = round(m["forecast"] / m["target"] * 100)
        mark = "▲" if m["forecast"] < m["target"] else "◎"
        ax.text(7, y - 4.5, f"このペースのまま行くと月末 {m['forecast']}件（目標の{pct}%）", color=INK, fontsize=19, fontweight="bold", va="center")
        short = m["target"] - m["forecast"]
        ax.text(7, y - 10, (f"{mark} 目標まで {short}件 足りない。今日からの動きで変えられる。" if short > 0
                            else f"{mark} 目標に届くペース。落とさずに続けよう。"), color=SUB, fontsize=15, va="center")
    else:
        ax.text(7, y - 7, "月のスタート。ペースづくりは最初の数日で決まる。", color=INK, fontsize=19, fontweight="bold", va="center")
    y -= 19

    # 売上（クローザーは個人の予算。全員に全社予算の残り）
    mo, co = m["money"], m["company"]
    if mo:
        ax.text(5, y, "あなたに任された売上", color=INK, fontsize=22, fontweight="bold", va="center")
        ax.text(95, y, f"月予算 {man(mo['budget'])}", color=SUB, fontsize=18, ha="right", va="center")
        pcol = GREEN if mo["sales"] >= mo["ideal"] - 0.5 else (AMBER if mo["sales"] >= mo["ideal"] * 0.7 else RED)
        gauge(y - 15, 6, mo["sales"], mo["budget"], mo["ideal"], pcol, f"理想ペース {man(mo['ideal'])}")
        ax.text(6, y - 21, f"実績 {man(mo['sales'])}", color=pcol, fontsize=22, fontweight="bold", va="center")
        ax.text(95, y - 21, f"着地まで あと {man(mo['remaining'])}" if mo["remaining"] else "月予算 達成済み", color=INK if mo["remaining"] else GREEN,
                fontsize=21, fontweight="bold", ha="right", va="center")
        box(4, y - 33, 92, 8, "#EAF1FB")
        ax.text(7, y - 29, f"今週の狙い  +{man(mo['week_aim'])}" if mo["remaining"] else "全社の残りを埋める上積みを、頼んだ",
                color=NAVY, fontsize=21 if mo["remaining"] else 19, fontweight="bold", va="center")
        ax.text(93, y - 29, f"今週ここまで {man(mo['week_sales'])}／残り{mo['week_left']}稼働日", color=SUB, fontsize=13.5, ha="right", va="center")
        y -= 38
    box(4, y - 13, 92, 13, "#F3F5F8")
    ax.text(7, y - 4, f"チームの月予算 {man(co['budget'])}　実績 {man(co['sales'])}", color=INK, fontsize=17, va="center")
    ax.text(7, y - 9.3, f"月末まで、あと {man(co['remaining'])}（残り{co['left']}稼働日で、1日 {man(co['per_day'])}）",
            color=RED if co["remaining"] > 0 else GREEN, fontsize=17, fontweight="bold", va="center")
    y -= 17

    # 今日の一言
    line = LINES[(today.toordinal() + sum(map(ord, m["name"]))) % len(LINES)]
    ax.text(50, y - 3.5, line, color=NAVY, fontsize=16, fontweight="bold", ha="center", va="center")
    ax.text(50, y - 9.5, "E.L.P  営業分析BOT", color=GRAY, fontsize=11, ha="center", va="center")
    bottom = y - 13
    height_units = Hu - bottom
    ax.set_ylim(bottom, Hu)
    fig.set_size_inches(10.4, height_units * PX_PER_UNIT / 100)

    for t in fig.findobj(Text):  # IPAゴシックは太字が無いので、太字指定は縁取りで太く見せる
        if t.get_fontweight() in ("bold", 700) and t.get_text():
            t.set_path_effects([pe.withStroke(linewidth=t.get_fontsize() / 22, foreground=t.get_color())])
    fig.savefig(path, format="png", dpi=100, facecolor="white")
    plt.close(fig)
    return int(round(height_units * PX_PER_UNIT))


# ------------------------------------------------------------------ 配信
def supabase_upload(local: str, remote: str) -> str:
    base = os.environ["ELP_SUPABASE_URL"].rstrip("/")
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    with open(local, "rb") as f:
        data = f.read()
    req = urllib.request.Request(f"{base}/storage/v1/object/{BUCKET}/{remote}", data=data, method="POST",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}",
                                          "Content-Type": "image/png", "x-upsert": "true"})
    urllib.request.urlopen(req, timeout=60).close()
    return f"{base}/storage/v1/object/public/{BUCKET}/{remote}"


def sb_get(path: str) -> list:
    base = os.environ["ELP_SUPABASE_URL"].rstrip("/")
    key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(f"{base}/rest/v1/{path}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def line_push(to: str, messages: list) -> int | str:
    req = urllib.request.Request("https://api.line.me/v2/bot/message/push", method="POST",
                                 data=json.dumps({"to": to, "messages": messages}).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + os.environ["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"ERROR {e.code} {e.read().decode()[:200]}"


def flex_message(m: dict, image_url: str, today: dt.date, height_px: int) -> dict:
    color, status = status_of(m)
    body = [{"type": "text", "text": headline(m, today), "weight": "bold", "size": "lg", "wrap": True, "color": "#B3261E" if m["fierce"] else "#1F3A5F"}]
    if m["fierce"]:
        body.append({"type": "text", "text": ZERO_REASON.format(k=m["zero_streak"]), "size": "sm", "wrap": True, "color": "#444444"})
    else:
        body.append({"type": "text", "text": status, "size": "sm", "wrap": True, "color": color})
    if m["money"]:
        body.append({"type": "text", "text": (f"今週の狙い +{man(m['money']['week_aim'])}／着地まで あと{man(m['money']['remaining'])}"
                                              if m["money"]["remaining"] else "月予算は達成済み。上積みを頼んだ"),
                     "size": "sm", "wrap": True, "weight": "bold", "color": "#1F3A5F"})
    alt = f"【今日の任された数字】アポ{m['today_target']}件｜{status}" + (f"｜今週の狙い+{man(m['money']['week_aim'])}" if m["money"] else "")
    h = height_px
    return {
        "type": "flex", "altText": alt,
        "contents": {
            "type": "bubble", "size": "mega",
            "hero": {"type": "image", "url": image_url, "size": "full", "aspectMode": "cover", "aspectRatio": f"1040:{h}"},
            "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
            "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                {"type": "button", "style": "primary", "color": "#1F3A5F",
                 "action": {"type": "message", "label": "今日やる！", "text": "今日やる！"}},
                {"type": "button", "style": "secondary",
                 "action": {"type": "message", "label": "くわしいKPIを見る", "text": "KPIを見る"}},
            ]},
        },
    }


def audit(by_day: dict, team_sales: float, today: dt.date, month: str) -> list[str]:
    """カードの数字を、別の読み方で出し直して突き合わせる。1か所でも違えば配信しない。
    別の読み方＝実績日の範囲ではなく「年月（YYYY-MM）」の数式列で絞って読み直し、売上は契約金額−キャンセル金額ではなく
    Notion側の数式「実質売上（万円）」の値を使う。"""
    rows = query_all(f"/data_sources/{PERFORMANCE_DB}/query",
                     {"property": "年月（YYYY-MM）", "formula": {"string": {"equals": month}}}, NOTION_VERSION_V2)
    apo: dict[str, float] = defaultdict(float)
    sales: dict[str, float] = defaultdict(float)
    team = 0.0
    for r in rows:
        d = ((r["properties"].get("実績日") or {}).get("date") or {}).get("start")
        if not d or dt.date.fromisoformat(d[:10]) >= today:
            continue
        v = ((r["properties"].get("実質売上（万円）") or {}).get("formula") or {}).get("number") or 0
        team += v
        name = prop_text(r, "メンバー")
        apo[name] += prop_num(r, "アポ数") or 0
        sales[name] += v
    bad = []
    if abs(team - team_sales) > 0.05:
        bad.append(f"全社売上が一致しません: カード{team_sales:.1f} / 再計算{team:.1f}")
    for name in set(by_day) | set(apo):
        a1 = sum(v.get("アポ数", 0) for v in by_day.get(name, {}).values())
        s1 = sum(v.get("売上", 0) for v in by_day.get(name, {}).values())
        if abs(a1 - apo.get(name, 0)) > 0.01:
            bad.append(f"{name}のアポ数が一致しません: カード{a1:.0f} / 再計算{apo.get(name, 0):.0f}")
        if abs(s1 - sales.get(name, 0)) > 0.05:
            bad.append(f"{name}の売上が一致しません: カード{s1:.1f} / 再計算{sales.get(name, 0):.1f}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="定休日でも送る（テスト用）")
    ap.add_argument("--only", help="この人だけ")
    ap.add_argument("--date", help="今日の日付を指定（YYYY-MM-DD。画像の確認用）")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "tools", "shuuji", "out", "daily_mission"))
    args = ap.parse_args()
    load_env()

    today = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(JST).date()
    if not is_workday(today) and not args.force:
        print(f"{today} は定休日（月火で祝日でない）なので送りません。")
        return 0
    days = month_days(today)
    os.makedirs(args.out, exist_ok=True)
    month = today.strftime("%Y-%m")
    admin = os.environ.get("IMAGAWA_LINE_USER_ID")

    by_day, team_sales = perf_by_member_day(_perf_rows(days[0], today - dt.timedelta(days=1)))  # 今日の分は朝時点でまだ無い
    budgets, team_budget = month_budgets(month)
    team = {"budget": team_budget, "sales": team_sales}
    bad = audit(by_day, team_sales, today, month)
    if bad:
        print("【数字の再計算が合いません。配信を止めます】\n" + "\n".join(bad))
        if admin and not args.dry_run:
            line_push(admin, [{"type": "text", "text": "⚠ 今日の任された数字カードの配信を止めました（再計算が合いません）\n" + "\n".join(bad)[:4000]}])
        return 1
    print(f"再計算OK: 全社売上 {team_sales:,.1f}万 / 全社予算 {team_budget:,.1f}万 / 実績のあるメンバー{len(by_day)}名")

    meet = meetings_today(today)
    pages = query_all(f"/databases/{KPI_DB}/query", None, NOTION_VERSION_V1)
    active = [p for p in pages if prop_text(p, "メンバー") and rollup_status(p, "在籍状況(名簿参照)") in (None, "稼働中")
              and prop_text(p, "メンバー") not in NO_CARD]
    users = {u["closer_name"]: u["line_user_id"] for u in sb_get("closer_line_users?select=closer_name,line_user_id")
             if u.get("closer_name")}
    salt = os.environ.get("ELP_SUPABASE_SERVICE_ROLE_KEY", "")[:16]

    sent, failed, skipped = [], [], []
    for p in active:
        name = prop_text(p, "メンバー")
        if args.only and name != args.only:
            continue
        m = build_mission(p, today, days, by_day, meet.get(name, 0), budgets, team)
        if not m:
            skipped.append((name, "今月のアポ目標が未設定"))
            continue
        local = os.path.join(args.out, f"{today:%Y%m%d}_{name}.png")
        height_px = render_card(m, today, local, None)
        _, status = status_of(m)
        mo = m["money"]
        print(f"{name}({m['role']}): アポ{m['done']}/{m['target']} 理想{m['ideal']:.1f} {status} → 今日{m['today_target']}件"
              + (f" 0件連続{m['zero_streak']}日" if m["zero_streak"] else "") + (f" {m['note']}" if m["note"] else "")
              + (f" | 売上{mo['sales']:.1f}/{mo['budget']:.1f}万 あと{mo['remaining']:.1f} 今週+{mo['week_aim']}" if mo else "")
              + (" ※昨日の日報なし" if m["missing_yday"] else ""))
        if args.dry_run:
            continue
        to = users.get(name)
        if not to:
            skipped.append((name, "LINE未登録"))
            continue
        token = hashlib.sha256(f"{salt}{name}{today}".encode()).hexdigest()[:16]
        try:
            url = supabase_upload(local, f"{FOLDER}/{today:%Y%m%d}/{token}.png")
        except Exception as e:  # noqa: BLE001
            failed.append((name, f"画像のアップロード失敗: {e}"))
            continue
        res = line_push(to, [flex_message(m, url, today, height_px)])
        (sent if res == 200 else failed).append(name if res == 200 else (name, res))
    print(f"送信 {len(sent)}名 / 失敗 {len(failed)}名 / スキップ {len(skipped)}名")
    for n_, why in skipped + failed:
        print(f"  ・{n_}: {why}")
    if (failed or skipped) and admin and not args.dry_run and not args.only:
        lines = [f"📣 今日の任された数字: 送信{len(sent)}名／失敗{len(failed)}名／スキップ{len(skipped)}名"]
        lines += [f"・{n_}: {why}" for n_, why in (failed + skipped)]
        line_push(admin, [{"type": "text", "text": "\n".join(lines)[:4500]}])
    return 0


if __name__ == "__main__":
    sys.exit(main())
