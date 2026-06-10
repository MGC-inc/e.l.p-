#!/usr/bin/env python3
"""E.L.P LINE Bot のリッチメニュー（日報/タスク/成績/通話）を生成・登録する。

処理:
  1. 2500x843 の画像を生成（4セル、絵文字なしのクリーンなアイコン）
  2. 既存リッチメニューを全削除（idempotent）
  3. リッチメニュー作成（各セル=messageアクションで「日報/タスク/成績/通話」送信）
  4. 画像アップロード → デフォルトに設定

タップすると Webhook の既存コマンド処理が走り、各アプリページのリンクが返る。
.env の ELP_LINE_CHANNEL_ACCESS_TOKEN を使用。
"""
import json
import os
import urllib.request

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "..", ".env")
IMG_PATH = "/tmp/elp_richmenu.png"
FONT = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"

W, H = 2500, 843
CELLS = [
    {"label": "日報", "sub": "REPORT", "text": "日報"},
    {"label": "タスク", "sub": "TASK", "text": "タスク"},
    {"label": "成績", "sub": "SALES", "text": "成績"},
    {"label": "通話", "sub": "CALLS", "text": "通話"},
]
# 交互のエメラルド濃淡（アプリのテーマに合わせる）
BG = [(5, 122, 85), (4, 108, 75), (5, 122, 85), (4, 108, 75)]
ACCENT = (16, 185, 129)


def load_env():
    env = {}
    for line in open(ENV_PATH, encoding="utf-8"):
        line = line.rstrip("\n")
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def draw_icon(d, kind, cx, cy, c=(255, 255, 255)):
    """簡易ラインアイコン。"""
    lw = 9
    if kind == "日報":  # 書類
        d.rounded_rectangle([cx - 70, cy - 90, cx + 70, cy + 90], radius=14, outline=c, width=lw)
        for i in range(3):
            y = cy - 40 + i * 40
            d.line([cx - 40, y, cx + 40, y], fill=c, width=lw - 2)
    elif kind == "タスク":  # チェックボックス
        d.rounded_rectangle([cx - 80, cy - 80, cx + 80, cy + 80], radius=18, outline=c, width=lw)
        d.line([cx - 42, cy + 4, cx - 12, cy + 40], fill=ACCENT, width=lw + 4)
        d.line([cx - 12, cy + 40, cx + 50, cy - 44], fill=ACCENT, width=lw + 4)
    elif kind == "成績":  # 棒グラフ
        bars = [(-70, 30), (-10, -20), (50, -70)]
        for x, top in bars:
            d.rounded_rectangle([cx + x - 22, cy + top, cx + x + 22, cy + 80], radius=6, fill=c)
    elif kind == "通話":  # 受話器（簡易）
        d.arc([cx - 80, cy - 80, cx + 80, cy + 80], start=20, end=200, fill=c, width=lw + 6)
        d.ellipse([cx - 90, cy - 6, cx - 58, cy + 26], fill=c)
        d.ellipse([cx + 58, cy - 6, cx + 90, cy + 26], fill=c)


def make_image():
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    cw = W // 4
    f_label = ImageFont.truetype(FONT, 120)
    f_sub = ImageFont.truetype(FONT, 42)
    for i, cell in enumerate(CELLS):
        x0 = i * cw
        d.rectangle([x0, 0, x0 + cw, H], fill=BG[i])
        if i > 0:  # 区切り線
            d.line([x0, 60, x0, H - 60], fill=(255, 255, 255), width=3)
        cx = x0 + cw // 2
        draw_icon(d, cell["label"], cx, 300)
        # ラベル中央寄せ
        tb = d.textbbox((0, 0), cell["label"], font=f_label)
        d.text((cx - (tb[2] - tb[0]) / 2, 470), cell["label"], font=f_label, fill=(255, 255, 255))
        sb = d.textbbox((0, 0), cell["sub"], font=f_sub)
        d.text((cx - (sb[2] - sb[0]) / 2, 640), cell["sub"], font=f_sub, fill=(180, 230, 210))
    img.save(IMG_PATH, "PNG")
    print(f"image saved: {IMG_PATH} ({os.path.getsize(IMG_PATH)} bytes)")


def api(token, method, path, data=None, ctype="application/json", raw=False):
    url = f"https://api.line.me/v2/bot/{path}"
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None and not raw:
        data = json.dumps(data).encode()
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    if raw:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        return json.loads(body) if body and not raw else body


def api_data_upload(token, richmenu_id, img_bytes):
    # 画像アップロードは api-data.line.me
    url = f"https://api-data.line.me/v2/bot/richmenu/{richmenu_id}/content"
    req = urllib.request.Request(url, data=img_bytes, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "image/png"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def main():
    env = load_env()
    token = env["ELP_LINE_CHANNEL_ACCESS_TOKEN"]
    make_image()

    # 既存を全削除
    existing = api(token, "GET", "richmenu/list").get("richmenus", [])
    for rm in existing:
        api(token, "DELETE", f"richmenu/{rm['richMenuId']}")
        print(f"deleted old richmenu {rm['richMenuId']}")

    cw = W // 4
    areas = []
    for i, cell in enumerate(CELLS):
        areas.append({
            "bounds": {"x": i * cw, "y": 0, "width": cw, "height": H},
            "action": {"type": "message", "text": cell["text"]},
        })
    menu = {
        "size": {"width": W, "height": H},
        "selected": True,
        "name": "E.L.P メニュー",
        "chatBarText": "メニュー",
        "areas": areas,
    }
    res = api(token, "POST", "richmenu", menu)
    rid = res["richMenuId"]
    print(f"created richmenu: {rid}")

    status = api_data_upload(token, rid, open(IMG_PATH, "rb").read())
    print(f"image upload status: {status}")

    api(token, "POST", f"user/all/richmenu/{rid}")
    print("set as default richmenu ✅")


if __name__ == "__main__":
    main()
