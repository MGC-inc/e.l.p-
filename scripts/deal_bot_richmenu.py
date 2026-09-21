#!/usr/bin/env python3
"""「ユメイク営業分析bot」（DEAL_LINE_CHANNEL_ACCESS_TOKEN）に、
リッチメニュー「📊 今日の目標を見る」（メッセージアクション）を1枚だけ登録する。

このbotが元々持っていた4問クイックリプライ（録音受付）フローとは独立した機能。
タップされたテキストはline-webhook/api/webhook.py側のhandle_goal_requestが処理する。

処理:
  1. GET /v2/bot/info でbotのプロフィール画像を取得し、主要カラーを抽出する
  2. 抽出カラーに合わせた2500x1686の1枚絵（全面1ボタン）を生成する
  3. 既存リッチメニューを全削除（idempotent。このbotに他のリッチメニューがある
     前提はない＝録音受付は元々クイックリプライのみで運用されている）
  4. リッチメニュー作成→画像アップロード→全ユーザーのデフォルトに設定

.env の DEAL_LINE_CHANNEL_ACCESS_TOKEN を使用。
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".." / ".env"
IMG_PATH = "/tmp/deal_bot_richmenu.png"
FONT_PATH = "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"
if not Path(FONT_PATH).exists():
    FONT_PATH = "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"

W, H = 2500, 1686
BUTTON_TEXT = "今日の目標を見る"


def load_env() -> dict:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


def api(token: str, method: str, path: str, data=None):
    url = f"https://api.line.me/v2/bot/{path}"
    headers = {"Authorization": f"Bearer {token}"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def api_data_upload(token: str, richmenu_id: str, img_bytes: bytes):
    url = f"https://api-data.line.me/v2/bot/richmenu/{richmenu_id}/content"
    req = urllib.request.Request(url, data=img_bytes, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "image/png"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def extract_palette(token: str) -> tuple[tuple, tuple]:
    """botのプロフィール画像から (背景色, アクセント色) を抽出する。
    画像が取得できない場合はブランドに寄せたデフォルト（エメラルド系）にフォールバックする。
    """
    default_bg, default_accent = (6, 95, 70), (16, 185, 129)
    try:
        info = api(token, "GET", "info")
        picture_url = info.get("pictureUrl")
        if not picture_url:
            return default_bg, default_accent
        with urllib.request.urlopen(picture_url, timeout=20) as r:
            img_bytes = r.read()
        import io
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((120, 120))
        quantized = img.quantize(colors=5, method=Image.MEDIANCUT)
        palette = quantized.getpalette()
        counts = sorted(quantized.getcolors(), reverse=True)
        colors = []
        for count, idx in counts:
            r, g, b = palette[idx * 3], palette[idx * 3 + 1], palette[idx * 3 + 2]
            colors.append((r, g, b))
        if not colors:
            return default_bg, default_accent
        bg = colors[0]
        # 背景が暗すぎ/明るすぎると単調になるため、輝度で軽く補正する
        luma = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        if luma < 40:
            bg = tuple(min(255, c + 40) for c in bg)
        elif luma > 220:
            bg = tuple(max(0, c - 40) for c in bg)
        accent = colors[1] if len(colors) > 1 else default_accent
        return bg, accent
    except Exception as e:  # noqa: BLE001 - 画像取得に失敗してもデフォルトで継続する
        print(f"プロフィール画像からのカラー抽出に失敗（デフォルト配色で続行）: {e}")
        return default_bg, default_accent


def make_image(bg: tuple, accent: tuple) -> None:
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # 縦グラデーション風の帯（単色よりモダンに見せる軽い演出）
    bg2 = tuple(max(0, c - 18) for c in bg)
    for y in range(H):
        t = y / H
        c = tuple(int(bg[i] * (1 - t) + bg2[i] * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)

    cx, cy = W // 2, H // 2 - 140

    # ターゲット（目標）アイコン: 同心円＋中心ドット
    for radius, width in ((190, 16), (130, 16), (70, 0)):
        if width:
            d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                      outline=(255, 255, 255), width=width)
        else:
            d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=accent)

    f_label = ImageFont.truetype(FONT_PATH, 130)
    f_sub = ImageFont.truetype(FONT_PATH, 46)

    tb = d.textbbox((0, 0), BUTTON_TEXT, font=f_label)
    d.text((cx - (tb[2] - tb[0]) / 2, cy + 260), BUTTON_TEXT, font=f_label, fill=(255, 255, 255))

    sub = "TAP TO CHECK YOUR GOAL"
    sb = d.textbbox((0, 0), sub, font=f_sub)
    d.text((cx - (sb[2] - sb[0]) / 2, cy + 420), sub, font=f_sub, fill=tuple(
        min(255, c + 70) for c in accent))

    img.save(IMG_PATH, "PNG")
    print(f"image saved: {IMG_PATH} ({os.path.getsize(IMG_PATH)} bytes)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image-only", action="store_true",
                     help="画像生成のみ行い、LINEへの登録は行わない（確認用）")
    args = ap.parse_args()

    env = load_env()
    token = env.get("DEAL_LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        raise SystemExit("環境変数 DEAL_LINE_CHANNEL_ACCESS_TOKEN が未設定です。")

    bg, accent = extract_palette(token)
    print(f"抽出カラー: 背景={bg} アクセント={accent}")
    make_image(bg, accent)

    if args.image_only:
        print("--image-only のためLINEへの登録は行いません。")
        return

    existing = api(token, "GET", "richmenu/list").get("richmenus", [])
    for rm in existing:
        api(token, "DELETE", f"richmenu/{rm['richMenuId']}")
        print(f"deleted old richmenu {rm['richMenuId']}")

    menu = {
        "size": {"width": W, "height": H},
        "selected": True,
        "name": "営業分析BOT 今日の目標",
        "chatBarText": "今日の目標",
        "areas": [{
            "bounds": {"x": 0, "y": 0, "width": W, "height": H},
            "action": {"type": "message", "text": BUTTON_TEXT},
        }],
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
