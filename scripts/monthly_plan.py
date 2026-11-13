#!/usr/bin/env python3
"""月次MT用の個人計画: 翌月の個人予算の配分・個人目標（先月×110%）・「どこをどれだけ改善すれば何が達成できるか」を出す。

使い方:
  python3 scripts/monthly_plan.py --month 2026-11 --total 2200 --exclude 藤江,樺澤,古賀
  python3 scripts/monthly_plan.py ... --asof 2026-11-03   # 実績はこの日まで読む（MT前日の集計用。予算配分は前月末で固定）
  python3 scripts/monthly_plan.py ... --out plan.json      # 結果をJSONで保存（MTスライド・Botが読む）
  python3 scripts/monthly_plan.py ... --write-budget       # 「💰 予算と達成率」に翌月の予算行を書く（今川さんの確認後だけ）

考え方（2026-10-29 今川さん指示）:
  - 個人予算: 全社予算（--total）を、前月予算×1.1を土台に、直近3か月の実質売上の実績で補正して配分する
      重み = 0.7×（前月予算のシェア）＋0.3×（直近3か月の月平均実質売上のシェア。稼働した月だけで平均）
      前月の実質売上を下回る人はそこまで引き上げ（scripts/monthly_budget_floor.py と同じ下限ルール）、残りで合計を合わせる
      売上はクローザーとアポインターで50:50按分（実績DBの契約金額がすでに按分済み）なので、個人予算の合計＝全社予算
  - 個人目標: 前月の1日あたりの数字（平日／土日祝で分けて）× 翌月の見込み稼働日（前月の出勤率×翌月の暦）× 1.1
      稼働日は会社の暦（水〜日と、祝日の月火）。月の途中から入った人は、入った日からの出勤率で見る
  - 売上の見込み: アポ×（アポ1件あたりの契約率）×（契約1件あたりの金額）×（1−キャンセル率）
                  ＋ 契約（クローザー側）×（契約1件あたりの金額）×（1−キャンセル率）
      率は直近3か月の本人の実績。件数が少ない人は全社の値に寄せる（少ない件数の偶然で極端な数字にならないように）
  - 改善ポイント: 各工程の率を「できてる人の水準」（同じ役割で件数が十分ある人の上位25%）まで上げたら、
      売上が何万円増えるかを計算し、増える額が大きい順に出す。予算との差を1つの工程だけで埋めるなら、
      その率をいくつにすればよいかも出す
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from monthly_budget_floor import (BUDGET_DB, KPI_DB, NOTION_VERSION_V1, NOTION_VERSION_V2,  # noqa: E402
                                  PERFORMANCE_DB, load_env, notion, prop_text, query_all)

try:
    import jpholiday
except ImportError:  # 祝日の月火を稼働日に数えられないだけ
    jpholiday = None

FIELDS = ["訪問数", "在宅数", "対面数", "対象数", "対話数", "アポ数", "商談数", "有効商談数", "契約", "契約（アポ）",
          "契約金額（万円）", "キャンセル金額（万円）", "実質売上（万円）", "クーリン", "審査落"]
GROWTH = 1.1
BUDGET_WEIGHT, PERF_WEIGHT = 0.7, 0.3
NOT_PEOPLE = {"催事", ""}   # 実績DBのメンバー欄にあるが人ではない行
SHRINK_APO, SHRINK_DEAL, SHRINK_CONTRACT = 15, 6, 4   # 率を全社の値に寄せるときの重み（件数の目安）


def num(page: dict, key: str) -> float:
    p = page["properties"].get(key) or {}
    t = p.get("type")
    v = p.get(t)
    if t == "formula":
        v = (v or {}).get((v or {}).get("type"))
    return float(v) if isinstance(v, (int, float)) else 0.0


def is_workday(d: dt.date) -> bool:
    """水〜日と、祝日の月火（scripts/daily_mission.py と同じ）。"""
    return d.weekday() >= 2 or bool(jpholiday and jpholiday.is_holiday(d))


def is_heavy(d: dt.date) -> bool:
    """土日祝（商談が入りやすい日）。"""
    return d.weekday() >= 5 or bool(jpholiday and jpholiday.is_holiday(d))


def month_range(ym: str) -> tuple[dt.date, dt.date]:
    y, m = map(int, ym.split("-"))
    first = dt.date(y, m, 1)
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return first, nxt - dt.timedelta(days=1)


def shift_month(ym: str, k: int) -> str:
    y, m = map(int, ym.split("-"))
    i = y * 12 + (m - 1) + k
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def company_days(first: dt.date, last: dt.date) -> dict[str, int]:
    out = {"heavy": 0, "light": 0}
    d = first
    while d <= last:
        if is_workday(d):
            out["heavy" if is_heavy(d) else "light"] += 1
        d += dt.timedelta(days=1)
    return out


def shrink(hits: float, base: float, rate0: float, k: float) -> float:
    """件数が少ないときに全社の率（rate0）へ寄せた率。"""
    return (hits + rate0 * k) / (base + k) if base + k > 0 else rate0


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="計画する月（YYYY-MM）")
    ap.add_argument("--total", type=float, required=True, help="全社予算（万円）")
    ap.add_argument("--exclude", default="", help="配分しないメンバー（カンマ区切り）")
    ap.add_argument("--asof", help="実績を読む最終日（YYYY-MM-DD）。省略時は前月末")
    ap.add_argument("--out", help="結果JSONの保存先")
    ap.add_argument("--set", default="", help="個別の指定（例: 福本=85,宮腰=岡野-10,今川=+10%%）")
    ap.add_argument("--min", type=float, help="個人予算の最低額（万円）。省略時はアポ1件分＝契約1件あたりの金額（折半後）")
    ap.add_argument("--smooth", type=float, default=0.8, help="差の縮め方（1=そのまま、小さいほど平らに）")
    ap.add_argument("--as-closer", default="", help="今月からクローザーとして見る人（カンマ区切り。例: 福本）")
    ap.add_argument("--write-budget", action="store_true", help="💰予算と達成率に翌月の予算行を書く")
    ap.add_argument("--lock", help="確定済みの計画JSON。予算と目標はこれを使い、実績・見込み・改善ポイントだけ最新にする（MT前日の集計用）")
    ap.add_argument("--publish", action="store_true",
                    help="LINE Botの「今月のアクションプランを登録する」が読む計画JSONをSupabase Storage（非公開）に置く")
    ap.add_argument("--write-kpi", action="store_true",
                    help="📊KPI逆算データに翌月の予算と月目標（クローザー=契約数、アポインター=アポ数）を書く")
    args = ap.parse_args()
    load_env()

    target, prev = args.month, shift_month(args.month, -1)
    t_first, t_last = month_range(target)
    p_first, p_last = month_range(prev)
    w_first = month_range(shift_month(target, -3))[0]
    asof = dt.date.fromisoformat(args.asof) if args.asof else p_last
    exclude = {x.strip() for x in args.exclude.split(",") if x.strip()}

    rows = query_all(f"/data_sources/{PERFORMANCE_DB}/query",
                     {"and": [{"property": "実績日", "date": {"on_or_after": w_first.isoformat()}},
                              {"property": "実績日", "date": {"on_or_before": asof.isoformat()}}]}, NOTION_VERSION_V2)
    monthly = collections.defaultdict(collections.Counter)    # (人, 月) → 合計
    daytype = collections.defaultdict(collections.Counter)    # (人, heavy/light) → 前月の合計と出勤日数
    first_seen: dict[str, dt.date] = {}
    role: dict[str, str] = {}
    for r in rows:
        name = prop_text(r, "メンバー")
        if name in NOT_PEOPLE:
            continue
        d = dt.date.fromisoformat(r["properties"]["実績日"]["date"]["start"][:10])
        role[name] = prop_text(r, "役割") or role.get(name, "")
        ym = d.strftime("%Y-%m")
        for f in FIELDS:
            monthly[(name, ym)][f] += num(r, f)
        worked = num(r, "訪問数") > 0 or num(r, "有効商談数") > 0 or num(r, "商談数") > 0
        if worked:
            monthly[(name, ym)]["稼働日"] += 1
            first_seen[name] = min(first_seen.get(name, d), d)
        if ym == prev:
            key = (name, "heavy" if is_heavy(d) else "light")
            for f in FIELDS:
                daytype[key][f] += num(r, f)
            if worked:
                daytype[key]["稼働日"] += 1

    budgets_prev, agency = {}, {}
    for b in query_all(f"/data_sources/{BUDGET_DB}/query", {"property": "年月（YYYY-MM）", "rich_text": {"equals": prev}},
                       NOTION_VERSION_V2):
        n = prop_text(b, "メンバー")
        v = (b["properties"].get("予算（万円）") or {}).get("number")
        if n and v is not None:
            budgets_prev[n] = float(v)
            sel = (b["properties"].get("代理店") or {}).get("select")
            agency[n] = sel["name"] if sel else ""
    members = sorted(n for n in budgets_prev if n not in exclude and n not in NOT_PEOPLE)
    win = [shift_month(target, -k) for k in (3, 2, 1)]

    # ---- 全社の率（3か月）
    tot = collections.Counter()
    for (n, ym), c in monthly.items():
        if ym in win:
            tot.update(c)
    sides = tot["契約"] + tot["契約（アポ）"]
    c_price = tot["契約金額（万円）"] / sides if sides else 82.5
    c_cancel = tot["キャンセル金額（万円）"] / tot["契約金額（万円）"] if tot["契約金額（万円）"] else 0.4
    c_apo2con = tot["契約（アポ）"] / tot["アポ数"] if tot["アポ数"] else 0.05
    c_close = tot["契約"] / tot["有効商談数"] if tot["有効商談数"] else 0.3

    # ---- 個人予算の配分
    perf = {}
    for n in members:
        vals = [monthly[(n, ym)]["実質売上（万円）"] for ym in win if monthly[(n, ym)]["稼働日"] > 0 or monthly[(n, ym)]["契約金額（万円）"]]
        perf[n] = max(0.0, sum(vals) / len(vals)) if vals else 0.0
    b_sum, p_sum = sum(budgets_prev[n] for n in members), sum(perf.values())
    weight = {n: BUDGET_WEIGHT * budgets_prev[n] / b_sum + (PERF_WEIGHT * perf[n] / p_sum if p_sum else 0) for n in members}
    weight = {n: w ** args.smooth for n, w in weight.items()}   # 突出した人が出ないよう、差を少し縮める
    min_budget = args.min if args.min is not None else round(c_price, 1)   # アポ1件分（契約1件の折半）
    # 個別の指定: 「福本=85」固定／「今川=+10%」計算値から増減／「宮腰=岡野-10」他の人から差を付ける
    rules = {}
    for item in filter(None, (x.strip() for x in args.set.split(","))):
        k, v = item.split("=", 1)
        rules[k.strip()] = v.strip()
    base_alloc = {n: args.total * weight[n] / sum(weight.values()) for n in members}
    alloc = dict(base_alloc)
    at_min: set[str] = set()   # 一度最低額に張り付いた人は、そのまま最低額で固定する（行ったり来たりしないように）
    for _ in range(50):
        fixed = {}
        for n, v in rules.items():
            if n not in alloc:
                continue
            if v.endswith("%"):
                fixed[n] = base_alloc[n] * (1 + float(v[:-1]) / 100)
            elif any(o in v for o in members if o != n):
                other = next(o for o in members if o != n and v.startswith(o))
                fixed[n] = alloc[other] + float(v[len(other):] or 0)
            else:
                fixed[n] = float(v)
        at_min |= {n for n in members if n not in fixed and alloc[n] < min_budget - 1e-6}
        for n in at_min:
            if n not in fixed:
                fixed[n] = min_budget
        free = [n for n in members if n not in fixed]
        rest = args.total - sum(fixed.values())
        fw = sum(weight[n] for n in free)
        new_alloc = {**{n: rest * weight[n] / fw for n in free}, **fixed}
        if all(abs(new_alloc[n] - alloc[n]) < 0.01 for n in members):
            alloc = new_alloc
            break
        alloc = new_alloc
    alloc = {n: round(v, 1) for n, v in alloc.items()}
    drift = round(args.total - sum(alloc.values()), 1)
    if members and drift:
        top = max((n for n in members if n not in rules), key=lambda n: alloc[n])
        alloc[top] = round(alloc[top] + drift, 1)

    locked = {}
    if args.lock:   # 確定済みの予算・目標で固定する（MT前日に実績だけ最新にするとき）
        locked = {m["name"]: m for m in json.load(open(args.lock, encoding="utf-8"))["members"]}
        members = [n for n in members if n in locked] + [n for n in locked if n not in members and n in budgets_prev]
        alloc = {n: locked[n]["budget"] for n in members}

    # ---- 稼働日（会社の暦）
    t_days = company_days(t_first, t_last)
    out_members = []
    for n in members:
        as_closer = n in {x.strip() for x in args.as_closer.split(",") if x.strip()}
        is_closer = "クローザー" in role.get(n, "") or as_closer or bool(locked.get(n, {}).get("is_closer"))
        if as_closer and "クローザー" not in role.get(n, ""):
            role[n] = f"クローザー（{target[5:].lstrip('0')}月から）"
        start = max(p_first, first_seen.get(n, p_first))
        p_days = company_days(start, min(p_last, asof))
        exp_days, per_day = {}, {}
        for typ in ("heavy", "light"):
            c = daytype[(n, typ)]
            ratio = min(1.0, c["稼働日"] / p_days[typ]) if p_days[typ] else 0.0
            exp_days[typ] = ratio * t_days[typ]
            per_day[typ] = {f: (c[f] / c["稼働日"] if c["稼働日"] else 0.0) for f in FIELDS}
        days_total = exp_days["heavy"] + exp_days["light"]

        def target_of(f: str) -> float:
            return sum(per_day[t][f] * exp_days[t] for t in ("heavy", "light")) * GROWTH

        tgt = {"訪問数": round(target_of("訪問数")), "アポ数": round(target_of("アポ数"))}
        if is_closer:
            tgt["有効商談数"] = round(target_of("有効商談数"))
            tgt["契約数"] = max(1, round(target_of("契約")))

        # 本人の率（3か月、少ない件数は全社に寄せる）
        m3 = collections.Counter()
        for ym in win:
            m3.update(monthly[(n, ym)])
        n_sides = m3["契約"] + m3["契約（アポ）"]
        price = shrink(m3["契約金額（万円）"], n_sides, c_price, SHRINK_CONTRACT) if n_sides else c_price
        cancel = shrink(m3["キャンセル金額（万円）"] / max(price, 1), n_sides, c_cancel, SHRINK_CONTRACT) if n_sides else c_cancel
        cancel = min(max(cancel, 0.0), 0.95)
        apo2con = shrink(m3["契約（アポ）"], m3["アポ数"], c_apo2con, SHRINK_APO)
        close = shrink(m3["契約"], m3["有効商談数"], c_close, SHRINK_DEAL) if is_closer else 0.0
        rates = {
            "対面率": m3["対面数"] / m3["訪問数"] if m3["訪問数"] else None,
            "対話率": m3["対話数"] / m3["対面数"] if m3["対面数"] else None,
            "アポ率": m3["アポ数"] / m3["対話数"] if m3["対話数"] else None,
            "アポ→契約率": apo2con,
            "商談→契約率": close if is_closer else None,
            "残る率": 1 - cancel,
        }

        def sales(apo: float, deals: float, r: dict, price=price, is_closer=is_closer) -> float:
            appointer_side = apo * r["アポ→契約率"]
            closer_side = deals * (r["商談→契約率"] or 0) if is_closer else 0
            return (appointer_side + closer_side) * price * r["残る率"]

        if as_closer and tgt.get("有効商談数", 0) < 3:
            # クローザーデビューで商談の実績が無い人: 予算からアポ側の見込みを引いた分を、全社の契約率・残る率で逆算する
            need_sales = max(0.0, alloc[n] - tgt["アポ数"] * apo2con * price * (1 - cancel))
            tgt["契約数"] = max(1, round(need_sales / (price * (1 - c_cancel)))) if price else 1
            tgt["有効商談数"] = max(1, round(tgt["契約数"] / c_close)) if c_close else tgt["契約数"]
            rates["商談→契約率"] = c_close
        if n in locked:
            tgt = dict(locked[n]["targets"])
            if locked[n].get("is_closer") and rates.get("商談→契約率") is None:
                rates["商談→契約率"] = c_close
        deals_t = tgt.get("有効商談数", 0)
        proj = sales(tgt["アポ数"], deals_t, rates)
        out_members.append({
            "name": n, "role": role.get(n, ""), "agency": agency.get(n, ""), "is_closer": is_closer,
            "budget_prev": budgets_prev[n], "budget": alloc[n], "perf_avg3": round(perf[n], 1),
            "prev": {f: round(monthly[(n, prev)][f], 1) for f in FIELDS + ["稼働日"]},
            "current": {f: round(monthly[(n, target)][f], 1) for f in FIELDS + ["稼働日"]},   # 計画月の実績（asofまで）
            "prev_budget_achv": round(monthly[(n, prev)]["実質売上（万円）"] / budgets_prev[n], 3) if budgets_prev[n] else None,
            "days": {"heavy": round(exp_days["heavy"], 1), "light": round(exp_days["light"], 1), "total": round(days_total, 1)},
            "targets": tgt, "rates": {k: (round(v, 4) if v is not None else None) for k, v in rates.items()},
            "price": round(price, 1), "proj_sales": round(proj, 1), "gap": round(alloc[n] - proj, 1),
            "_calc": (tgt, rates, deals_t, sales),
        })

    # ---- できてる人の水準（同じ役割・件数が十分ある人の上位25%）と改善ポイント
    def bench(key: str, pool: list[dict], min_base) -> float | None:
        vals = sorted(m["rates"][key] for m in pool if m["rates"][key] is not None and min_base(m))
        if len(vals) < 3:
            return None
        return statistics.quantiles(vals, n=4)[2]

    base_ok = {
        "対面率": lambda m: m["prev"]["訪問数"] >= 150, "対話率": lambda m: m["prev"]["対面数"] >= 50,
        "アポ率": lambda m: m["prev"]["対話数"] >= 30, "アポ→契約率": lambda m: m["prev"]["アポ数"] >= 10,
        "商談→契約率": lambda m: m["prev"]["有効商談数"] >= 5, "残る率": lambda m: (m["prev"]["契約"] + m["prev"]["契約（アポ）"]) >= 2,
    }
    benches = {k: bench(k, out_members, f) for k, f in base_ok.items()}
    for m in out_members:
        tgt, rates, deals_t, sales = m.pop("_calc")
        base_sales = m["proj_sales"]
        levers = []
        for key, b in benches.items():
            cur = rates.get(key)
            if cur is None or b is None or cur >= b or (key == "商談→契約率" and not m["is_closer"]):
                continue
            apo = tgt["アポ数"]
            r2 = dict(rates)
            r2[key] = b
            if key in ("対面率", "対話率", "アポ率"):
                apo2 = apo * b / cur if cur else apo
                delta_apo = apo2 - apo
                new = sales(apo2, deals_t, rates)
            else:
                delta_apo = 0
                new = sales(apo, deals_t, r2)
            gain = new - base_sales
            if gain < 1:
                continue
            need = None   # この工程だけで予算に届かせるなら、率をいくつに
            if m["gap"] > 0:   # 売上は率に比例するので、差の分だけ率を動かす
                need = cur + (b - cur) * m["gap"] / gain
                need = need if need <= min(0.95, b * 1.6) else None
            levers.append({"key": key, "now": round(cur, 4), "bench": round(b, 4), "gain_sales": round(gain, 1),
                           "gain_apo": round(delta_apo, 1), "need_for_budget": round(need, 4) if need else None})
        levers.sort(key=lambda x: -x["gain_sales"])
        m["levers"] = levers[:3]
        # 率を変えずに件数で埋めるなら、あと何件（クローザーは有効商談、アポインターはアポ）
        if m["gap"] > 0:
            if m["is_closer"] and rates["商談→契約率"]:
                per = rates["商談→契約率"] * m["price"] * rates["残る率"]
                m["volume"] = {"key": "有効商談数", "add": round(m["gap"] / per, 1) if per else None}
            else:
                per = rates["アポ→契約率"] * m["price"] * rates["残る率"]
                m["volume"] = {"key": "アポ数", "add": round(m["gap"] / per, 1) if per else None}
        else:
            m["volume"] = None

    result = {"month": target, "prev": prev, "asof": asof.isoformat(), "total": args.total,
              "company": {"price": round(c_price, 1), "cancel": round(c_cancel, 3), "apo2con": round(c_apo2con, 4),
                          "close": round(c_close, 3), "days": t_days},
              "benchmarks": {k: (round(v, 4) if v else None) for k, v in benches.items()},
              "excluded": sorted(exclude), "members": out_members}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    if args.publish:   # line-webhook/api/webhook.py の AP_PLAN_PATH と同じ場所
        import urllib.request
        url = os.environ["ELP_SUPABASE_URL"].rstrip("/") + f"/storage/v1/object/deal-recordings/plans/{target}.json"
        key = os.environ["ELP_SUPABASE_SERVICE_ROLE_KEY"]
        req = urllib.request.Request(url, data=json.dumps(result, ensure_ascii=False).encode(), method="POST",
                                     headers={"apikey": key, "Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json", "x-upsert": "true"})
        urllib.request.urlopen(req, timeout=30).close()
        print(f"計画JSONを公開しました（Bot用・非公開バケット）: plans/{target}.json")

    print(f"{target}の計画（実績は{asof}まで）／全社予算{args.total:.0f}万 → 配分合計{sum(alloc.values()):.1f}万")
    print(f"翌月の会社の稼働日: 土日祝{t_days['heavy']}日・平日{t_days['light']}日／全社: 契約1件あたり{c_price:.1f}万・"
          f"キャンセル率{pct(c_cancel)}・アポ→契約{pct(c_apo2con)}・商談→契約{pct(c_close)}")
    print("できてる人の水準:", "／".join(f"{k}{pct(v)}" for k, v in benches.items() if v))
    for m in sorted(out_members, key=lambda x: -x["budget"]):
        t = m["targets"]
        print(f"\n■{m['name']}（{m['role']}・{m['agency']}）予算 {m['budget_prev']:.1f}→{m['budget']:.1f}万"
              f"（前月実質{m['prev']['実質売上（万円）']:.1f}万・3か月平均{m['perf_avg3']:.1f}万）")
        print(f"  稼働見込み{m['days']['total']}日（土日祝{m['days']['heavy']}・平日{m['days']['light']}）／目標: "
              + "・".join(f"{k}{v}" for k, v in t.items())
              + f"／この目標での売上見込み{m['proj_sales']:.1f}万（予算との差{m['gap']:+.1f}万）")
        if m["volume"] and m["volume"]["add"]:
            print(f"  ・率を変えずに件数で埋めるなら: {m['volume']['key']} あと+{m['volume']['add']:.1f}件")
        for lv in m["levers"]:
            need = f"、これ1つで予算に届かせるなら{pct(lv['need_for_budget'])}" if lv["need_for_budget"] else ""
            apo = f"アポ+{lv['gain_apo']:.1f}件→" if lv["gain_apo"] else ""
            print(f"  ・{lv['key']} {pct(lv['now'])}→{pct(lv['bench'])}: {apo}実質売上+{lv['gain_sales']:.1f}万{need}")

    if args.write_budget:
        existing = {prop_text(b, "メンバー"): b for b in query_all(
            f"/data_sources/{BUDGET_DB}/query", {"property": "年月（YYYY-MM）", "rich_text": {"equals": target}}, NOTION_VERSION_V2)}
        for n in members:
            if n in existing:
                notion("PATCH", f"/pages/{existing[n]['id']}", {"properties": {"予算（万円）": {"number": alloc[n]}}}, NOTION_VERSION_V2)
                print("更新:", n, alloc[n])
            else:
                props = {"タイトル": {"title": [{"text": {"content": f"{n}_{target}"}}]},
                         "メンバー": {"select": {"name": n}},
                         "予算（万円）": {"number": alloc[n]},
                         "年月（YYYY-MM）": {"rich_text": [{"text": {"content": target}}]}}
                if agency.get(n):
                    props["代理店"] = {"select": {"name": agency[n]}}
                notion("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": BUDGET_DB},
                                          "properties": props}, NOTION_VERSION_V2)
                print("追加:", n, alloc[n])
    if args.write_kpi:
        by_name = {m["name"]: m for m in out_members}
        for p in query_all(f"/databases/{KPI_DB}/query", None, NOTION_VERSION_V1):
            m = by_name.get(prop_text(p, "メンバー"))
            if not m:
                continue
            props = {"今月予算万円": {"number": m["budget"]}}
            if prop_text(p, "役割") == "クローザー":
                props["今月契約目標"] = {"number": m["targets"].get("契約数", 1)}
            else:
                props["今月アポ目標概算"] = {"number": m["targets"]["アポ数"]}
            notion("PATCH", f"/pages/{p['id']}", {"properties": props}, NOTION_VERSION_V1)
            print("KPI:", m["name"], props)


if __name__ == "__main__":
    main()
