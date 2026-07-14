#!/usr/bin/env python3
"""週次ゴールマップ更新：ワンコマンドで全成果物を再生成する。

MT後に `members/*.json`（studioで「JSON保存」した“正”データ）を更新したら、
このスクリプト1本で週次の共有物をまとめて作り直す。非エンジニアでも
`python3 tools/goalmap/weekly_update.py` だけで回せることを目的にしている。

実行される処理（各ステップは独立・失敗しても止まらず最後にまとめて報告）:
    1. 🐉 成長アイコン一覧      koi_dragon_icons.py --sheet
    2. 🗺️ 個人ゴールマップ（全員） generate_goalmap.py members/*.json → out/<名前>.png
    3. 📱 1枚カード（全員）       summary_cards.py            → out/summary/
    4. 📊 チーム達成率バー        team_chart.py              → out/チーム達成率.png
    5. 🖥️ 週次スライド(任意)      build_weekly_deck.py       → out/週次.pptx（python-pptx必要）
    6. 🏢 組織ロードマップ同期(任意) roadmap/sync_notion_to_json.py（NOTION_TOKENがある時のみ）

使い方:
    python3 tools/goalmap/weekly_update.py            # 1〜6を実行（6はTOKEN無ければスキップ）
    python3 tools/goalmap/weekly_update.py --commit   # 生成物をgit add＋コミットまで
    python3 tools/goalmap/weekly_update.py --no-notion # 組織マップ同期をスキップ
    python3 tools/goalmap/weekly_update.py --no-deck   # スライド生成をスキップ

デプロイ（Vercel本番反映）はこのスクリプトには含めない。--commit で
push まで済ませ、Vercelの自動デプロイに任せるか、手動で `vercel --prod` する。
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # tools/goalmap
REPO = HERE.parents[1]                           # リポジトリルート
PY = sys.executable or "python3"


def members() -> list[Path]:
    return sorted(p for p in HERE.glob("members/*.json") if "_template" not in p.name)


def run(label: str, cmd: list[str], cwd: Path = HERE) -> tuple[str, str]:
    """1ステップ実行。(status, detail) を返す。status ∈ {ok, fail, skip}。"""
    print(f"\n▶ {label}")
    print("  $", " ".join(str(c) for c in cmd))
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    except FileNotFoundError as e:
        print(f"  ⚠ 実行できません: {e}")
        return "fail", f"{label}: {e}"
    dt = time.time() - t0
    tail = (r.stdout or "").strip().splitlines()[-3:]
    for line in tail:
        print("  ", line)
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()[-4:]
        for line in err:
            print("  ✗", line)
        return "fail", f"{label}: exit {r.returncode}"
    print(f"  ✓ 完了（{dt:.1f}s）")
    return "ok", label


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true", help="生成物をgit add＋コミット")
    ap.add_argument("--push", action="store_true", help="--commit後にpushまで行う")
    ap.add_argument("--no-notion", action="store_true", help="組織ロードマップ同期をスキップ")
    ap.add_argument("--no-deck", action="store_true", help="週次スライド(pptx)生成をスキップ")
    args = ap.parse_args(argv)

    ms = members()
    if not ms:
        print("✗ members/*.json が見つかりません。", file=sys.stderr)
        return 1
    print(f"対象メンバー {len(ms)}名: " + "、".join(p.stem for p in ms))

    results: list[tuple[str, str]] = []

    # 1. 成長アイコン一覧
    results.append(run("🐉 成長アイコン一覧", [PY, "koi_dragon_icons.py", "--sheet"]))

    # 2. 個人ゴールマップ（全員・大きい地図）
    ok = 0
    for p in ms:
        st, _ = run(f"🗺️ ゴールマップ {p.stem}", [PY, "generate_goalmap.py", str(p.relative_to(HERE))])
        ok += (st == "ok")
    results.append(("ok" if ok == len(ms) else "fail",
                    f"🗺️ 個人ゴールマップ {ok}/{len(ms)}名"))

    # 3. 1枚カード（全員）
    results.append(run("📱 1枚カード（全員）", [PY, "summary_cards.py"]))

    # 4. チーム達成率バー
    results.append(run("📊 チーム達成率バー", [PY, "team_chart.py"]))

    # 5. 週次スライド（任意・python-pptx必要）
    if args.no_deck:
        results.append(("skip", "🖥️ 週次スライド（--no-deck）"))
    else:
        st, det = run("🖥️ 週次スライド(pptx)", [PY, "build_weekly_deck.py"])
        if st == "fail":
            print("  ℹ python-pptx未導入なら `pip install python-pptx`。スキップ扱いにします。")
            st = "skip"
        results.append((st, "🖥️ 週次スライド"))

    # 6. 組織ロードマップ同期（NOTION_TOKENがある時のみ）
    if args.no_notion:
        results.append(("skip", "🏢 組織ロードマップ同期（--no-notion）"))
    elif not os.environ.get("NOTION_TOKEN"):
        print("\nℹ NOTION_TOKEN 未設定のため組織ロードマップ同期はスキップ。")
        print("  同期したい時: export NOTION_TOKEN=... して再実行（値はリポジトリに書かない）。")
        results.append(("skip", "🏢 組織ロードマップ同期（NOTION_TOKEN無し）"))
    else:
        results.append(run("🏢 組織ロードマップ同期",
                           [PY, "sync_notion_to_json.py"], cwd=HERE.parent / "roadmap"))

    # ── まとめ ──
    print("\n" + "=" * 44)
    print("週次更新サマリー")
    print("=" * 44)
    mark = {"ok": "✓", "fail": "✗", "skip": "－"}
    for st, label in results:
        print(f"  {mark.get(st,'?')} {label}")
    failed = [l for s, l in results if s == "fail"]

    # git 差分の可視化
    diff = subprocess.run(["git", "status", "--short", "tools/goalmap/out",
                           "tools/roadmap/app/public/data"],
                          cwd=str(REPO), capture_output=True, text=True)
    changed = [l for l in (diff.stdout or "").splitlines() if l.strip()]
    print(f"\n変更ファイル: {len(changed)}件")
    for l in changed[:12]:
        print("  ", l)
    if len(changed) > 12:
        print(f"   … 他 {len(changed)-12}件")

    if args.commit and changed:
        subprocess.run(["git", "add", "tools/goalmap/out",
                        "tools/roadmap/app/public/data", "tools/roadmap/app/src/data"],
                       cwd=str(REPO))
        msg = f"chore(goalmap): 週次更新（{len(ms)}名分の図解・カード再生成）"
        c = subprocess.run(["git", "commit", "-m", msg], cwd=str(REPO),
                           capture_output=True, text=True)
        print("\n" + (c.stdout or c.stderr).strip().splitlines()[0] if (c.stdout or c.stderr) else "")
        if args.push and c.returncode == 0:
            subprocess.run(["git", "push"], cwd=str(REPO))
    elif args.commit:
        print("\nℹ 変更なし。コミットしません。")

    if failed:
        print(f"\n⚠ 失敗ステップ: {len(failed)}件（上のログ参照）")
        return 1
    print("\n✅ 週次更新おわり。out/ の画像をLINE/Notion/朝礼で共有してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
