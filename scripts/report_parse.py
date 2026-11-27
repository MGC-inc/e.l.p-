#!/usr/bin/env python3
"""アポ報告グループ・商談報告グループのLINE発言を読み取る部品。2026-11-22 今川さん指示。

・LINEの表示名 → 実績DBの登録名（名簿DBの「備考・表記注意」に合わせた変換表）
・アポ報告：「〇〇邸💧N」の行（Nはその人のその日の累計アポ数。「N/4」は目標4件中のN件目）と、1日の終わりの日報
  （訪問数・在宅数・対面数・対象者・対話数・アポ数と、反省文）
・声かけ（「ナイス」「頑張れ」など）やスタンプは集計の対象外。💧の行と日報の数字だけを拾う
"""
from __future__ import annotations

import datetime as dt
import re

JST = dt.timezone(dt.timedelta(hours=9))

# 集計の対象外（声かけだけの人・役員・特例）。名簿DBの備考に従う
SKIP_KEYS = ("鰐部", "山田海大", "kanata", "兼松", "野間", "松田康平", "宮下")
# 表示名に含まれる文字 → 実績DBの登録名（先に書いたものが優先）
NAME_RULES = [
    ("福本よしき", "福本豊樹"), ("福本凌", "福本"), ("虎春", "鈴木虎春"), ("虎之介", "三浦"),
    ("宮腰", "宮腰"), ("林陽太", "林"), ("田村征大", "田村"), ("巻田", "巻田"), ("依田", "依田"),
    ("岡野", "岡野"), ("戸田", "戸田"), ("山口夕貴", "山口"), ("安達", "安達"), ("男沢", "男沢"),
    ("村山", "村山"), ("平井", "平井"), ("村田旭", "村田旭"), ("あさひ", "村田旭"), ("今川", "今川"),
    ("門田", "門田"), ("柚木", "柚木"), ("藤井", "藤井"), ("齊藤", "齊藤"), ("名取", "名取"),
]
_INVISIBLE = re.compile(r"[​‪-‮️]")


def norm(s: str) -> str:
    return re.sub(r"[\s　]+", "", _INVISIBLE.sub("", s or ""))


def member(display: str) -> tuple[str | None, str]:
    """(登録名, 状態)。状態: ok＝登録名が分かる／skip＝集計対象外（声かけだけの人など）／unknown＝変換表にない"""
    n = norm(display)
    if any(k in n for k in SKIP_KEYS):
        return None, "skip"
    for key, name in NAME_RULES:
        if key in n:
            return name, "ok"
    return None, "unknown"


APO_LINE = re.compile(r"^\s*(.{1,20}?)\s*💧\s*(\d+)(?:\s*/\s*(\d+))?\s*(.*)$")
_NUM_KEYS = [("訪問数", r"訪問数"), ("在宅数", r"在宅数"), ("対面数", r"対面数"), ("対象数", r"対象(?:者数|者|数)"),
             ("対話数", r"対話数"), ("アポ数", r"アポ数")]


def hhmm(ts_ms) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, JST).strftime("%H:%M") if ts_ms else ""


def jst_date(ts_ms) -> dt.date:
    return dt.datetime.fromtimestamp(ts_ms / 1000, JST).date()


def parse_nippo(text: str) -> dict | None:
    """1日の終わりの日報。数字が6つのうち訪問数とアポ数があれば日報とみなす。"""
    t = _INVISIBLE.sub("", text)
    if "訪問数" not in t or "アポ数" not in t:
        return None
    nums, end = {}, 0
    for key, pat in _NUM_KEYS:
        m = re.search(pat + r"\s*[:：]?\s*(\d+)", t)
        if m:
            nums[key] = int(m.group(1))
            end = max(end, m.end())
    reflect = t[end:].strip()
    return {"nums": nums, "reflect": reflect}


def parse_apo_reports(msgs: list[dict]) -> dict:
    """発言（{name,text,ts}）→ 人ごと・日ごとのアポ報告。戻り値:
    {(登録名, 日付): {"lines": [(時刻, お客様, N, 目標)], "max": 最大N, "nippo": {...}|None, "reflect": 反省文}}
    変換表にない名前は、キー「?表示名」で入れる（確認に回す）。"""
    out: dict = {}
    for m in sorted(msgs, key=lambda x: x.get("ts") or 0):
        name, st = member(m.get("name", ""))
        if st == "skip":
            continue
        key = (name or "?" + norm(m.get("name", "")), jst_date(m["ts"]) if m.get("ts") else None)
        rec = out.setdefault(key, {"lines": [], "max": 0, "nippo": None, "reflect": ""})
        text = _INVISIBLE.sub("", m.get("text") or "")
        nip = parse_nippo(text)
        if nip:
            rec["nippo"], rec["reflect"] = nip["nums"], nip["reflect"]
            continue
        for line in text.splitlines():
            mm = APO_LINE.match(line)
            if mm:
                n = int(mm.group(2))
                rec["lines"].append((hhmm(m.get("ts")), mm.group(1).strip(), n, int(mm.group(3)) if mm.group(3) else None))
                rec["max"] = max(rec["max"], n)
    return {k: v for k, v in out.items() if v["lines"] or v["nippo"]}
