#!/usr/bin/env python3
"""商談録音分析パイプラインに新しい営業マン（クローザー／アポインター／管理者）を登録する。

「ユメイク営業分析bot」は、LINE表示名が line-webhook/api/webhook.py の
KNOWN_CLOSERS / KNOWN_APPOINTERS / KNOWN_ADMINS に一致すれば、初回メッセージで
即座に本登録される（あいさつ→苗字→役割選択の2問を省略できる）。
このスクリプトはその一致リストに名前を追加する（メイン機能）。

表記が一致しなくても、本人はBotとのやり取り（苗字→クローザー/アポインター/管理者を
選ぶだけ）で自己登録できるので、このスクリプトを使わなくても新規メンバーは
Botを使える。このスクリプトは「あいさつの2問を省略したい」「Supabase・従業員.md・
ゴールマップも一括で整えたい」場合の時短ツール。

使い方:
  # クローザーとして登録（最小構成）
  python3 scripts/add_closer.py 山田太郎 --full-name "山田 太郎"

  # アポインターとして登録
  python3 scripts/add_closer.py 山田太郎 --full-name "山田 太郎" --role appointer

  # 管理者として登録（週次のチーム全体結果のみ受け取る）
  python3 scripts/add_closer.py 山田太郎 --role admin

  # 代理店所属の場合（従業員.mdの代理店メンバー表に追加）
  python3 scripts/add_closer.py 山田太郎 --full-name "山田 太郎" \\
      --agency 株式会社ピタサチ --role closer

  # ゴールマップの雛形も作る場合（クローザーのみ想定）
  python3 scripts/add_closer.py 山田太郎 --full-name "山田 太郎" --goalmap

  # 本人が既に一度Botにメッセージを送っている場合、Supabaseの仮登録行を確認できる
  python3 scripts/add_closer.py --list-pending

このスクリプトが自動でやること:
  1. line-webhook/api/webhook.py の KNOWN_CLOSERS / KNOWN_APPOINTERS / KNOWN_ADMINS
     （--roleで指定した方）に、苗字・姓名（スペースあり/なし）の表記ゆれを登録する
  2. 本人が既にBotへメッセージ済みで closer_line_users に仮登録行がある場合、
     closer_name・role をその場でSupabaseに反映する（redeployを待たずに使えるようにする）
  3. 従業員.md に行を追加する
  4. （--goalmap時）tools/goalmap/members/<氏名>.json をテンプレートから作成する

このスクリプトが自動でやらないこと（手動 or Claude+Notion MCPで別途対応）:
  - Notion「DB 商談分析＆アポ分析」のクローザー／アポインター選択肢に名前を追加する
    （既存データベースの選択肢文字列と完全一致させる運用のため、意図的に自動化していない。
    Claude Codeセッションでこのスクリプトを実行した流れで、Notion MCPで追加してもらう）
  - webhook.py の変更を実際にデプロイする（git commit・push は別途。デプロイ先は
    今川さん個人のVercelプロジェクトで、mainブランチへのpushで自動反映される想定）
  - 予算と達成率DB（Notion）への月次予算行の追加（クローザーの場合、週次実績配信の
    KPI逆算に使われる。Claude+Notion MCPで別途対応）

退社時の削除は scripts/remove_closer.py を使う。
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

ROLE_DICT_NAME = {"closer": "KNOWN_CLOSERS", "appointer": "KNOWN_APPOINTERS", "admin": "KNOWN_ADMINS"}
ROLE_LABEL = {"closer": "クローザー", "appointer": "アポインター", "admin": "管理者"}


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


def name_variants(surname, full_name):
    variants = {surname}
    if full_name:
        variants.add(full_name.replace(" ", "").replace("　", ""))
        if " " not in full_name and "　" not in full_name:
            pass  # スペース無し表記のみ渡された場合はそのまま
        else:
            variants.add(full_name)
    return variants


def update_known_dict(role, surname, full_name):
    dict_name = ROLE_DICT_NAME[role]
    if not os.path.exists(WEBHOOK_PATH):
        log(f"警告: {WEBHOOK_PATH} が見つかりません。{dict_name} の更新をスキップします。")
        return False
    with open(WEBHOOK_PATH, encoding="utf-8") as f:
        content = f.read()

    m = re.search(dict_name + r" = (\{[^}]*\})", content, re.DOTALL)
    if not m:
        log(f"警告: webhook.py 内に {dict_name} が見つかりませんでした。手動で追加してください。")
        return False

    dict_literal = m.group(1)
    if f'"{surname}"' in dict_literal:
        log(f"{dict_name} には既に「{surname}」が含まれています。変更なし。")
        return False

    entries = ", ".join(f'"{v}": "{surname}"' for v in sorted(name_variants(surname, full_name)))
    # 末尾の "}" の直前（末尾カンマの有無を問わない）に新しい行を挿入する
    insert_pos = m.end(1) - 1
    inner = dict_literal[1:-1].rstrip()
    if inner.endswith(","):
        new_inner = f"{inner}\n    {entries},\n"
    elif inner:
        new_inner = f"{inner},\n    {entries},\n"
    else:
        new_inner = f"\n    {entries},\n"
    new_literal = "{" + new_inner + "}"
    content = content[:m.start(1)] + new_literal + content[m.end(1):]
    with open(WEBHOOK_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    log(f"line-webhook/api/webhook.py の {dict_name} に「{surname}」を追加しました。")
    return True


def patch_pending_row_if_exists(env, surname, role):
    rows = sb_request(env, "GET", f"closer_line_users?closer_name=is.null&order=created_at.desc&select=*")
    if not rows:
        return
    if len(rows) == 1:
        target = rows[0]
    else:
        log(f"仮登録待ちが複数（{len(rows)}件）あるため、Supabaseの即時反映はスキップします。"
            "本人の初回メッセージ送信後に再実行するか、次回デプロイ後の自己登録に任せてください。")
        return
    sb_request(env, "PATCH", f"closer_line_users?id=eq.{target['id']}", {"closer_name": surname, "role": role})
    log(f"Supabase closer_line_users: line_user_id={target['line_user_id']} に "
        f"closer_name=「{surname}」・role=「{role}」を即時反映しました。")


def update_employees_doc(closer_name, role_label, agency):
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
        row = f"| {closer_name} | {agency} | {role_label} | | | |\n"
    else:
        row = f"| {closer_name} | {role_label} | | | |\n"

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
    ap.add_argument("surname", nargs="?", help="苗字（クローザー名・KNOWN_*の登録キーとして使う表記）")
    ap.add_argument("--full-name", help="フルネーム（スペースあり推奨。表記ゆれ登録に使う。例: '山田 太郎'）")
    ap.add_argument("--role", choices=["closer", "appointer", "admin"], default="closer",
                     help="役割（既定: closer）")
    ap.add_argument("--agency", help="代理店所属の場合の会社名（従業員.mdの代理店メンバー表に追加）")
    ap.add_argument("--goalmap", action="store_true", help="tools/goalmap/members/<氏名>.json の雛形も作成する")
    ap.add_argument("--list-pending", action="store_true", help="仮登録待ち（closer_name未設定）の一覧を表示して終了")
    args = ap.parse_args()

    env = load_env(ENV_PATH)

    if args.list_pending:
        list_pending(env)
        return

    if not args.surname:
        sys.exit("苗字を指定するか --list-pending を使ってください（-h でヘルプ）")

    update_known_dict(args.role, args.surname, args.full_name)
    patch_pending_row_if_exists(env, args.surname, args.role)
    update_employees_doc(args.surname, ROLE_LABEL[args.role], args.agency)
    if args.goalmap:
        create_goalmap_member(args.surname)

    print("\n--- 残りの手動ステップ ---")
    if args.role in ("closer", "appointer"):
        print(f"1. Notion「DB 商談分析＆アポ分析」の {ROLE_LABEL[args.role]} 選択肢に「{args.surname}」を追加する"
              "（Claude+Notion MCPで実施）")
    if args.role == "closer":
        print("2. Notion「💰 予算と達成率」に今月分の予算行を追加する（週次実績配信のKPI逆算に使う。Claude+Notion MCPで実施）")
    print("3. line-webhook/api/webhook.py の変更をコミット・pushする（今川さん個人のVercelプロジェクトに自動反映）")
    print("4. 従業員.md ・（該当すれば）goalmapファイルの中身を確認・コミットする")


if __name__ == "__main__":
    main()
