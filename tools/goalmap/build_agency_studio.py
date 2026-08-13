#!/usr/bin/env python3
"""代理店ごとにEMBEDDEDを絞り込んだ個人スタジオを生成する。

tools/goalmap/goalmap-studio.html（本体・全メンバー内蔵・オフライン配布用）は
一切変更しない。代わりに、tools/roadmap/sync_notion_to_json.py が生成した
tools/roadmap/app/public/data/roadmap-<token>.json（代理店ごとの部分木）を読み、
そのメンバーだけをEMBEDDEDに含めたコピーを
tools/roadmap/app/public/studio/<token>.html として書き出す。

これにより、代理店の専用URL（/studio/<token>.html?member=<名前>）経由では、
そのHTMLファイル自体に他代理店のメンバーデータが一切含まれない
（クライアント側フィルタではなく、配信データ自体の分離）。

使い方:
    python3 tools/goalmap/build_agency_studio.py
    （事前に tools/roadmap/sync_notion_to_json.py で roadmap-*.json を生成しておくこと）
"""
from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GOALMAP_DIR = REPO_ROOT / "tools/goalmap"
MEMBERS_DIR = GOALMAP_DIR / "members"
STUDIO_MASTER = GOALMAP_DIR / "goalmap-studio.html"
DATA_DIR = REPO_ROOT / "tools/roadmap/app/public/data"
STUDIO_OUT_DIR = REPO_ROOT / "tools/roadmap/app/public/studio"

EMBEDDED_RE = re.compile(r"const EMBEDDED = \[.*?\n\];", re.S)


def collect_member_names(subtree: dict, out: list[str]) -> list[str]:
    if subtree.get("level") == 3:
        out.append(subtree.get("owner") or subtree.get("title"))
    for child in subtree.get("children", []):
        collect_member_names(child, out)
    return out


def load_member_files_for(names: set[str]) -> list[dict]:
    """members/*.json のうち、氏名（「〇〇（育成）」等のバリエーション込み）が
    names に含まれるものだけを、ファイル名順で読み込む。"""
    picked: list[dict] = []
    for member_file in sorted(MEMBERS_DIR.glob("*.json")):
        stem = member_file.stem
        if stem.startswith("_"):
            continue
        base = stem.split("（")[0]
        if base in names:
            picked.append(json.loads(member_file.read_text(encoding="utf-8")))
    return picked


def build_embedded_block(members: list[dict]) -> str:
    lines = ["  " + json.dumps(m, ensure_ascii=False) for m in members]
    return "const EMBEDDED = [\n" + ",\n".join(lines) + "\n];"


def main() -> None:
    if not STUDIO_MASTER.exists():
        raise SystemExit(f"見つかりません: {STUDIO_MASTER}")

    agency_files = sorted(glob.glob(str(DATA_DIR / "roadmap-*.json")))
    if not agency_files:
        raise SystemExit(
            f"{DATA_DIR} に roadmap-<token>.json がありません。"
            "先に tools/roadmap/sync_notion_to_json.py を実行してください。"
        )

    master_html = STUDIO_MASTER.read_text(encoding="utf-8")
    STUDIO_OUT_DIR.mkdir(parents=True, exist_ok=True)

    for agency_file in agency_files:
        token = Path(agency_file).stem.removeprefix("roadmap-")
        subtree = json.loads(Path(agency_file).read_text(encoding="utf-8"))
        names = set(collect_member_names(subtree, []))
        members = load_member_files_for(names)
        if not members:
            print(f"skip {token}（{subtree.get('title')}）: 該当メンバーのJSONが見つかりません")
            continue

        block = build_embedded_block(members)
        new_html = EMBEDDED_RE.sub(block, master_html, count=1)
        if new_html == master_html:
            raise SystemExit(
                "EMBEDDED置換が発生しませんでした。goalmap-studio.htmlの定義書式を確認してください。"
            )

        out_path = STUDIO_OUT_DIR / f"{token}.html"
        out_path.write_text(new_html, encoding="utf-8")
        print(f"wrote {out_path}（{subtree.get('title')}: {[m['name'] for m in members]}）")


if __name__ == "__main__":
    main()
