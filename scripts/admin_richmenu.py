#!/usr/bin/env python3
"""管理者専用のリッチメニュー「全員の数字」（日次／週次／月次／直近3ヶ月の4ボタン）を作る。

- 他のメニュー（全員向け・クローザー用）は消さない。同名の管理者用メニューだけ作り直す
- デフォルトにはしない。Supabase closer_line_users の role='admin' の人に個別リンクする
  （今後 role=admin に確定した人は webhook.py の link_admin_richmenu が自動でリンクする）
- ボタンが送る文言は webhook.py の TEAM_TEXT（「全員の数字」）＋期間と完全一致させる

使い方: python3 scripts/admin_richmenu.py
"""
from __future__ import annotations

import json
import urllib.request

import deal_bot_richmenu as base

MENU_NAME = "営業分析BOT 管理者用"
IMG_PATH = "/tmp/deal_bot_richmenu_admin.jpg"
CELLS = [("日次\n全員の数字", "全員の数字 日次"), ("週次\n全員の数字", "全員の数字 週次"),
         ("月次\n全員の数字", "全員の数字 月次"), ("直近3ヶ月\n全員の数字", "全員の数字 直近3ヶ月")]


def main() -> None:
    env = base.load_env()
    token = env["DEAL_LINE_CHANNEL_ACCESS_TOKEN"]
    base.make_grid_image(2, CELLS, IMG_PATH)
    for rm in base.api(token, "GET", "richmenu/list").get("richmenus", []):
        if rm.get("name") == MENU_NAME:
            base.api(token, "DELETE", f"richmenu/{rm['richMenuId']}")
            print(f"deleted old admin richmenu {rm['richMenuId']}")
    rid = base.register_menu(token, MENU_NAME, "全員の数字", base.grid_areas(2, CELLS), IMG_PATH)
    users = base.sb_get(env, "closer_line_users?role=eq.admin&select=line_user_id,closer_name")
    for u in users:
        base.api(token, "POST", f"user/{u['line_user_id']}/richmenu/{rid}")
        print(f"linked admin menu: {u['closer_name']}")
    if not users:
        print("role=admin の登録者はまだいません（本人が最初のメッセージを送った時に自動でリンクされます）")


if __name__ == "__main__":
    main()
