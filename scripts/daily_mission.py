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
# 稼働して0件の日が続いたアポインターだけ、必達を「まず1件」に下げて、チームで一緒に取りに行く言葉で誘う。
# それ以外のアポインターの必達は、少なくとも APPOINTER_MIN 件（1件は低すぎるため）。
APPOINTER_MIN = 2
ZERO_STREAK_FOR_ONE = 2
BIG_AHEAD_RATIO = 1.3  # 月目標の何倍を超えたら「大きく上回っている」として褒めちぎるか
ACHIEVE_BY = 0.75      # 月の稼働日の何割の時点（＝3週目の終わり）で月の目標を達成しておくか。最終週は記録更新の週にする
CANCEL_BUFFER = 1.15   # クーリングオフ・審査落ちを見越して、予算の何倍を契約しておくか（予算2,000万→2,300万）
STRETCH_RATIO = 1.2    # 目標達成済みの人の必達は、自分の平均ペースの何倍にするか（自己ベスト更新を促す）

# 「今日はアポ◯件。やり切ろう。」の言い換え（日替わり）。任せた・頼んだ、の重みと、一緒に取る、のチーム感を両方込める。
HEADLINES = [
    "今日はアポ{n}件。任せた。みんなで勝ち取ろう。",
    "アポ{n}件。君の1件が、チームの1件。頼んだ。",
    "今日のアポ{n}件。取るまで、一緒に走ろう。",
    "アポ{n}件。今日が今月を決める。一緒に取り切ろう。",
    "アポ{n}件、必ず。信じて待ってる。",
    "今日はアポ{n}件。言い訳より、結果で一緒に笑おう。",
    "アポ{n}件。苦しい日こそ、チームで取りに行こう。",
    "今日のアポ{n}件、任せた。足りない分は、みんなで埋める。",
    "アポ{n}件。この数字が、来月の景色を変える。頼んだ。",
    "アポ{n}件を、取り切る。ちゃんと見てるから。頼んだ。",
    "今日の{n}件、君となら取れる。行こう。",
    "アポ{n}件。一緒に、今日も勝ちにいこう。",
]
# 稼働して0件が続いている人向け（責めずに、一緒に取りに行く）
ZERO_HEADLINES = [
    "まず1件、一緒にもぎ取ろう！",
    "今日の最初の1件、みんなで取りにいこう！",
    "まず1件。ここから流れを変えよう、一緒に。",
    "1件でいい。まず1件、もぎ取ろ！",
]
ZERO_REASON = ("{k}日連続でアポ0件。ここで止まると、次の1件がいちばん遠く感じる。"
               "だから今日は、最初の1件にチームで全部賭けよう。1件取れれば流れは変わる。一緒にがんばろ！")
# 月目標を大きく上回っている人を、褒めて、さらなる記録に期待を乗せる
PRAISE_HEADLINES = [
    "ここまで{done}件。目標の{pct}%。圧倒的です。今日の{n}件でまた記録更新、期待してます！",
    "目標{target}件に対して{done}件。チームの誇り。今日も更新、楽しみにしてる！",
    "{done}件、すごすぎる。月末の数字、みんなが期待して見てます！",
    "{pct}%達成。次の景色を見せてほしい。今日は{n}件、一緒に取りにいこう。",
    "{done}件。この数字はもう、チームの基準を変えた。さらに上へ、期待してる。",
    "目標を軽々超えてる{name}さん。ここからが本当の勝負、記録更新を待ってます！",
]


def is_sales_day(d: dt.date) -> bool:
    """売上（商談）が立つ日＝土日と祝日。"""
    return d.weekday() >= 5 or bool(jpholiday and jpholiday.is_holiday(d))


def week_window(today: dt.date) -> tuple[dt.date, dt.date]:
    start = today - dt.timedelta(days=today.weekday())  # 1週間は月曜〜日曜
    return start, start + dt.timedelta(days=6)


def build_mission(page: dict, today: dt.date, days: list[dt.date], by_day: dict, n_meet: int,
                  budgets: dict[str, float], team: dict) -> dict | None:
    name = prop_text(page, "メンバー")
    role = prop_text(page, "役割")
    target = int(prop_num(page, "今月アポ目標概算") or 0)
    if not target:  # クローザーは契約目標から逆算（契約目標 × 1契約に必要なアポ数）
        target = math.ceil((prop_num(page, "今月契約目標") or 0) * (prop_num(page, "1契約に必要なアポ数") or 0))
    declared = int(prop_num(page, "宣言アポ目標") or 0)
    if declared > target and prop_text(page, "宣言月") == today.strftime("%Y-%m"):
        target = declared  # 本人が宣言し直した目標（LINEの「目標を上げる」）
    if not name or role not in ("クローザー", "アポインター") or target <= 0:
        return None
    work = [d for d in days if is_workday(d)]
    total = len(work)
    left = len([d for d in work if d >= today])  # 今日を含む残り稼働日
    mine = by_day.get(name, {})
    past = [d for d in work if d < today]
    # 出勤した日（日報がある日）。定休の月火に稼働した日も含める
    worked = sorted(d for d, v in mine.items() if v.get("出勤") and d <= today)
    last_work = worked[-1] if worked else None
    # ペースは「前回稼働した日」までの結果で比べる（日報が無い日＝休みか未入力かは分からないため、その日までは数えない）
    elapsed = len([d for d in work if last_work and d <= last_work])
    # 月の目標は3週目の終わり（稼働日の ACHIEVE_BY 割の日）までに達成しておき、最終週は記録更新の週にする
    plan_days = max(1, math.ceil(total * ACHIEVE_BY))
    goal_left = max(0, plan_days - len(past))  # 達成の期限まで、今日を含めてあと何稼働日か
    eff_left = goal_left if goal_left > 0 else max(1, left)
    wk_no = min(4, len(past) * 4 // total + 1) if total else 1  # いま月の第何週か（稼働日を4等分）
    done = int(sum(v.get("アポ数", 0) for v in mine.values()))
    visits_month = int(sum(v.get("訪問数", 0) for v in mine.values()))
    ideal = target * min(1.0, elapsed / plan_days)
    gap = done - ideal
    remaining = max(0, target - done)
    base = math.ceil(remaining / eff_left) if remaining else 0
    avg_w = done / len(worked) if worked else 0  # 稼働した日の平均アポ数
    stretch = False
    if not base and avg_w:  # 月の目標は達成済み → 自分の平均ペースの少し上を今日の必達にする（記録更新）
        base, stretch = math.ceil(avg_w * STRETCH_RATIO), True
    factor, note = 1.0, ""
    if role == "クローザー":
        factor = MEETING_FACTOR.get(n_meet, MEETING_FACTOR_MANY) if n_meet else 1.0
        if n_meet:
            note = f"今日は商談{n_meet}件。商談に集中する分、アポ目標を調整"
    today_target = max(1, math.ceil(base * factor)) if base else 0

    # 稼働して（出勤して）アポ0件の日が何日続いているか（出勤していない日は数えず、途切れさせない）
    zero_streak = 0
    for d in reversed(worked):
        if mine[d].get("アポ数", 0) > 0:
            break
        zero_streak += 1
    fierce = role == "アポインター" and zero_streak >= ZERO_STREAK_FOR_ONE
    if role == "アポインター":
        today_target = 1 if fierce else max(APPOINTER_MIN, today_target)
    big_ahead = (not fierce) and done >= target * BIG_AHEAD_RATIO and gap >= 2

    forecast = round(done + avg_w * max(0, left - (1 if today in worked else 0))) if worked else None  # 稼働日の平均ペースで残りを走った場合の月末見込み
    per_day = target / total if total else 0
    day_state: dict[dt.date, str] = {}
    for d in days:
        if d > today:
            day_state[d] = "future" if is_workday(d) else "rest"
        elif d == today:
            day_state[d] = "today"
        elif d in worked:
            n = mine[d].get("アポ数", 0)
            day_state[d] = "ok" if n >= per_day and n > 0 else "some" if n > 0 else "zero"
        else:
            day_state[d] = "off" if is_workday(d) else "rest"  # 日報なし（休みか未入力）／定休
    streak = 0
    for d in reversed([d for d in days if d < today and day_state[d] not in ("off", "rest")]):
        if day_state[d] == "ok":
            streak += 1
        else:
            break
    yday_work = [d for d in past][-1] if past else None
    stale_since = last_work if (last_work and yday_work and last_work < yday_work) else None  # 前回稼働日が昨日より前

    # 売上は土日（と祝日）に立つ。稼働日で割らず、「残りの土日」で達成する計算にする（ここで達成して、更新する）
    # 予算に加えて「クーリングオフ・審査落ちを見越した契約ベースの目標」（予算×CANCEL_BUFFER）まで契約しておく
    wk_start, wk_end = week_window(today)
    sdays = [d for d in days if is_sales_day(d)]
    s_past = [d for d in sdays if d < today]
    s_left_days = [d for d in sdays if d >= today]  # 今日を含む、残りの売上が立つ日
    s_plan = max(1, math.ceil(len(sdays) * ACHIEVE_BY))  # 3週目の終わりまでに数える土日の日数
    s_goal_left = max(0, s_plan - len(s_past))
    s_eff_left = s_goal_left if s_goal_left > 0 else max(1, len(s_left_days))
    s_wk_no = min(4, len(s_past) * 4 // len(sdays) + 1) if sdays else 1
    week_s_left = len([d for d in s_left_days if d <= wk_end])  # 今週（水〜火）の、残りの売上が立つ日
    week_goal_left = min(week_s_left, s_eff_left)
    money = None
    budget_p = budgets.get(name)
    ideal_k = min(1.0, len(s_past) / s_plan)
    if role == "クローザー" and budget_p:
        goal_p = budget_p * CANCEL_BUFFER
        sales_m = sum(v.get("売上", 0) for v in mine.values())
        rem_p = max(0.0, goal_p - sales_m)
        money = {"budget": budget_p, "goal": goal_p, "sales": sales_m, "remaining": rem_p,
                 "to_budget": max(0.0, budget_p - sales_m), "ideal": goal_p * ideal_k,
                 "week_aim": math.ceil(rem_p * week_goal_left / s_eff_left) if rem_p else 0,
                 "per_day": math.ceil(rem_p / s_eff_left) if rem_p else 0,
                 "week_sales": sum(v.get("売上", 0) for d, v in mine.items() if d >= wk_start)}
    goal_c = team["budget"] * CANCEL_BUFFER
    rem_c = max(0.0, goal_c - team["sales"])
    company = {"budget": team["budget"], "goal": goal_c, "sales": team["sales"], "remaining": rem_c,
               "to_budget": max(0.0, team["budget"] - team["sales"]), "left": left,
               "week_aim": math.ceil(rem_c * week_goal_left / s_eff_left) if rem_c else 0,
               "per_day": math.ceil(rem_c / s_eff_left) if rem_c else 0,
               "n_closers": team.get("n_closers", 0),
               "week_aim_each": math.ceil(rem_c * week_goal_left / s_eff_left / team["n_closers"]) if rem_c and team.get("n_closers") else 0}
    sales_plan = {"days_left": s_left_days, "wk_no": s_wk_no, "week_left": week_s_left, "eff_left": s_eff_left,
                  "ideal_k": ideal_k, "total": len(sdays)}
    return {"name": name, "role": role, "target": target, "declared": declared, "done": done, "ideal": ideal, "gap": gap,
            "left": left, "total": total, "elapsed": elapsed, "today_target": today_target,
            "forecast": forecast, "note": note, "stretch": stretch, "n_meet": n_meet,
            "streak": streak, "day_state": day_state, "days": days, "wk_no": wk_no, "plan_days": plan_days, "goal_left": goal_left, "zero_streak": zero_streak, "fierce": fierce, "big_ahead": big_ahead,
            "stale_since": stale_since, "last_work": last_work,
            "visit_today": int(prop_num(page, "今日やるべき訪問数") or 0), "visits_month": visits_month,
            "budget": prop_num(page, "今月予算万円") or 0, "week_apo": int(prop_num(page, "今週の必要アポ数") or 0),
            "week_visit": int(prop_num(page, "今週の必要訪問数") or 0), "money": money, "company": company, "sales_plan": sales_plan}


def pick(items: list[str], m: dict, today: dt.date) -> str:
    return items[(today.toordinal() + sum(map(ord, m["name"]))) % len(items)]


def headline(m: dict, today: dt.date) -> str:
    if m["fierce"]:
        return pick(ZERO_HEADLINES, m, today)
    if m["big_ahead"]:
        return pick(PRAISE_HEADLINES, m, today).format(
            name=m["name"], done=m["done"], target=m["target"], n=m["today_target"], pct=round(m["done"] / m["target"] * 100))
    if not m["today_target"]:
        return "今日も1件、積み上げよう。"
    return pick(HEADLINES, m, today).format(n=m["today_target"])


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


# ------------------------------------------------------------------ カード画像（2枚。LINEのカルーセルで横に送る）
PX_PER_UNIT = 10.4  # 横100単位＝1040px。縦は中身に合わせて決める（上から順に積んで、最後に切る）


def man(v: float) -> str:
    return f"{v:,.0f}万" if abs(v - round(v)) < 0.05 else f"{v:,.1f}万"


def _canvas(Hu: float):
    fig = plt.figure(figsize=(10.4, Hu * PX_PER_UNIT / 100), dpi=100, facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(0, Hu); ax.axis("off")

    def box(x, y, w, h, fc, ec="none", r=1.6, lw=0):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw))

    def gauge(y, h, value, goal, ideal, col, ideal_label):
        box(5, y, 90, h, "#E6EAF0", r=1.2)
        fill = min(1.0, value / goal) if goal else 0
        if fill > 0:
            box(5, y, max(90 * fill, 2.4), h, col, r=1.2)
        ix = 5 + 90 * min(1.0, ideal / goal) if goal else 5
        ax.plot([ix, ix], [y - 1.6, y + h + 1.6], color=INK, lw=3, ls=(0, (3, 2)))
        ax.text(min(max(ix, 22), 68), y + h + 3.4, ideal_label, color=INK, fontsize=15, ha="center", va="center")

    return fig, ax, box, gauge


def _finish(fig, ax, Hu: float, bottom: float, path: str) -> int:
    ax.set_ylim(bottom, Hu)
    height_units = Hu - bottom
    fig.set_size_inches(10.4, height_units * PX_PER_UNIT / 100)
    for t in fig.findobj(Text):  # IPAゴシックは太字が無いので、太字指定は縁取りで太く見せる
        if t.get_fontweight() in ("bold", 700) and t.get_text():
            t.set_path_effects([pe.withStroke(linewidth=t.get_fontsize() / 22, foreground=t.get_color())])
    fig.savefig(path, format="png", dpi=100, facecolor="white")
    plt.close(fig)
    return int(round(height_units * PX_PER_UNIT))


def _header(ax, box, Hu, m, today, sub):
    box(0, Hu - 18, 100, 18, NAVY, r=0)
    ax.text(5, Hu - 6.5, f"{m['name']}さん", color="white", fontsize=30, fontweight="bold", va="center")
    ax.text(95, Hu - 6.5, f"{today.month}/{today.day}（{WD[today.weekday()]}）", color="#C9D6E8", fontsize=22, ha="right", va="center")
    # 名前と日付のあいだに、日付と同じ大きさで（長い名前でも重ならない位置・大きさに収める）
    name_end = 5 + 4.3 * (len(m["name"]) + 1) + 3
    fs = max(14.0, min(22.0, (76 - name_end) / 2.27))  # 日付と同じ22pt。長い名前のときだけ、重ならないよう少し小さくする
    ax.text((name_end + 76) / 2, Hu - 6.5, "日本一の営業マンになるための現在地", color="#9FB3CF", fontsize=fs, ha="center", va="center")
    ax.text(5, Hu - 14, sub, color=GOLD, fontsize=21, fontweight="bold", va="center")
    return Hu - 21


def render_card1(m: dict, today: dt.date, path: str) -> int:
    """1枚目: 今日の必達・今月のアポのペース・稼働日。"""
    Hu = 200
    fig, ax, box, gauge = _canvas(Hu)
    color, status = status_of(m)
    y = _header(ax, box, Hu, m, today, "今日、あなたが任された数字")

    # 今日の必達（いちばん大きく）
    box(4, y - 32, 92, 32, "#FDECEC" if m["fierce"] else "#FFF8E6" if m["big_ahead"] else BG)
    ax.text(50, y - 5, "今日の必達！　任された数字！", color=RED if m["fierce"] else NAVY, fontsize=26, fontweight="bold", ha="center", va="center")
    if m["today_target"]:
        ax.text(14, y - 17, "アポ", color=SUB, fontsize=30, fontweight="bold", ha="center", va="center")
        ax.text(52, y - 17, f"{m['today_target']}", color=RED if m["fierce"] else (color if color != GREEN else INK),
                fontsize=96, fontweight="bold", ha="right", va="center")
        ax.text(54, y - 19, "件", color=INK, fontsize=36, fontweight="bold", ha="left", va="center")
        if m["fierce"]:
            sub = f"{m['zero_streak']}日連続アポ0件。まず1件、一緒にもぎ取ろう"
        elif m["big_ahead"]:
            sub = f"目標の{round(m['done'] / m['target'] * 100)}%達成。さらに高みへ。期待してる"
        elif m["note"]:
            sub = m["note"]
        elif today.weekday() == 2 and m["week_apo"]:  # 水曜は週次の「今週任された数字」を入れる
            sub = f"今週の数字：アポ{m['week_apo']}件／訪問{m['week_visit']}件"
        elif m["stretch"]:
            sub = "月の目標は達成済み。自分の記録を更新しよう"
        else:
            sub = (f"残り{m['target'] - m['done']}件 ÷ 3週目までの残り{m['goal_left']}稼働日" if m["goal_left"] > 0
                   else f"残り{m['target'] - m['done']}件 ÷ 残り{m['left']}稼働日（期限の3週目を過ぎてます。必ず埋め切ろう）")
        ax.text(50, y - 27.5, sub, color=RED if m["fierce"] else (NAVY if m["big_ahead"] else SUB), fontsize=15.5, ha="center", va="center",
                fontweight="bold" if (m["fierce"] or m["big_ahead"]) else "normal")
    else:
        ax.text(50, y - 17, "今月のアポ目標 達成！", color=GREEN, fontsize=34, fontweight="bold", ha="center", va="center")
        ax.text(50, y - 26, "ここからの1件が、さらに上積み", color=SUB, fontsize=18, ha="center", va="center")
    y -= 38

    # 月ゲージ: 理想ペースの線と実績
    ax.text(5, y, "今月のアポ", color=INK, fontsize=22, fontweight="bold", va="center")
    tgt = f"目標 {m['target']}件" + ("（自分で宣言）" if m["declared"] and m["declared"] == m["target"] else "")
    ax.text(95, y, tgt, color=SUB, fontsize=18, ha="right", va="center")
    gauge(y - 15, 7, m["done"], m["target"], m["ideal"], color, f"理想 {m['ideal']:.0f}件（3週目で達成）")
    ax.text(6, y - 21, f"実績 {m['done']}件", color=color, fontsize=24, fontweight="bold", va="center")
    ax.text(95, y - 21, status, color=color, fontsize=19, fontweight="bold", ha="right", va="center")
    y -= 28
    if m["stale_since"]:
        d = m["stale_since"]
        ax.text(5, y, f"※前回の稼働日（{d.month}/{d.day}）までの結果で計算しています", color=SUB, fontsize=13.5, va="center")
        y -= 5

    # 稼働カレンダー（週ごとに1行。上に月〜日のガイド。定休の月火も、稼働した日は色が付く）
    ax.text(5, y, f"稼働カレンダー  今日を含めて残り{m['left']}日（月の稼働日 {m['total']}日）", color=INK, fontsize=17, va="center")
    gx0, gw = 9.0, 82.0
    colw = gw / 7
    for i, wd in enumerate(WD):
        ax.text(gx0 + colw * i + colw / 2, y - 6, wd, color=SUB if i >= 2 else GRAY, fontsize=15, fontweight="bold", ha="center", va="center")
    first = m["days"][0]
    weeks_rows: dict[int, list] = defaultdict(list)
    for d in m["days"]:
        weeks_rows[(d.toordinal() - first.toordinal() + first.weekday()) // 7].append(d)
    row_h = 6.8
    for r, ds in sorted(weeks_rows.items()):
        cy = y - 12 - row_h * r
        for d in ds:
            cx = gx0 + colw * d.weekday() + colw / 2
            st = m["day_state"][d]
            face = {"ok": GREEN, "some": AMBER, "zero": RED, "off": "#E6EAF0", "rest": "white", "today": "white", "future": "white"}[st]
            edge = {"today": GOLD, "future": GRAY, "off": "#E6EAF0", "rest": "#EEF1F5"}.get(st, face)
            ax.add_patch(Circle((cx, cy), 2.7, fc=face, ec=edge, lw=4 if st == "today" else 1.4, ls="-"))
            ax.text(cx, cy, str(d.day), color="white" if st in ("ok", "some", "zero") else (GRAY if st == "rest" else SUB),
                    fontsize=12.5, ha="center", va="center")
    ly = y - 12 - row_h * (len(weeks_rows) - 1) - 6
    lx = 6.0
    for face, edge, label in ((GREEN, GREEN, "目安達成"), (AMBER, AMBER, "1件以上"), (RED, RED, "0件"), ("#E6EAF0", "#E6EAF0", "日報なし"), ("white", GOLD, "今日")):
        ax.add_patch(Circle((lx, ly), 1.1, fc=face, ec=edge, lw=2))
        ax.text(lx + 2, ly, label, color=SUB, fontsize=12.5, va="center")
        lx += 7 + 1.55 * len(label)
    if m["streak"] >= 2:
        ax.text(95, ly - 5, f"{m['streak']}日連続で目安達成", color=GREEN, fontsize=15, fontweight="bold", ha="right", va="center")
        ly -= 5
    ax.text(50, ly - 9, "E.L.P  営業分析BOT", color=GRAY, fontsize=11, ha="center", va="center")
    return _finish(fig, ax, Hu, ly - 13, path)


def render_card2(m: dict, today: dt.date, path: str) -> int:
    """2枚目: 着地見込み・任された売上（クローザー）・チームの月予算の残り（週で見る）。"""
    Hu = 200
    fig, ax, box, gauge = _canvas(Hu)
    color, status = status_of(m)
    y = _header(ax, box, Hu, m, today, "今月の着地と、予算")

    # 着地見込み
    box(4, y - 15, 92, 15, "#FFF8E6" if color != RED else "#FDECEC")
    if m["forecast"] is not None:
        pct = round(m["forecast"] / m["target"] * 100)
        mark = "▲" if m["forecast"] < m["target"] else "◎"
        ax.text(7, y - 5, f"このペースで走ると月末 アポ{m['forecast']}件（目標の{pct}%）", color=INK, fontsize=19, fontweight="bold", va="center")
        short = m["target"] - m["forecast"]
        ax.text(7, y - 11, (f"{mark} 目標まで {short}件 足りない。今日からの動きで変えられる。" if short > 0
                            else f"{mark} 目標に届くペース。さらに上積みを期待してる。"), color=SUB, fontsize=15, va="center")
    else:
        ax.text(7, y - 8, "月のスタート。ペースづくりは最初の数日で決まる。", color=INK, fontsize=19, fontweight="bold", va="center")
    y -= 22

    mo, co = m["money"], m["company"]
    dl = sp_days = m["sales_plan"]["days_left"]
    wk_text = ("今月の売上が立つ土日は、残り" + str(len(dl)) + "日（" + "・".join(f"{d.day}日" for d in dl) + "）。ここで達成して、更新する"
               if dl else "今月の土日は終了。月末の取りこぼしを確認しよう")
    # 月の進め方: 3週目で目標を達成し、最終週は記録更新
    sp = m["sales_plan"]
    wk = sp["wk_no"]
    ax.text(5, y, "今月の進め方（売上は土日で立てる）", color=INK, fontsize=22, fontweight="bold", va="center")
    stages = [("1週目", "土台"), ("2週目", "加速"), ("3週目", "達成！"), ("4週目", "記録更新！")]
    sw = 21.5
    for i, (a_, b_) in enumerate(stages):
        cur = (wk == i + 1)
        face = NAVY if cur else ("#EAF1FB" if i < wk - 1 else "#F3F5F8")
        box(5 + i * (sw + 1.3), y - 13.5, sw, 9.5, face, r=1.2)
        ax.text(5 + i * (sw + 1.3) + sw / 2, y - 7.3, a_, color="white" if cur else SUB, fontsize=15, ha="center", va="center")
        ax.text(5 + i * (sw + 1.3) + sw / 2, y - 11.3, b_, color=GOLD if cur else (NAVY if i == 3 else SUB), fontsize=17, fontweight="bold", ha="center", va="center")
    y -= 21

    if mo:
        ax.text(5, y, "あなたに任された売上", color=INK, fontsize=22, fontweight="bold", va="center")
        ax.text(95, y, f"月予算 {man(mo['budget'])}", color=SUB, fontsize=18, ha="right", va="center")
        ax.text(95, y - 4.6, f"見越し目標 {man(mo['goal'])}（クーリング・審査落ち+15%）", color=SUB, fontsize=13, ha="right", va="center")
        pcol = GREEN if mo["sales"] >= mo["ideal"] - 0.5 else (AMBER if mo["sales"] >= mo["ideal"] * 0.7 else RED)
        gauge(y - 19, 6, mo["sales"], mo["goal"], mo["ideal"], pcol, f"理想 {man(mo['ideal'])}（3週目で達成）")
        ax.text(6, y - 25, f"実績 {man(mo['sales'])}", color=pcol, fontsize=22, fontweight="bold", va="center")
        ax.text(95, y - 25, f"見越し目標まで あと {man(mo['remaining'])}" if mo["remaining"] else "見越し目標 達成済み", color=INK if mo["remaining"] else GREEN,
                fontsize=19, fontweight="bold", ha="right", va="center")
        box(4, y - 38, 92, 9, "#EAF1FB")
        ax.text(7, y - 33.5, f"あなたの今週の狙い  +{man(mo['week_aim'])}" if mo["remaining"] else "ここから先は記録更新。期待してる",
                color=NAVY, fontsize=22 if mo["remaining"] else 19, fontweight="bold", va="center")
        ax.text(93, y - 33.5, f"今週ここまで {man(mo['week_sales'])}", color=SUB, fontsize=13.5, ha="right", va="center")
        ax.text(5, y - 44, wk_text, color=RED if sp["days_left"] and len(sp["days_left"]) <= 2 else SUB, fontsize=14.5, fontweight="bold", va="center")
        y -= 51

    pct_b = co["sales"] / co["budget"] * 100 if co["budget"] else 0
    if not mo:
        ax.text(5, y + 3, wk_text, color=RED if dl and len(dl) <= 2 else SUB, fontsize=14.5, fontweight="bold", va="center")
        y -= 5
    box(4, y - 43, 92, 43, "#F3F5F8")
    ax.text(7, y - 5, "チームの月予算", color=SUB, fontsize=16, va="center")
    ax.text(93, y - 5, man(co["budget"]), color=INK, fontsize=22, fontweight="bold", ha="right", va="center")
    ax.text(7, y - 11.5, "ここまでの実績", color=SUB, fontsize=16, va="center")
    ax.text(93, y - 11.5, f"{man(co['sales'])}（予算の{pct_b:.0f}%）", color=INK, fontsize=22, fontweight="bold", ha="right", va="center")
    ax.text(7, y - 18, "予算まで、あと", color=SUB, fontsize=16, va="center")
    ax.text(93, y - 18, man(co["to_budget"]), color=INK, fontsize=22, fontweight="bold", ha="right", va="center")
    ax.plot([7, 93], [y - 22, y - 22], color=LINE, lw=1.5)
    ax.text(7, y - 27.5, f"見越し目標 {man(co['goal'])} まで、あと", color=INK, fontsize=17, fontweight="bold", va="center")
    ax.text(93, y - 27.5, man(co["remaining"]), color=RED if co["remaining"] > 0 else GREEN, fontsize=30, fontweight="bold", ha="right", va="center")
    ax.text(7, y - 33.5, f"チーム全体の今週の狙い  +{man(co['week_aim'])}", color=NAVY, fontsize=18, fontweight="bold", va="center")
    ax.text(7, y - 38.5, f"クローザー{co['n_closers']}人で割ると、1人あたり約{man(co['week_aim_each'])}（残りの土日{len(dl)}日で1日 {man(co['per_day'])}）",
            color=SUB, fontsize=13.5, va="center")
    y -= 49

    line = pick(LINES, m, today)
    ax.text(50, y - 3.5, line, color=NAVY, fontsize=16, fontweight="bold", ha="center", va="center")
    ax.text(50, y - 9.5, "E.L.P  営業分析BOT", color=GRAY, fontsize=11, ha="center", va="center")
    return _finish(fig, ax, Hu, y - 13, path)


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


def flex_message(m: dict, url1: str, h1: int, url2: str, h2: int, today: dt.date) -> dict:
    """横に2枚（今日の必達／今月の着地と予算）のカルーセル。1通として数える。"""
    color, status = status_of(m)
    body = [{"type": "text", "text": headline(m, today), "weight": "bold", "size": "lg", "wrap": True,
             "color": "#B3261E" if m["fierce"] else "#1F3A5F"}]
    if m["fierce"]:
        body.append({"type": "text", "text": ZERO_REASON.format(k=m["zero_streak"]), "size": "sm", "wrap": True, "color": "#444444"})
    else:
        body.append({"type": "text", "text": status, "size": "sm", "wrap": True, "color": color})
    footer = [
        {"type": "button", "style": "primary", "color": "#1F3A5F", "action": {"type": "message", "label": "今日やる！", "text": "今日やる！"}},
        {"type": "button", "style": "secondary", "action": {"type": "message", "label": "今月の進み具合を見る", "text": "今月の進み具合を見る"}},
    ]
    if m["done"] >= m["target"]:  # 目標を達成済みの人には、自分で新しい目標を宣言してもらう
        footer.insert(1, {"type": "button", "style": "primary", "color": "#E0A100",
                          "action": {"type": "message", "label": "目標を上げる", "text": "目標を上げる"}})
    b1 = {"type": "bubble", "size": "mega",
          "hero": {"type": "image", "url": url1, "size": "full", "aspectMode": "fit", "aspectRatio": f"1040:{h1}"},
          "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
          "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": footer}}
    body2 = []
    if m["money"]:
        mo = m["money"]
        body2.append({"type": "text", "weight": "bold", "size": "md", "wrap": True, "color": "#1F3A5F",
                      "text": (f"あなたの今週の狙い +{man(mo['week_aim'])}／見越し目標まであと{man(mo['remaining'])}（残りの土日{len(m['sales_plan']['days_left'])}日）" if mo["remaining"]
                               else "見越し目標は達成済み。記録更新を期待してる")})
    body2.append({"type": "text", "size": "sm", "wrap": True, "color": "#444444",
                  "text": f"チームは予算{man(m['company']['budget'])}に対して{man(m['company']['sales'])}。見越し目標{man(m['company']['goal'])}まであと{man(m['company']['remaining'])}、今週の狙い +{man(m['company']['week_aim'])}"})
    b2 = {"type": "bubble", "size": "mega",
          "hero": {"type": "image", "url": url2, "size": "full", "aspectMode": "fit", "aspectRatio": f"1040:{h2}"},
          "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body2}}
    alt = f"【今日の任された数字】アポ{m['today_target']}件｜{status}" + (f"｜今週の狙い+{man(m['money']['week_aim'])}" if m["money"] else "")
    return {"type": "flex", "altText": alt, "contents": {"type": "carousel", "contents": [b1, b2]}}


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
        if not d or dt.date.fromisoformat(d[:10]) > today:
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
    ap.add_argument("--skip-verify", action="store_true", help="商談一覧と実績DBの突き合わせを省く（テスト用）")
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
    if not bad and not args.skip_verify:  # 商談一覧（商談1件ずつの記録）と実績DBが人別に一致するか（週次MT資料と同じ確認）
        sys.path.insert(0, os.path.join(HERE, "..", "tools", "shuuji"))
        import verify_numbers  # noqa: E402
        bad = verify_numbers.compare(days[0], today)
    if bad:
        print("【数字の再計算が合いません。配信を止めます】\n" + "\n".join(bad))
        if admin and not args.dry_run:
            line_push(admin, [{"type": "text", "text": "⚠ 今日の任された数字カードの配信を止めました（再計算が合いません）\n" + "\n".join(bad)[:4000]}])
        return 1
    print(f"確認OK（再計算・商談一覧との突き合わせ）: 全社売上 {team_sales:,.1f}万 / 全社予算 {team_budget:,.1f}万 / 実績のあるメンバー{len(by_day)}名")

    meet = meetings_today(today)
    pages = query_all(f"/databases/{KPI_DB}/query", None, NOTION_VERSION_V1)
    active = [p for p in pages if prop_text(p, "メンバー") and rollup_status(p, "在籍状況(名簿参照)") in (None, "稼働中")
              and prop_text(p, "メンバー") not in NO_CARD]
    users = {u["closer_name"]: u["line_user_id"] for u in sb_get("closer_line_users?select=closer_name,line_user_id")
             if u.get("closer_name")}
    salt = os.environ.get("ELP_SUPABASE_SERVICE_ROLE_KEY", "")[:16]

    team["n_closers"] = len([p for p in active if prop_text(p, "役割") == "クローザー"])
    sent, failed, skipped = [], [], []
    for p in active:
        name = prop_text(p, "メンバー")
        if args.only and name != args.only:
            continue
        m = build_mission(p, today, days, by_day, meet.get(name, 0), budgets, team)
        if not m:
            skipped.append((name, "今月のアポ目標が未設定"))
            continue
        local1 = os.path.join(args.out, f"{today:%Y%m%d}_{name}_1.png")
        local2 = os.path.join(args.out, f"{today:%Y%m%d}_{name}_2.png")
        h1 = render_card1(m, today, local1)
        h2 = render_card2(m, today, local2)
        _, status = status_of(m)
        mo = m["money"]
        print(f"{name}({m['role']}): アポ{m['done']}/{m['target']} 理想{m['ideal']:.1f} {status} → 今日{m['today_target']}件"
              + (f" 0件連続{m['zero_streak']}日" if m["zero_streak"] else "") + (f" {m['note']}" if m["note"] else "")
              + (" 【褒める】" if m["big_ahead"] else "") + (" 【まず1件】" if m["fierce"] else "")
              + (f" | 売上{mo['sales']:.1f}/{mo['budget']:.1f}万 あと{mo['remaining']:.1f} 今週+{mo['week_aim']}" if mo else "")
              + (f" ※前回稼働日{m['stale_since']}までで計算" if m["stale_since"] else ""))
        if args.dry_run:
            continue
        to = users.get(name)
        if not to:
            skipped.append((name, "LINE未登録"))
            continue
        token = hashlib.sha256(f"{salt}{name}{today}".encode()).hexdigest()[:16]
        try:
            url1 = supabase_upload(local1, f"{FOLDER}/{today:%Y%m%d}/{token}_1.png")
            url2 = supabase_upload(local2, f"{FOLDER}/{today:%Y%m%d}/{token}_2.png")
        except Exception as e:  # noqa: BLE001
            failed.append((name, f"画像のアップロード失敗: {e}"))
            continue
        res = line_push(to, [flex_message(m, url1, h1, url2, h2, today)])
        (sent if res == 200 else failed).append(name if res == 200 else (name, res))
    if admin and not args.dry_run and not args.only:
        line_push(admin, [{"type": "text", "text": (
            f"✅ 今日の任された数字カード: 配信前の数字確認OK\n・{today.month}/{today.day}までのNotion実績を読み込み\n"
            f"・メンバー別のアポ数・売上を別の読み方で再計算 → 一致\n・商談一覧と実績DBを人別に突き合わせ → 一致\n"
            f"・全社売上 {team_sales:,.1f}万／予算 {team_budget:,.1f}万\n送信 {len(sent)}名／失敗 {len(failed)}名／スキップ {len(skipped)}名")}])
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
