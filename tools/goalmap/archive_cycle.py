#!/usr/bin/env python3
"""周回アーカイブ — その周回の最終ゴールマップを履歴として残す。

メンバーjsonの「いまの状態」を out/archive/<名前>_cycle<N>.{json,svg,png} に保存する。
ゴール達成→次のゴールへ進む前（タスクをリセットする前）に実行すると、
過去の達成地図をいつでも見返せる。N は json の `cycle`（何周目）。

    python tools/goalmap/archive_cycle.py tools/goalmap/members/岡野.json

studio(goalmap-studio.html) の「🎉 ゴール達成 → 次のゴールへ」ボタンは
ブラウザからこれと同じ2ファイル（PNG/JSON）を書き出す。手元に落ちたものを
out/archive/ に移してコミットすれば、リポジトリ＝履歴になる。
"""
import json
import sys
from pathlib import Path

import generate_goalmap as G


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        return 1
    src = Path(args[0])
    data = G.load_member_data(str(src))
    cycle = max(1, int(data.get("cycle", 1) or 1))
    name = data.get("name", "member")

    archive_dir = Path(__file__).parent / "out" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    base = archive_dir / f"{name}_cycle{cycle}"

    # データのスナップショット
    base.with_suffix(".json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {base.with_suffix('.json')}")

    # 図解（svg + png）。generate_goalmap.main を -o で再利用
    G.main(["generate_goalmap.py", str(src), "-o", str(base)])
    print(f"archived: {name} cycle{cycle} → {base}.{{json,svg,png}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
