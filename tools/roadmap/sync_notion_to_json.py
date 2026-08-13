#!/usr/bin/env python3
"""Notion「🏢 組織ロードマップ」「🧭 目標マップ」→ roadmap.json 生成バッチ

tools/goalmap/generate_goalmap.py と同じ立て付け（標準ライブラリのみ・CLIから実行）。
Notion REST API を直接叩くため、事前にこのリポジトリの2DBを読めるNotion
インテグレーションを作成し、トークンを環境変数 NOTION_TOKEN に入れて実行する。

セットアップ（初回のみ・Notion UI操作）:
    1. https://www.notion.so/my-integrations で Internal Integration を作成
    2. 「🏢 組織ロードマップ」「🧭 目標マップ」の2DBそれぞれで
       「•••」→ Connections → 作成したインテグレーションを追加（共有）
    3. 発行された Internal Integration Secret を NOTION_TOKEN に設定
       （このリポジトリにトークンの値を書かない。シェル環境変数か .env
       （.gitignore済み）等、ローカルにのみ保持する）

使い方:
    export NOTION_TOKEN=secret_xxx
    python3 tools/roadmap/sync_notion_to_json.py

    # 出力先を変えたい/サンプルへのコピーを止めたい場合
    python3 tools/roadmap/sync_notion_to_json.py --output path/to/roadmap.json --no-sample

データモデル: tools/roadmap/app/src/types/roadmap.ts の RoadmapNode に対応。
    Level0-2（会社/組織/チーム）＝🏢組織ロードマップDB（自己リレーション「親」でツリー化）
    Level3（個人）＝🧭目標マップDBの各行。「所属チーム」リレーションで
    Level2チームの子として接続する（1メンバーが複数チームに属してもよい＝兼務）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

# 「🏢 組織ロードマップ」「🧭 目標マップ」の database_id（固定値・秘密情報ではない）
ORG_DB_ID = "d6bd4fd0-7d41-4c2e-9862-ef43527dbe45"
GOAL_DB_ID = "4705d6f7-074a-4305-9d17-52744992420e"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "tools/roadmap/app/public/data/roadmap.json"
SAMPLE_OUTPUT = REPO_ROOT / "tools/roadmap/app/src/data/roadmap.sample.json"

LEVEL_BY_NAME = {"会社": 0, "組織": 1, "チーム": 2}
STATUS_MAP = {
    "未着手": "not_started",
    "進行中": "in_progress",
    "完了": "done",
    "リスクあり": "at_risk",
}

# 成長ステージ（鯉→龍・Lv0-9）。studio/generate_goalmap.py の FORM_NAMES と同一。
GROWTH_FORMS = ["たまご", "針子", "稚鯉", "若鯉", "錦鯉",
                "大錦鯉", "滝登り", "化龍", "青龍", "金龍"]


def member_growth_level(members_dir: Path, slug: str) -> int | None:
    """members/<slug>.json の level（＝studioが正）を読む。無ければNone。

    組織マップに出す個人の成長レベルは、Notionの成長ステージ列ではなく
    studioの正データ（members/*.json）を単一情報源にして、PNGアバターと必ず一致させる。
    """
    f = members_dir / f"{slug}.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    v = d.get("level", d.get("startForm"))
    if v is None:
        return None
    try:
        return max(0, min(9, int(float(v))))
    except (TypeError, ValueError):
        return None


def notion_query(database_id: str, token: str) -> list[dict]:
    """データベースの全ページを取得（ページネーション対応）。"""
    results: list[dict] = []
    cursor: str | None = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"{NOTION_API}/databases/{database_id}/query",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise SystemExit(
                f"Notion APIエラー（{database_id}）: {e.code} {detail}"
            ) from e
        results.extend(payload.get("results", []))
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
    return results


def _prop(page: dict, name: str) -> dict | None:
    return page.get("properties", {}).get(name)


def prop_title(page: dict, name: str) -> str:
    p = _prop(page, name)
    if not p:
        return ""
    return "".join(t.get("plain_text", "") for t in p.get("title", []))


def prop_text(page: dict, name: str) -> str:
    p = _prop(page, name)
    if not p:
        return ""
    return "".join(t.get("plain_text", "") for t in p.get("rich_text", []))


def prop_select(page: dict, name: str) -> str | None:
    p = _prop(page, name)
    if not p:
        return None
    v = p.get("select")
    return v.get("name") if v else None


def prop_status(page: dict, name: str) -> str | None:
    p = _prop(page, name)
    if not p:
        return None
    v = p.get("status")
    return v.get("name") if v else None


def prop_number(page: dict, name: str) -> float | None:
    p = _prop(page, name)
    if not p:
        return None
    return p.get("number")


def prop_date(page: dict, name: str) -> str | None:
    p = _prop(page, name)
    if not p:
        return None
    v = p.get("date")
    return v.get("start") if v else None


def prop_relation_ids(page: dict, name: str) -> list[str]:
    p = _prop(page, name)
    if not p:
        return []
    return [r["id"] for r in p.get("relation", [])]


def prop_rollup_number(page: dict, name: str) -> float | None:
    p = _prop(page, name)
    if not p:
        return None
    rollup = p.get("rollup") or {}
    if rollup.get("type") != "number":
        return None
    value = rollup.get("number")
    if value is None:
        return None
    # 「完了の割合」ロールアップは 0..1 で返るため 0..100 に揃える
    return round(value * 100, 1) if value <= 1 else round(value, 1)


def build_org_tree(org_pages: list[dict]) -> dict:
    """🏢組織ロードマップDBの全行から、「親」リレーションでツリーを組む。"""
    nodes: dict[str, dict] = {}
    for page in org_pages:
        pid = page["id"]
        node = {
            "id": pid,
            "level": LEVEL_BY_NAME.get(prop_select(page, "レベル"), 1),
            "title": prop_title(page, "名称"),
            "children": [],
        }
        desc = prop_text(page, "説明")
        if desc:
            node["description"] = desc
        owner = prop_select(page, "責任者")
        if owner:
            node["owner"] = owner
        deadline = prop_date(page, "期限")
        if deadline:
            node["deadline"] = deadline
        status = prop_select(page, "ステータス")
        if status:
            node["status"] = STATUS_MAP.get(status, "not_started")
        progress = prop_number(page, "進捗率")
        if progress is not None:
            node["progress"] = progress
        nodes[pid] = node

    parent_of: dict[str, str] = {}
    for page in org_pages:
        parents = prop_relation_ids(page, "親")
        if parents:
            parent_of[page["id"]] = parents[0]

    roots = []
    for pid, node in nodes.items():
        parent_id = parent_of.get(pid)
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)

    if len(roots) != 1:
        names = ", ".join(r["title"] for r in roots)
        raise SystemExit(
            f"レベル0（会社）のルートが1件ではありません（{len(roots)}件: {names}）。"
            "🏢組織ロードマップDBの「親」設定を確認してください。"
        )
    return roots[0]


def attach_members(company: dict, goal_pages: list[dict]) -> None:
    """🧭目標マップDBの各行を、「所属チーム」で紐づくノードの子として追加する。

    「所属チーム」はLevel2（チーム）だけでなく、代理店チームを持たない
    マネジメント担当者（今川など）をLevel1（組織）へ直接ぶら下げる場合も
    あるため、Level0〜2のどのノードでも接続先になれる。

    1メンバーが同じ接続先に複数の目標行（テーマ）を持っていても、個人の
    ゴールマップ（goalmap-studio.html）は原則1人1枚なので、接続先ごとに
    メンバー名で重複除去し、Level3リーフは1人1枚にする（最初に見つかった
    行のテーマ・ステータスを採用）。ただし members/ 配下に「〇〇（育成）」
    のような本人名のバリエーションファイルが複数存在する場合は、実在する
    ファイル名の分だけリーフを分けて割り当てる（現状は該当メンバーなし。
    1人が並行して2つの実ゴールを持つ場合の拡張余地として残す）。
    """
    team_index: dict[str, dict] = {}

    def index_teams(node: dict) -> None:
        if node["level"] in (0, 1, 2):
            team_index[node["id"]] = node
        for child in node.get("children", []):
            index_teams(child)

    index_teams(company)

    members_dir = REPO_ROOT / "tools/goalmap/members"
    slug_variants: dict[str, list[str]] = {}
    for member_file in sorted(members_dir.glob("*.json")):
        stem = member_file.stem
        if stem.startswith("_"):
            continue
        base = stem.split("（")[0]
        slug_variants.setdefault(base, []).append(stem)

    # (team_id, member) -> 割り当て済みのバリエーション数（次に使うslugを選ぶため）
    assigned_count: dict[tuple[str, str], int] = {}

    for page in goal_pages:
        member = prop_select(page, "メンバー")
        if not member:
            continue  # メンバー未設定のサンプル行等はスキップ
        team_ids = prop_relation_ids(page, "所属チーム")
        if not team_ids:
            continue  # 所属チーム未設定はロードマップ側に出さない

        status = prop_status(page, "ステータス")
        theme = prop_title(page, "テーマ")
        progress = prop_rollup_number(page, "進捗率")

        for team_id in team_ids:
            team = team_index.get(team_id)
            if team is None:
                continue
            variants = slug_variants.get(member, [member])
            key = (team_id, member)
            idx = assigned_count.get(key, 0)
            if idx >= len(variants):
                continue  # このメンバーの実ファイル数を超える行はこのチームでは無視
            assigned_count[key] = idx + 1
            slug = variants[idx]

            leaf = {
                "id": f"{page['id']}:{team_id}",
                "level": 3,
                "title": slug,
                "owner": member,
                "status": STATUS_MAP.get(status, "not_started"),
                "externalLink": f"/goalmap-studio.html?member={quote(slug)}",
            }
            if theme:
                leaf["description"] = theme
            if progress is not None:
                leaf["progress"] = progress
            lv = member_growth_level(members_dir, slug)
            if lv is not None:
                leaf["growthLevel"] = lv
                leaf["growthForm"] = GROWTH_FORMS[lv]
            team.setdefault("children", []).append(leaf)


def find_agency_nodes(node: dict) -> list[dict]:
    """代理店（アクセス制御の境界）ノードを見つける。

    Level2（チーム＝代理店。例：ピタサチ／wanny）は無条件に境界。
    Level1は、Level2の子を持たず（＝チーム層を経ない代理店構成）かつ
    Level3（メンバー）を直接子に持つ場合のみ境界として扱う（将来、
    施工部・クリーニング事業部にチーム層を作らずメンバーを直接
    ぶら下げるケースの拡張余地）。それ以外のLevel1（例：現状の
    営業部＝Level2の親）は境界にせず、配下を掘り進める。
    """
    found: list[dict] = []

    def walk(n: dict) -> None:
        if n["level"] == 2:
            found.append(n)
            return
        if n["level"] == 1:
            children = n.get("children", [])
            has_level2_child = any(c["level"] == 2 for c in children)
            has_direct_member = any(c["level"] == 3 for c in children)
            if not has_level2_child and has_direct_member:
                found.append(n)
                return
        for c in n.get("children", []):
            walk(c)

    walk(node)
    return found


def _rewrite_member_links(node: dict, token: str) -> None:
    """代理店スコープの部分木内で、個人リンクを代理店専用studioへ向け直す。"""
    if node["level"] == 3 and "externalLink" in node:
        link = node["externalLink"]
        prefix = "/goalmap-studio.html?"
        if link.startswith(prefix):
            node["externalLink"] = f"/studio/{quote(token)}.html?{link[len(prefix):]}"
    for c in node.get("children", []):
        _rewrite_member_links(c, token)


def slice_agency_subtrees(company: dict) -> list[tuple[str, dict]]:
    """代理店境界ノードごとに、その配下だけを含む部分木を作る。

    各部分木はその境界ノード自身を新しいrootとする（＝「自社チームの
    配下だけ」を見せる。組織全体のシェルは見せない）。トークンは
    新規発行せず、境界ノードの既存id（Notion UUID・十分に推測不能）を
    そのまま流用する。
    """
    subtrees: list[tuple[str, dict]] = []
    for boundary in find_agency_nodes(company):
        sub = json.loads(json.dumps(boundary, ensure_ascii=False))  # deep copy
        token = boundary["id"]
        _rewrite_member_links(sub, token)
        subtrees.append((token, sub))
    return subtrees


def rollup_progress(node: dict) -> float | None:
    """子の進捗率の単純平均を、未設定ノードのprogressとして埋める（下から上へ）。"""
    children = node.get("children", [])
    child_values = [
        v for v in (rollup_progress(c) for c in children) if v is not None
    ]
    if "progress" not in node and child_values:
        node["progress"] = round(sum(child_values) / len(child_values), 1)
    return node.get("progress")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--no-sample",
        action="store_true",
        help=f"{SAMPLE_OUTPUT.relative_to(REPO_ROOT)} への複製をしない",
    )
    args = parser.parse_args()

    token = os.environ.get("NOTION_TOKEN")
    if not token:
        sys.exit(
            "環境変数 NOTION_TOKEN が未設定です。スクリプト冒頭のセットアップ手順を参照してください。"
        )

    org_pages = notion_query(ORG_DB_ID, token)
    goal_pages = notion_query(GOAL_DB_ID, token)

    company = build_org_tree(org_pages)
    attach_members(company, goal_pages)
    rollup_progress(company)

    text = json.dumps(company, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output}")

    if not args.no_sample:
        SAMPLE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        SAMPLE_OUTPUT.write_text(text, encoding="utf-8")
        print(f"wrote {SAMPLE_OUTPUT}")

    for token, sub in slice_agency_subtrees(company):
        agency_path = args.output.parent / f"roadmap-{token}.json"
        agency_text = json.dumps(sub, ensure_ascii=False, indent=2) + "\n"
        agency_path.write_text(agency_text, encoding="utf-8")
        print(f"wrote {agency_path} (代理店: {sub.get('title')})")


if __name__ == "__main__":
    main()
