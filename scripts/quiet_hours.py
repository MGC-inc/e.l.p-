"""今川さん宛ての「質問・確認」を送ってよい時間帯の判定（2026-11-11 今川さん指示）。

- 回答待ちの質問は、遅くとも 23:30 JST までに送る。23:30〜翌6:00 は送らない。
- 今川さんが深夜に答えたぶんの返信は、朝 6:00 JST 以降に返す（scripts/flush_deferred.py が回す）。
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
CUTOFF = dt.time(23, 30)   # これ以降は送らない
OPEN = dt.time(6, 0)       # この時刻になるまで送らない


def in_quiet_hours(now: dt.datetime | None = None) -> bool:
    t = (now or dt.datetime.now(JST)).astimezone(JST).time()
    return t >= CUTOFF or t < OPEN
