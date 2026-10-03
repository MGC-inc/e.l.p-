#!/usr/bin/env python3
"""退社した営業マン（クローザー／アポインター／管理者）を商談録音分析パイプラインから削除する。

scripts/add_closer.py の対になるスクリプト。

使い方:
  python3 scripts/remove_closer.py 山田太郎

  # ゴールマップファイルも削除する場合（既定では残す。過去の実績確認用に取っておく想定）
  python3 scripts/remove_closer.py 山田太郎 --remove-goalmap

このスクリプトが自動でやること:
  1. line-webhook/api/webhook.py の KNOWN_CLOSERS / KNOWN_APPOINTERS / KNOWN_ADMINS
     から、値がその氏名に一致するエントリ（表記ゆれ含む）を全て削除する
     （以後、本人がBotに再度メッセージを送っても自動登録されず、あいさつ→苗字→役割選択の
     自己登録フローに入る＝実質的にアクセスできなくなる）
  2. Supabase closer_line_users から、closer_name が一致する行を削除する
     （以後、週次実績配信の対象外になる。過去の deal_recordings.closer_name は
     テキストのスナップショットなので、この行を消しても過去の分析結果・録音は消えない）
  3. 従業員.md からその人の行を削除する（git履歴には残るので、元に戻す必要が出たら
     `git log -- 従業員.md` から復元できる）
  4. （--remove-goalmap時）tools/goalmap/members/<氏名>.json を削除する

このスクリプトが自動でやらないこと（意図的。過去データを壊さないため）:
  - Notion「DB 商談分析＆アポ分析」のクローザー／アポインター選択肢からの削除
    （既存の過去ページがその選択肢を参照しているため、選択肢自体を消すと表示が壊れる。
    消さずに残しておいて問題ない）
  - Notion「💰 予算と達成率」の来月以降の予算行の停止（今川さんが次月の予算設定時に
    自然と除外される想定。急ぎ止めたい場合はClaude+Notion MCPで別途対応）
  - webhook.py の変更を実際にデプロイする（git commit・push は別途）
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENV_PATH = os.path.join(REPO, ".env")
WEBHOOK_PATH = os.path.join(REPO, "line-webhook", "api", "webhook.py")
EMPLOYEES_PATH = os.path.join(REPO, "従業員.md")
GOALMAP_DIR = os.path.join(REPO, "tools", "goalmap", "members")

KNOWN_DICT_NAMES = ["KNOWN_CLOSERS", "KNOWN_APPOINTERS", "KNOWN_ADMINS"]


def log(msg):
    print(msg, file=sys.stderr)


def clean_env_value(v):
    return v.strip().strip("（）()")


def load_env(path):
    env = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    for key in ("ELP_SUPABASE_URL", "ELP_SUPABASE_SERVICE_ROLE_KEY"):
        if key not in env and key in os.environ:
            env[key] = os.environ[key]
    if "ELP_SUPABASE_URL" not in env or "ELP_SUPABASE_SERVICE_ROLE_KEY" not in env:
        sys.exit("ELP_SUPABASE_URL / ELP_SUPABASE_SERVICE_ROLE_KEY が見つかりません（.env か環境変数を確認）")
    env["ELP_SUPABASE_URL"] = clean_env_value(env["ELP_SUPABASE_URL"])
    env["ELP_SUPABASE_SERVICE_ROLE_KEY"] = clean_env_value(env["ELP_SUPABASE_SERVICE_ROLE_KEY"])
    return env


def sb_request(env, method, path, data=None):
    url = f"{env['ELP_SUPABASE_URL']}/rest/v1/{path}"
    key = env["ELP_SUPABASE_SERVICE_ROLE_KEY"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def remove_from_known_dicts(surname):
    if not os.path.exists(WEBHOOK_PATH):
        log(f"警告: {WEBHOOK_PATH} が見つかりません。KNOWN_*の更新をスキップします。")
        return
    with open(WEBHOOK_PATH, encoding="utf-8") as f:
        content = f.read()

    any_removed = False
    for dict_name in KNOWN_DICT_NAMES:
        m = re.search(dict_name + r" = (\{[^}]*\})", content, re.DOTALL)
        if not m:
            continue
        dict_literal = m.group(1)
        # "キー": "苗字" の形のエントリを、末尾カンマごと削除する
        pattern = re.compile(r'\s*"[^"]*":\s*"' + re.escape(surname) + r'",?')
        new_literal, n = pattern.subn("", dict_literal)
        if n:
            content = content[:m.start(1)] + new_literal + content[m.end(1):]
            log(f"{dict_name} から「{surname}」関連のエントリを{n}件削除しました。")
            any_removed = True

    if any_removed:
        with open(WEBHOOK_PATH, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        log(f"KNOWN_CLOSERS/KNOWN_APPOINTERS/KNOWN_ADMINSに「{surname}」は見つかりませんでした。")


def remove_from_supabase(env, surname):
    q = urllib.parse.quote(surname, safe="")
    rows = sb_request(env, "GET", f"closer_line_users?closer_name=eq.{q}&select=*")
    if not rows:
        log(f"Supabase closer_line_users に「{surname}」の行は見つかりませんでした。")
        return
    sb_request(env, "DELETE", f"closer_line_users?closer_name=eq.{q}")
    log(f"Supabase closer_line_users から「{surname}」の行を{len(rows)}件削除しました "
        f"（line_user_id: {', '.join(r['line_user_id'] for r in rows)}）。")


def remove_from_employees_doc(surname):
    if not os.path.exists(EMPLOYEES_PATH):
        log(f"警告: {EMPLOYEES_PATH} が見つかりません。従業員.mdの更新をスキップします。")
        return
    with open(EMPLOYEES_PATH, encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = [l for l in lines if not (l.startswith("|") and f"| {surname} " in l)]
    removed = len(lines) - len(new_lines)
    if not removed:
        log(f"従業員.md に「{surname}」の行は見つかりませんでした。")
        return
    with open(EMPLOYEES_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    log(f"従業員.md から「{surname}」の行を{removed}件削除しました（git履歴には残ります）。")


def remove_goalmap(surname):
    path = os.path.join(GOALMAP_DIR, f"{surname}.json")
    if not os.path.exists(path):
        log(f"tools/goalmap/members/{surname}.json は存在しません。")
        return
    os.remove(path)
    log(f"tools/goalmap/members/{surname}.json を削除しました。")


def main():
    ap = argparse.ArgumentParser(description="退社した営業マンを商談録音分析パイプラインから削除する")
    ap.add_argument("surname", help="苗字（closer_name・KNOWN_*の登録キーとして使われている表記）")
    ap.add_argument("--remove-goalmap", action="store_true",
                     help="tools/goalmap/members/<氏名>.json も削除する（既定では残す）")
    args = ap.parse_args()

    env = load_env(ENV_PATH)

    remove_from_known_dicts(args.surname)
    remove_from_supabase(env, args.surname)
    remove_from_employees_doc(args.surname)
    if args.remove_goalmap:
        remove_goalmap(args.surname)

    print("\n--- 残りの手動ステップ ---")
    print("1. line-webhook/api/webhook.py の変更をコミット・pushする（今川さん個人のVercelプロジェクトに自動反映）")
    print("2. Notion「💰 予算と達成率」の来月以降の予算行を止める（必要なら。Claude+Notion MCPで実施）")
    print("   ※ Notionの選択肢（クローザー／アポインター）と過去のdeal_recordings・録音データは、")
    print("     過去実績を壊さないため意図的に削除していません。")


if __name__ == "__main__":
    main()
