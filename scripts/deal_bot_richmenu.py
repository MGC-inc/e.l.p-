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

import numpy as np
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

# 赤系ファイヤーカラー（固定。プロフィール画像からの抽出はしない）
TEXT_GLOW = (255, 110, 30)

# 爆発の炎色グラデーション（0=明るめの赤 → 1=白熱に近い黄）。ノイズベースの爆発背景で使う
# ユーザー指示で全体的に明るい赤系に調整（黒に近い暗部を無くす）
FIRE_STOPS = [
    (0.00, (74, 10, 10)),
    (0.22, (140, 16, 14)),
    (0.45, (214, 46, 18)),
    (0.66, (255, 104, 28)),
    (0.85, (255, 196, 96)),
    (1.00, (255, 248, 214)),
]

# 中央の「ボタン」（グロス調の円形ボタン。押せそうな見た目にする。中は無地）
BUTTON_RADIUS = 300
BUTTON_HIGHLIGHT = (255, 140, 110)
BUTTON_SHADOW_COLOR = (204, 24, 20)
BUTTON_RIM = (255, 232, 200)


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


def _value_noise(width: int, height: int, octaves: int = 5, seed: int = 7) -> np.ndarray:
    """複数解像度のランダムグリッドを重ねた乱流ノイズ（0..1）。
    フラットなベクター感を消し、爆発らしい不規則さを出すために使う。
    """
    rng = np.random.default_rng(seed)
    acc = np.zeros((height, width), dtype=np.float32)
    amp, total, res = 1.0, 0.0, 5
    for _ in range(octaves):
        small = (rng.random((res, res)) * 255).astype(np.uint8)
        big = np.asarray(
            Image.fromarray(small, "L").resize((width, height), Image.BICUBIC),
            dtype=np.float32,
        ) / 255.0
        acc += amp * big
        total += amp
        amp *= 0.55
        res *= 2
    return acc / total


def _fire_colormap(intensity: np.ndarray) -> np.ndarray:
    h, w = intensity.shape
    out = np.zeros((h, w, 3), dtype=np.float32)
    for (t0, c0), (t1, c1) in zip(FIRE_STOPS, FIRE_STOPS[1:]):
        mask = (intensity >= t0) & (intensity <= t1)
        local_t = np.clip((intensity - t0) / (t1 - t0), 0, 1)
        for ch in range(3):
            out[..., ch] = np.where(mask, c0[ch] + (c1[ch] - c0[ch]) * local_t, out[..., ch])
    return out


def make_explosion_bg(width: int, height: int, cx: int, cy: int) -> Image.Image:
    """写真のような質感の爆発（フォトリアル寄り）を、放射状の距離場に乱流ノイズを
    重ねて生成する。ポリゴンの光条は使わず、輪郭・濃淡ともに不規則にする。
    """
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    dx = (xx - cx) / (width * 0.62)
    dy = (yy - cy) / (height * 0.62)
    r = np.sqrt(dx * dx + dy * dy)

    detail = _value_noise(width, height, octaves=5, seed=11)
    turbulence = _value_noise(width, height, octaves=3, seed=29)

    # 中心ほど明るく、ノイズで輪郭を不規則にゆらす（きれいな円にしない）
    intensity = 1.05 - r + (detail - 0.5) * 0.6 + (turbulence - 0.5) * 0.25
    intensity = np.clip(intensity, 0, 1) ** 1.5

    rgb = np.clip(_fire_colormap(intensity), 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


def draw_button(img: Image.Image, cx: int, cy: int) -> None:
    """押せそうな見た目のグロス調の円形ボタン（無地）。
    ドロップシャドウ→本体→縁取り→光沢ハイライトの順に重ねる。"""
    r = BUTTON_RADIUS

    # ドロップシャドウ
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(
        [cx - r - 10, cy - r + 22, cx + r + 10, cy + r + 34], fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(30))
    img.paste(Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB"), (0, 0))

    # 本体（左上が明るく右下が暗いグラデーションで立体感を出す）
    body = Image.new("RGB", (r * 2, r * 2))
    bx = np.linspace(-1, 1, r * 2, dtype=np.float32)
    gx, gy = np.meshgrid(bx, bx)
    t = np.clip((gx * -0.7 + gy * -0.7 + 1) / 2, 0, 1) ** 1.3
    body_arr = np.zeros((r * 2, r * 2, 3), dtype=np.float32)
    for ch in range(3):
        body_arr[..., ch] = BUTTON_HIGHLIGHT[ch] * t + BUTTON_SHADOW_COLOR[ch] * (1 - t)
    body = Image.fromarray(np.clip(body_arr, 0, 255).astype(np.uint8), "RGB")
    mask = Image.new("L", (r * 2, r * 2), 0)
    ImageDraw.Draw(mask).ellipse([2, 2, r * 2 - 2, r * 2 - 2], fill=255)
    img.paste(body, (cx - r, cy - r), mask)

    # 縁取り
    ImageDraw.Draw(img).ellipse([cx - r, cy - r, cx + r, cy + r], outline=BUTTON_RIM, width=8)

    # 光沢ハイライト（左上）
    hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    hr = r * 0.5
    ImageDraw.Draw(hl).ellipse(
        [cx - r * 0.45 - hr, cy - r * 0.5 - hr * 0.6, cx - r * 0.45 + hr, cy - r * 0.5 + hr * 0.6],
        fill=(255, 255, 255, 110))
    hl = hl.filter(ImageFilter.GaussianBlur(24))
    img.paste(Image.alpha_composite(img.convert("RGBA"), hl).convert("RGB"), (0, 0))


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
    cx, cy = W // 2, 760
    img = make_explosion_bg(W, H, cx, cy)
    draw_button(img, cx, cy)

    f_label = ImageFont.truetype(FONT_PATH, 132, index=FONT_INDEX)
    f_sub = ImageFont.truetype(FONT_PATH, 46, index=FONT_INDEX)

    draw_glow_text(img, BUTTON_TEXT, f_label, cx, 1190, fill=(255, 255, 255), glow=TEXT_GLOW, blur=18)

    sub = "TAP & FIRE UP YOUR DAY"
    d = ImageDraw.Draw(img)
    sb = d.textbbox((0, 0), sub, font=f_sub)
    d.text((cx - (sb[2] - sb[0]) / 2, 1360), sub, font=f_sub, fill=(255, 176, 110))

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
