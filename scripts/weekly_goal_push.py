#!/usr/bin/env python3
"""毎週水曜8:00 JSTの「週次目標」自動送信（営業分析BOTからの唯一の自動送信）。

Notion「📊 個人別KPI逆算データ（参照用）」DBの数式プロパティ
（`_週末メッセージ_クローザー` / `_週末メッセージ_アポインター`）の値を、
役割に応じてそのままLINE Push APIで送る。計算は一切ここでやり直さない
（進捗バー・逆算等はすべてNotion側の数式が担う）。

数式プロパティの計算結果はNotion公式REST APIでしか取得できない
（Notion MCP経由では参照URLしか返らず中身が読めない）ため、このスクリプトは
tools/roadmap/sync_notion_to_json.py と同じ方式でREST APIを直接叩く。

前提（初回のみ・Notion UI操作）:
    1. https://www.notion.so/my-integrations でInternal Integrationを作成
       （既にtools/roadmap用に作成済みなら使い回してよい）
    2. 「📊 個人別KPI逆算データ（参照用）」DBの「•••」→ Connections →
       作成したインテグレーションを追加（共有）
    3. 発行されたInternal Integration Secretを環境変数 NOTION_TOKEN に設定
       （このリポジトリやチャットにトークンの値を書かない）

使い方:
    python3 scripts/weekly_goal_push.py            # 実際に送信する
    python3 scripts/weekly_goal_push.py --dry-run   # 送信せず内容を表示するだけ
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".." / ".env"

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"
# 「📊 個人別KPI逆算データ（参照用）」DBのdatabase_id（固定値・秘密情報ではない）
KPI_DATABASE_ID = "5f110c1c-b977-4054-9b00-5b3ce27d3493"

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
PUSH_RETRIES = 3
PUSH_RETRY_WAIT_SEC = 5


def load_env() -> dict:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


def notion_query_all(token: str) -> list[dict]:
    results = []
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"{NOTION_API}/databases/{KPI_DATABASE_ID}/query",
            data=json.dumps(body).encode(), method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read())
        results.extend(payload.get("results", []))
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
    return results


def prop_title(page: dict, name: str) -> str:
    p = page.get("properties", {}).get(name) or {}
    return "".join(t.get("plain_text", "") for t in p.get("title", []))


def prop_select(page: dict, name: str) -> str | None:
    p = page.get("properties", {}).get(name) or {}
    v = p.get("select")
    return v.get("name") if v else None


def prop_formula_text(page: dict, name: str) -> str:
    p = page.get("properties", {}).get(name) or {}
    formula = p.get("formula") or {}
    return (formula.get("string") or "").strip()


def prop_rollup_status(page: dict, name: str) -> str | None:
    p = page.get("properties", {}).get(name) or {}
    rollup = p.get("rollup") or {}
    for item in rollup.get("array", []):
        select = item.get("select")
        if select and select.get("name"):
            return select["name"]
    return None


def sb_get(env: dict, path: str) -> list:
    url = f"{env['ELP_SUPABASE_URL']}/rest/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": env["ELP_SUPABASE_SERVICE_ROLE_KEY"],
        "Authorization": f"Bearer {env['ELP_SUPABASE_SERVICE_ROLE_KEY']}",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def line_push(env: dict, line_user_id: str, text: str):
    body = json.dumps({"to": line_user_id, "messages": [{"type": "text", "text": text}]}).encode()
    req = urllib.request.Request(LINE_PUSH_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {env['DEAL_LINE_CHANNEL_ACCESS_TOKEN']}",
        "Content-Type": "application/json",
    })
    last_err = None
    for attempt in range(1, PUSH_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=20):
                return
        except urllib.error.HTTPError as e:
            last_err = f"{e.code} {e.read().decode('utf-8', errors='replace')}"
            if attempt < PUSH_RETRIES:
                time.sleep(PUSH_RETRY_WAIT_SEC)
    raise RuntimeError(last_err)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="送信せず対象者と本文を表示するだけ")
    args = ap.parse_args()

    env = load_env()
    notion_token = env.get("NOTION_TOKEN")
    if not notion_token:
        sys.exit(
            "環境変数 NOTION_TOKEN が未設定です。scripts/weekly_goal_push.py 冒頭の"
            "セットアップ手順を参照してください。"
        )

    pages = notion_query_all(notion_token)
    print(f"KPI逆算データ: {len(pages)}行取得")

    closers = sb_get(env, "closer_line_users?select=*")
    line_user_by_name = {row["closer_name"]: row["line_user_id"] for row in closers if row.get("closer_name")}

    sent, skipped, failed = [], [], []

    for page in pages:
        member = prop_title(page, "メンバー")
        if not member:
            continue
        role = prop_select(page, "役割")
        if role not in ("クローザー", "アポインター"):
            continue
        status = prop_rollup_status(page, "在籍状況(名簿参照)")
        if status == "退社":
            continue

        text = prop_formula_text(
            page, "_週末メッセージ_クローザー" if role == "クローザー" else "_週末メッセージ_アポインター"
        )
        if not text:
            skipped.append((member, "本文が空（Notion側の数式が未算出）"))
            continue

        line_user_id = line_user_by_name.get(member)
        if not line_user_id:
            skipped.append((member, "closer_line_usersにLINE user_idが見つからない"))
            continue

        if args.dry_run:
            print(f"\n--- {member}（{role}） ---\n{text}")
            sent.append(member)
            continue

        try:
            line_push(env, line_user_id, text)
            sent.append(member)
        except Exception as e:  # noqa: BLE001 - 1人の失敗で他を止めない
            failed.append((member, str(e)))

    print(f"\n送信成功: {len(sent)}人 {sent}")
    if skipped:
        print(f"スキップ: {len(skipped)}件 {skipped}")
    if failed:
        print(f"送信失敗: {len(failed)}件 {failed}")

    if not args.dry_run and (skipped or failed):
        admin_id = line_user_by_name.get("今川")
        if admin_id:
            lines = ["📣 週次目標配信レポート"]
            if failed:
                lines.append("送信失敗:")
                lines.extend(f"・{name}: {reason}" for name, reason in failed)
            if skipped:
                lines.append("スキップ:")
                lines.extend(f"・{name}: {reason}" for name, reason in skipped)
            try:
                line_push(env, admin_id, "\n".join(lines))
            except Exception as e:  # noqa: BLE001
                print(f"今川さんへのエスカレーション通知にも失敗: {e}")


if __name__ == "__main__":
    main()
