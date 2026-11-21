"""今川さん宛ての「質問・確認」を送ってよい時間帯の判定（今川さん指示 2026-11-11／2026-11-13 改定）。

- 回答待ちの質問・確認（困ったメモのまとめ・施工案件の更新まとめ・見落としチェック）は、毎日 0:00 JST にまとめて送る。
  Routineの起動が数分遅れることがあるため、0:30 までは送ってよい。0:30〜6:00 は送らない（次の0:00に回す）。
- 0:00 に送るときの「その日」は、今日ではなく、いま終わった日（前日）として数える（business_date）。
- 今川さんが深夜に答えたぶんの返信は、朝 6:00 JST 以降に返す（scripts/flush_deferred.py が回す）。
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
SEND_UNTIL = dt.time(0, 30)   # 0:00 の定時送信の遅れを許す上限。これ以降は送らない
OPEN = dt.time(6, 0)          # この時刻になるまで送らない


def in_quiet_hours(now: dt.datetime | None = None) -> bool:
    t = (now or dt.datetime.now(JST)).astimezone(JST).time()
    return SEND_UNTIL <= t < OPEN


def business_date(now: dt.datetime | None = None) -> dt.date:
    """0:00〜6:00 に動くときは、いま終わった日（前日）を「その日」とする。"""
    n = (now or dt.datetime.now(JST)).astimezone(JST)
    return (n - dt.timedelta(days=1)).date() if n.time() < OPEN else n.date()
