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
from monthly_budget_floor import (DEAL_DB, JST, KPI_DB, NOTION_VERSION_V1, NOTION_VERSION_V2,  # noqa: E402
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
def perf_by_member_day(start: dt.date, end: dt.date) -> dict[str, dict[dt.date, dict[str, float]]]:
    rows = query_all(f"/data_sources/{PERFORMANCE_DB}/query", {"and": [
        {"property": "実績日", "date": {"on_or_after": start.isoformat()}},
        {"property": "実績日", "date": {"on_or_before": end.isoformat()}},
    ]}, NOTION_VERSION_V2)
    out: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for r in rows:
        name = prop_text(r, "メンバー")
        d = ((r["properties"].get("実績日") or {}).get("date") or {}).get("start")
        if not name or not d:
            continue
        day = dt.date.fromisoformat(d[:10])
        for col in ("訪問数", "アポ数"):
            out[name][day][col] += prop_num(r, col) or 0
    return out


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
def build_mission(page: dict, today: dt.date, days: list[dt.date], by_day: dict, n_meet: int) -> dict | None:
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
    factor, note = 1.0, ""
    if role == "クローザー":
        factor = MEETING_FACTOR.get(n_meet, MEETING_FACTOR_MANY) if n_meet else 1.0
        if n_meet:
            note = f"今日は商談{n_meet}件。商談に集中する分、アポ目標を調整"
    avg = done / len(past) if past else 0
    stretch = False
    if not base and avg:  # 月の目標は達成済み → 自分の平均ペースを今日の必達にする（自己ベスト基準）
        base, stretch = math.ceil(avg), True
    today_target = max(1, math.ceil(base * factor)) if base else 0
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
    for s in reversed([s for s, d in zip(strip, work) if d < today]):
        if s == "ok":
            streak += 1
        else:
            break
    return {"name": name, "role": role, "target": target, "done": done, "ideal": ideal, "gap": gap,
            "left": left, "total": total, "elapsed": len(past), "today_target": today_target,
            "forecast": forecast, "note": note, "stretch": stretch, "n_meet": n_meet, "strip": strip, "streak": streak,
            "visit_today": int(prop_num(page, "今日やるべき訪問数") or 0), "visits_month": visits_month,
            "budget": prop_num(page, "今月予算万円") or 0, "week_apo": int(prop_num(page, "今週の必要アポ数") or 0),
            "week_visit": int(prop_num(page, "今週の必要訪問数") or 0)}


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
def render_card(m: dict, today: dt.date, path: str, budget_share: float | None) -> None:
    W, H = 10.4, 13.0
    fig = plt.figure(figsize=(W, H), dpi=100, facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(0, 125); ax.axis("off")
    color, status = status_of(m)

    def box(x, y, w, h, fc, ec="none", r=1.6, lw=0):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw))

    # 見出し
    box(0, 108, 100, 17, NAVY, r=0)
    ax.text(5, 119, f"{m['name']}さん", color="white", fontsize=30, fontweight="bold", va="center")
    ax.text(95, 119, f"{today.month}/{today.day}（{WD[today.weekday()]}）", color="#C9D6E8", fontsize=22, ha="right", va="center")
    ax.text(5, 111.5, "今日、あなたが任された数字", color=GOLD, fontsize=21, fontweight="bold", va="center")

    # 今日の必達（いちばん大きく）
    box(4, 76, 92, 29, BG)
    unit = "アポ"
    if m["today_target"]:
        ax.text(50, 96, f"今日の必達 {unit}", color=SUB, fontsize=22, ha="center", va="center")
        ax.text(46, 87, f"{m['today_target']}", color=color if color != GREEN else INK, fontsize=96,
                fontweight="bold", ha="right", va="center")
        ax.text(48, 85, "件", color=INK, fontsize=36, fontweight="bold", ha="left", va="center")
        sub = m["note"] or ("月の目標は達成済み。自分の平均ペースを守ろう" if m["stretch"]
                            else f"残り{m['target'] - m['done']}件 ÷ 残り{m['left']}稼働日")
        if today.weekday() == 2 and m["week_apo"]:  # 水曜は週次の「今週任された数字」を入れる
            sub = f"今週の数字：アポ{m['week_apo']}件／訪問{m['week_visit']}件"
        ax.text(50, 79, sub, color=SUB, fontsize=15, ha="center", va="center")
    else:
        ax.text(50, 90, "今月のアポ目標 達成！", color=GREEN, fontsize=34, fontweight="bold", ha="center", va="center")
        ax.text(50, 82, "ここからの1件が、さらに上積み", color=SUB, fontsize=18, ha="center", va="center")

    # 月ゲージ: 理想ペースの線と実績
    ax.text(5, 71, "今月のペース", color=INK, fontsize=22, fontweight="bold", va="center")
    ax.text(95, 71, f"目標 {m['target']}件", color=SUB, fontsize=18, ha="right", va="center")
    gx, gw, gy, gh = 5, 90, 60, 7
    box(gx, gy, gw, gh, "#E6EAF0", r=1.2)
    fill = min(1.0, m["done"] / m["target"])
    if fill > 0:
        box(gx, gy, max(gw * fill, 2.4), gh, color, r=1.2)
    ix = gx + gw * min(1.0, m["ideal"] / m["target"])
    ax.plot([ix, ix], [gy - 1.8, gy + gh + 1.8], color=INK, lw=3, ls=(0, (3, 2)))
    ax.text(min(max(ix, 18), 82), gy + gh + 3.6, f"理想ペース {m['ideal']:.0f}件", color=INK, fontsize=15, ha="center", va="center")
    ax.text(gx + 1, gy - 4, f"実績 {m['done']}件", color=color, fontsize=24, fontweight="bold", va="center")
    ax.text(95, gy - 4, status, color=color, fontsize=19, fontweight="bold", ha="right", va="center")

    # 稼働日ストリップ
    ax.text(5, 49.5, f"稼働日  {m['elapsed']}日経過 ／ 残り{m['left']}日（今日を含む）", color=INK, fontsize=17, va="center")
    n = len(m["strip"])
    cols = min(n, 16)
    rows = math.ceil(n / cols)
    step = 90 / cols
    for i, s in enumerate(m["strip"]):
        cx = 5 + step * (i % cols) + step / 2
        cy = 43 - 4.6 * (i // cols)
        face = {"ok": GREEN, "some": AMBER, "zero": RED, "today": "white", "future": "white"}[s]
        edge = {"today": GOLD, "future": GRAY}.get(s, face)
        ax.add_patch(Circle((cx, cy), step * 0.36, fc=face, ec=edge, lw=4 if s == "today" else 1.6))
    ly = 43 - 4.6 * (rows - 1) - 5
    lx = 6.0
    for face, edge, label in ((GREEN, GREEN, "目安達成"), (AMBER, AMBER, "1件以上"), (RED, RED, "0件"), ("white", GOLD, "今日")):
        ax.add_patch(Circle((lx, ly), 1.1, fc=face, ec=edge, lw=2))
        ax.text(lx + 2, ly, label, color=SUB, fontsize=12.5, va="center")
        lx += 8 + 1.6 * len(label)
    if m["streak"] >= 2:
        ax.text(95, ly, f"{m['streak']}日連続で目安達成", color=GREEN, fontsize=15, fontweight="bold", ha="right", va="center")

    # 着地見込みと予算
    box(4, 16, 92, 15, "#FFF8E6" if color != RED else "#FDECEC")
    if m["forecast"] is not None:
        pct = round(m["forecast"] / m["target"] * 100)
        mark = "▲" if m["forecast"] < m["target"] else "◎"
        ax.text(7, 26, f"このペースのまま行くと月末 {m['forecast']}件（目標の{pct}%）", color=INK, fontsize=19, fontweight="bold", va="center")
        short = m["target"] - m["forecast"]
        ax.text(7, 20, (f"{mark} 目標まで {short}件 足りない。今日からの動きで変えられる。" if short > 0
                        else f"{mark} 目標に届くペース。落とさずに続けよう。"), color=SUB, fontsize=15, va="center")
    else:
        ax.text(7, 23, "月のスタート。ペースづくりは最初の数日で決まる。", color=INK, fontsize=19, fontweight="bold", va="center")
    if budget_share:
        ax.text(95, 18.6, f"チーム月予算のうち {budget_share:.0%} が、あなたの数字", color=SUB, fontsize=12.5, ha="right", va="center")

    # 今日の一言
    line = LINES[(today.toordinal() + sum(map(ord, m["name"]))) % len(LINES)]
    ax.text(50, 8, line, color=NAVY, fontsize=17, fontweight="bold", ha="center", va="center", wrap=True)
    ax.text(50, 2.5, "E.L.P  営業分析BOT", color=GRAY, fontsize=11, ha="center", va="center")

    for t in fig.findobj(Text):  # IPAゴシックは太字が無いので、太字指定は縁取りで太く見せる
        if t.get_fontweight() in ("bold", 700) and t.get_text():
            t.set_path_effects([pe.withStroke(linewidth=t.get_fontsize() / 22, foreground=t.get_color())])
    fig.savefig(path, format="png", dpi=100, facecolor="white")
    plt.close(fig)


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


def flex_message(m: dict, image_url: str, today: dt.date) -> dict:
    color, status = status_of(m)
    head = f"今日はアポ{m['today_target']}件。やり切ろう。" if m["today_target"] else "今日も1件、積み上げよう。"
    alt = f"【今日の任された数字】アポ{m['today_target']}件｜{status}"
    return {
        "type": "flex", "altText": alt,
        "contents": {
            "type": "bubble", "size": "mega",
            "hero": {"type": "image", "url": image_url, "size": "full", "aspectMode": "cover", "aspectRatio": "800:1000"},
            "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                {"type": "text", "text": head, "weight": "bold", "size": "lg", "wrap": True, "color": "#1F3A5F"},
                {"type": "text", "text": status, "size": "sm", "wrap": True, "color": color},
            ]},
            "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                {"type": "button", "style": "primary", "color": "#1F3A5F",
                 "action": {"type": "message", "label": "今日やる！", "text": "今日やる！"}},
                {"type": "button", "style": "secondary",
                 "action": {"type": "message", "label": "くわしいKPIを見る", "text": "KPIを見る"}},
            ]},
        },
    }


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

    by_day = perf_by_member_day(days[0], today - dt.timedelta(days=1))  # 今日の分は朝時点でまだ無い
    meet = meetings_today(today)
    pages = query_all(f"/databases/{KPI_DB}/query", None, NOTION_VERSION_V1)
    active = [p for p in pages if prop_text(p, "メンバー") and rollup_status(p, "在籍状況(名簿参照)") in (None, "稼働中")
              and prop_text(p, "メンバー") not in NO_CARD]
    team_budget = sum(prop_num(p, "今月予算万円") or 0 for p in active)
    users = {u["closer_name"]: u["line_user_id"] for u in sb_get("closer_line_users?select=closer_name,line_user_id")
             if u.get("closer_name")}
    salt = os.environ.get("ELP_SUPABASE_SERVICE_ROLE_KEY", "")[:16]

    sent, failed, skipped = [], [], []
    for p in active:
        name = prop_text(p, "メンバー")
        if args.only and name != args.only:
            continue
        m = build_mission(p, today, days, by_day, meet.get(name, 0))
        if not m:
            skipped.append((name, "今月のアポ目標が未設定"))
            continue
        share = (m["budget"] / team_budget) if team_budget and m["budget"] else None
        local = os.path.join(args.out, f"{today:%Y%m%d}_{name}.png")
        render_card(m, today, local, share)
        _, status = status_of(m)
        print(f"{name}({m['role']}): 実績{m['done']}/目標{m['target']} 理想{m['ideal']:.1f} {status} → 今日{m['today_target']}件 {m['note']}")
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
        res = line_push(to, [flex_message(m, url, today)])
        (sent if res == 200 else failed).append(name if res == 200 else (name, res))
    print(f"送信 {len(sent)}名 / 失敗 {len(failed)}名 / スキップ {len(skipped)}名")
    for n_, why in skipped + failed:
        print(f"  ・{n_}: {why}")
    admin = os.environ.get("IMAGAWA_LINE_USER_ID")
    if (failed or skipped) and admin and not args.dry_run and not args.only:
        lines = [f"📣 今日の任された数字: 送信{len(sent)}名／失敗{len(failed)}名／スキップ{len(skipped)}名"]
        lines += [f"・{n_}: {why}" for n_, why in (failed + skipped)]
        line_push(admin, [{"type": "text", "text": "\n".join(lines)[:4500]}])
    return 0


if __name__ == "__main__":
    sys.exit(main())
