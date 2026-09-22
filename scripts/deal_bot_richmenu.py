#!/usr/bin/env python3
"""「ユメイク営業分析bot」（DEAL_LINE_CHANNEL_ACCESS_TOKEN）に、
リッチメニュー「今日の目標を見る」（メッセージアクション）を1枚だけ登録する。

このbotが元々持っていた4問クイックリプライ（録音受付）フローとは独立した機能。
タップされたテキストはline-webhook/api/webhook.py側のhandle_goal_requestが処理する。

デザイン（ユーザー指示）: 気合い・勢いを感じるファイヤー（炎）モチーフ、光るグロー
演出、イメージカラーは赤。botのプロフィール画像から配色を抽出する方式はやめ、
この赤系ファイヤーカラーで固定する。

処理:
  1. 赤系ファイヤーカラーで2500x1686の1枚絵（全面1ボタン）を生成する
  2. 既存リッチメニューを全削除（idempotent。このbotに他のリッチメニューがある
     前提はない＝録音受付は元々クイックリプライのみで運用されている）
  3. リッチメニュー作成→画像アップロード→全ユーザーのデフォルトに設定

.env の DEAL_LINE_CHANNEL_ACCESS_TOKEN を使用。
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".." / ".env"
IMG_PATH = "/tmp/deal_bot_richmenu.png"
# 太字の方がロゴらしく見えるため、Noto Sans CJK Boldがあれば優先する
# （なければ従来のIPAGothic Regularにフォールバック。TTCの0番目がJP面）
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_INDEX = 0
if not Path(FONT_PATH).exists():
    FONT_PATH = "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"
    FONT_INDEX = 0
    if not Path(FONT_PATH).exists():
        FONT_PATH = "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"

W, H = 2500, 1686
BUTTON_TEXT = "今日の目標を見る"

# 赤系ファイヤーカラー（固定。プロフィール画像からの抽出はしない。ユーザー指示で
# より明るい赤・爆発するような光り方に調整）
BG_CENTER = (232, 40, 24)     # 中心の明るい赤（グローの発光源）
BG_EDGE = (54, 4, 4)          # 外周の赤黒（暗すぎないよう底上げ）
FLAME_OUTER = (150, 14, 10)   # 炎の外側（赤）
FLAME_MID = (255, 90, 20)     # 炎の中間（赤〜オレンジ）
FLAME_CORE = (255, 214, 110)  # 炎の芯（明るい黄）
RAY_COLOR = (255, 150, 40)    # 爆発の光条
TEXT_GLOW = (255, 110, 30)

# 炎シルエット（頂点=上、揺らぎを右側に持たせた非対称の輪郭。単位座標: x=-1..1, y=0(頂点)..1(裾)）
FLAME_PTS = [
    (0.00, 1.00), (-0.40, 0.94), (-0.52, 0.74), (-0.40, 0.54),
    (-0.50, 0.36), (-0.30, 0.16), (-0.08, 0.05), (0.06, -0.03),
    (0.24, 0.09), (0.32, 0.27), (0.22, 0.42), (0.36, 0.58),
    (0.30, 0.78), (0.40, 0.95),
]


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


def radial_gradient(size: tuple, center_color: tuple, edge_color: tuple,
                     small: tuple = (250, 169)) -> Image.Image:
    """中心が明るく外周が暗い放射グラデーション（グロー演出の土台）。
    フル解像度でピクセル毎に計算すると遅いため、小さい画像で作ってから拡大する。
    """
    small_img = Image.new("RGB", small)
    px = small_img.load()
    cx, cy = small[0] / 2, small[1] / 2
    maxd = math.hypot(cx, cy)
    for y in range(small[1]):
        for x in range(small[0]):
            t = min(1.0, math.hypot(x - cx, y - cy) / maxd)
            px[x, y] = tuple(int(center_color[i] * (1 - t) + edge_color[i] * t) for i in range(3))
    return small_img.resize(size, Image.BICUBIC)


def flame_polygon(cx: int, base_y: int, width: int, height: int) -> list:
    return [(cx + x * width / 2, base_y - (1 - y) * height) for x, y in FLAME_PTS]


def draw_burst_rays(img: Image.Image, cx: int, cy: int, n: int = 16,
                     r_short: int = 300, r_long: int = 720) -> None:
    """爆発のような光条（スターバースト）。長短の三角形を交互に放射状に配置する。"""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    half_w = math.radians(5)
    for i in range(n):
        angle = 2 * math.pi * i / n
        length = r_long if i % 2 == 0 else r_short
        p1 = (cx, cy)
        p2 = (cx + length * math.cos(angle - half_w), cy + length * math.sin(angle - half_w))
        p3 = (cx + length * math.cos(angle + half_w), cy + length * math.sin(angle + half_w))
        ld.polygon([p1, p2, p3], fill=(*RAY_COLOR, 165))
    layer = layer.filter(ImageFilter.GaussianBlur(8))
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"), (0, 0))


def draw_flame(img: Image.Image, cx: int, base_y: int) -> None:
    burst_cy = base_y - 260

    # 爆発のような光条を先に敷き、その上に大きくぼかしたグロー（発光）を重ねる
    draw_burst_rays(img, cx, burst_cy)

    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.polygon(flame_polygon(cx, base_y + 30, 780, 900), fill=(255, 140, 30, 235))
    glow = glow.filter(ImageFilter.GaussianBlur(95))
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"), (0, 0))

    d = ImageDraw.Draw(img)
    d.polygon(flame_polygon(cx, base_y, 520, 620), fill=FLAME_OUTER)
    d.polygon(flame_polygon(cx, base_y - 40, 380, 490), fill=FLAME_MID)
    d.polygon(flame_polygon(cx, base_y - 95, 220, 310), fill=FLAME_CORE)


def draw_glow_text(img: Image.Image, text: str, font: ImageFont.FreeTypeFont,
                    cx: int, y: int, fill: tuple, glow: tuple, blur: int) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    tb = ld.textbbox((0, 0), text, font=font)
    x = cx - (tb[2] - tb[0]) / 2
    ld.text((x, y), text, font=font, fill=(*glow, 255))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"), (0, 0))
    ImageDraw.Draw(img).text((x, y), text, font=font, fill=fill)


def make_image() -> None:
    img = radial_gradient((W, H), BG_CENTER, BG_EDGE)

    cx = W // 2
    draw_flame(img, cx, base_y=1020)

    f_label = ImageFont.truetype(FONT_PATH, 132, index=FONT_INDEX)
    f_sub = ImageFont.truetype(FONT_PATH, 46, index=FONT_INDEX)

    draw_glow_text(img, BUTTON_TEXT, f_label, cx, 1120, fill=(255, 255, 255), glow=TEXT_GLOW, blur=18)

    sub = "TAP & FIRE UP YOUR DAY"
    d = ImageDraw.Draw(img)
    sb = d.textbbox((0, 0), sub, font=f_sub)
    d.text((cx - (sb[2] - sb[0]) / 2, 1290), sub, font=f_sub, fill=(255, 176, 110))

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

    make_image()

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
