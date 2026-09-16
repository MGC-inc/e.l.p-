#!/usr/bin/env python3
"""商談録音分析パイプラインに新しい営業マン（クローザー／アポインター）を登録する。

前提: 本人が一度「ユメイク営業分析bot」にLINEでメッセージを送っていること。
Webhook（line-webhook/api/webhook.py）が自動で closer_line_users に
line_user_id + display_name（LINEの表示名）を仮登録する（closer_name は空のまま）。
このスクリプトはその仮登録行を見つけて、正式な氏名（closer_name）を確定させる。

使い方:
  # 仮登録待ち（closer_name未設定）の一覧を確認
  python3 scripts/add_closer.py --list-pending

  # 表示名を指定して本登録（クローザーとして）
  python3 scripts/add_closer.py 山田太郎 --display-name "やまだ太郎"

  # アポインターとしても選べるようにする（LINEクイックリプライに追加）
  python3 scripts/add_closer.py 山田太郎 --display-name "やまだ太郎" --appointer

  # 代理店所属の場合（従業員.mdの代理店メンバー表に追加）
  python3 scripts/add_closer.py 山田太郎 --display-name "やまだ太郎" \\
      --agency 株式会社ピタサチ --role 営業

  # ゴールマップの雛形も作る場合
  python3 scripts/add_closer.py 山田太郎 --display-name "やまだ太郎" --goalmap

  # display_nameでの一致が複数/不明な場合は line_user_id を直接指定
  python3 scripts/add_closer.py 山田太郎 --line-user-id U1234...

このスクリプトが自動でやること:
  1. Supabase closer_line_users: 該当する仮登録行に closer_name（・任意で
     goalmap_member_name）を設定する
  2. （--appointer時）line-webhook/api/webhook.py の APPOINTER_OPTIONS に追加する
  3. 従業員.md に行を追加する
  4. （--goalmap時）tools/goalmap/members/<氏名>.json をテンプレートから作成する

このスクリプトが自動でやらないこと（手動 or Claude+Notion MCPで別途対応）:
  - Notion「DB 商談分析＆アポ分析」のクローザー／アポインター選択肢に名前を追加する
    （既存データベースの選択肢文字列と完全一致させる運用のため、意図的に自動化していない。
    Claude Codeセッションでこのスクリプトを実行した流れで、Notion MCPで追加してもらう）
  - webhook.py の変更を実際にデプロイする（git commit・push は別途。デプロイ先は
    今川さん個人のVercelプロジェクトで、mainブランチへのpushで自動反映される想定）
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
GOALMAP_TEMPLATE = os.path.join(GOALMAP_DIR, "_template.json")


def log(msg):
    print(msg, file=sys.stderr)


def clean_env_value(v):
    # 環境変数に全角括弧が紛れ込むことがあるための防御的処理
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


def list_pending(env):
    rows = sb_request(env, "GET", "closer_line_users?closer_name=is.null&select=*&order=created_at.desc")
    if not rows:
        print("仮登録待ち（closer_name未設定）の人はいません。")
        return
    print("仮登録待ち一覧（本人がbotに一度メッセージを送った状態）:")
    for row in rows:
        print(f"  - display_name={row.get('display_name') or '(空)'}  line_user_id={row['line_user_id']}  "
              f"created_at={row.get('created_at')}")


def find_pending_row(env, display_name, line_user_id):
    if line_user_id:
        rows = sb_request(env, "GET", f"closer_line_users?line_user_id=eq.{urllib.parse.quote(line_user_id)}&select=*")
        if not rows:
            sys.exit(f"line_user_id={line_user_id} の行が見つかりません。")
        return rows[0]

    if not display_name:
        sys.exit("--display-name か --line-user-id のどちらかを指定してください（--list-pending で確認可）")

    q = urllib.parse.quote(display_name, safe="")
    rows = sb_request(env, "GET", f"closer_line_users?display_name=eq.{q}&select=*")
    if not rows:
        sys.exit(
            f"表示名「{display_name}」で仮登録された行が見つかりません。\n"
            "→ 本人が「ユメイク営業分析bot」にまだ一度もメッセージを送っていない可能性があります。\n"
            "  一度何か送ってもらってから再実行してください（--list-pending で確認できます）。"
        )
    if len(rows) > 1:
        sys.exit(
            f"表示名「{display_name}」に一致する行が複数見つかりました。--line-user-id で対象を指定してください:\n"
            + "\n".join(f"  - {r['line_user_id']} (created_at={r.get('created_at')})" for r in rows)
        )
    return rows[0]


def update_webhook_appointer_options(closer_name):
    if not os.path.exists(WEBHOOK_PATH):
        log(f"警告: {WEBHOOK_PATH} が見つかりません。APPOINTER_OPTIONS の更新をスキップします。")
        return False
    with open(WEBHOOK_PATH, encoding="utf-8") as f:
        content = f.read()

    m = re.search(r'APPOINTER_OPTIONS = (\[[^\]]*\])', content)
    if not m:
        log("警告: webhook.py 内に APPOINTER_OPTIONS が見つかりませんでした。手動で追加してください。")
        return False

    options = json.loads(m.group(1).replace("'", '"'))
    if closer_name in options:
        log(f"APPOINTER_OPTIONS には既に「{closer_name}」が含まれています。変更なし。")
        return False

    options.append(closer_name)
    new_literal = json.dumps(options, ensure_ascii=False)
    content = content[:m.start(1)] + new_literal + content[m.end(1):]
    with open(WEBHOOK_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    log(f"line-webhook/api/webhook.py の APPOINTER_OPTIONS に「{closer_name}」を追加しました。")
    return True


def update_employees_doc(closer_name, role, agency):
    if not os.path.exists(EMPLOYEES_PATH):
        log(f"警告: {EMPLOYEES_PATH} が見つかりません。従業員.mdの更新をスキップします。")
        return False
    with open(EMPLOYEES_PATH, encoding="utf-8") as f:
        lines = f.readlines()

    if closer_name in "".join(lines):
        log(f"従業員.md には既に「{closer_name}」の記載があります。追記をスキップします。")
        return False

    heading = "## 2. 代理店メンバー" if agency else "## 1. メンバー一覧"
    header_idx = next((i for i, l in enumerate(lines) if l.strip() == heading), None)
    if header_idx is None:
        log(f"警告: 従業員.md に「{heading}」セクションが見つかりません。手動で追加してください。")
        return False

    # ヘッダー2行（見出し＋テーブルヘッダー＋区切り線）の後、次の空行/見出しの直前に挿入する
    insert_idx = header_idx + 1
    while insert_idx < len(lines) and not lines[insert_idx].startswith("##"):
        if lines[insert_idx].strip() == "" and insert_idx > header_idx + 3:
            break
        insert_idx += 1

    if agency:
        row = f"| {closer_name} | {agency} | {role} | | | |\n"
    else:
        row = f"| {closer_name} | {role} | | | |\n"

    lines.insert(insert_idx, row)
    with open(EMPLOYEES_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
    log(f"従業員.md の「{heading}」に「{closer_name}」を追加しました。")
    return True


def create_goalmap_member(closer_name):
    if not os.path.exists(GOALMAP_TEMPLATE):
        log(f"警告: {GOALMAP_TEMPLATE} が見つかりません。ゴールマップ作成をスキップします。")
        return False
    out_path = os.path.join(GOALMAP_DIR, f"{closer_name}.json")
    if os.path.exists(out_path):
        log(f"tools/goalmap/members/{closer_name}.json は既に存在します。上書きしません。")
        return False
    with open(GOALMAP_TEMPLATE, encoding="utf-8") as f:
        data = json.load(f)
    data["name"] = closer_name
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    log(f"tools/goalmap/members/{closer_name}.json を作成しました（中身は要編集: テーマ・ゴール・フェーズ等）。")
    return True


def main():
    ap = argparse.ArgumentParser(description="商談録音分析パイプラインに新しい営業マンを登録する")
    ap.add_argument("closer_name", nargs="?", help="正式な氏名（クローザー名として使う表記）")
    ap.add_argument("--display-name", help="LINEの表示名（closer_line_users の仮登録行を検索するキー）")
    ap.add_argument("--line-user-id", help="display_nameで一意に絞れない場合に直接指定")
    ap.add_argument("--appointer", action="store_true", help="アポインターとしても登録する（webhook.pyのクイックリプライに追加）")
    ap.add_argument("--agency", help="代理店所属の場合の会社名（従業員.mdの代理店メンバー表に追加）")
    ap.add_argument("--role", default="営業", help="従業員.mdに記載する役職（既定: 営業）")
    ap.add_argument("--goalmap", action="store_true", help="tools/goalmap/members/<氏名>.json の雛形も作成する")
    ap.add_argument("--list-pending", action="store_true", help="仮登録待ち（closer_name未設定）の一覧を表示して終了")
    args = ap.parse_args()

    env = load_env(ENV_PATH)

    if args.list_pending:
        list_pending(env)
        return

    if not args.closer_name:
        sys.exit("氏名を指定するか --list-pending を使ってください（-h でヘルプ）")

    row = find_pending_row(env, args.display_name, args.line_user_id)
    patch = {"closer_name": args.closer_name}
    if args.goalmap:
        patch["goalmap_member_name"] = args.closer_name

    sb_request(env, "PATCH", f"closer_line_users?id=eq.{row['id']}", patch)
    log(f"Supabase closer_line_users: line_user_id={row['line_user_id']} に closer_name=「{args.closer_name}」を設定しました。")

    if args.appointer:
        update_webhook_appointer_options(args.closer_name)
    update_employees_doc(args.closer_name, args.role, args.agency)
    if args.goalmap:
        create_goalmap_member(args.closer_name)

    print("\n--- 残りの手動ステップ ---")
    print(f"1. Notion「DB 商談分析＆アポ分析」の クローザー{'／アポインター' if args.appointer else ''} 選択肢に"
          f"「{args.closer_name}」を追加する（Claude+Notion MCPで実施）")
    if args.appointer:
        print("2. line-webhook/api/webhook.py の変更をコミット・pushする（今川さん個人のVercelプロジェクトに自動反映）")
    print("3. 従業員.md ・（該当すれば）goalmapファイルの中身を確認・コミットする")


if __name__ == "__main__":
    main()
