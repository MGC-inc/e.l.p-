#!/usr/bin/env python3
"""「ユメイク営業分析bot」（DEAL_LINE_CHANNEL_ACCESS_TOKEN）に、役割別の
リッチメニューを2種類登録する。

- 全員向け（デフォルト）: 「今日の目標を見る」1ボタン
- クローザー用: 「今日の目標を見る」＋「直近の商談分析結果を見る」の2ボタン
  （アポインターは商談分析の対象外のため、こちらは表示しない）

タップされたテキストはline-webhook/api/webhook.py側のhandle_goal_request /
handle_analysis_requestが処理する。役割ごとの出し分けは、Supabase
closer_line_usersのroleが確定したタイミングでwebhook.py側が
`linkRichMenuToUser`を呼んで個別に切り替える（このスクリプトは初期登録と
既存クローザーへのバックフィルのみ担当）。

デザイン: 気合い・勢いを感じるファイヤー（爆発）モチーフ、イメージカラーは赤。
ノイズベースの爆発背景＋グロス調の円形ボタン（無地）。

処理:
  1. 2種類のメニュー画像を生成する
  2. 既存リッチメニューを全削除（idempotent）
  3. 2種類とも作成→画像アップロード。デフォルト用のみ全ユーザーの
     デフォルトに設定する
  4. --backfill 指定時、Supabase closer_line_usersのrole='closer'全員に
     クローザー用メニューを個別リンクする（既存登録者向けの一括反映）

.env の DEAL_LINE_CHANNEL_ACCESS_TOKEN・ELP_SUPABASE_URL・
ELP_SUPABASE_SERVICE_ROLE_KEY を使用（--backfillのみSupabaseが必要）。
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".." / ".env"
DEFAULT_IMG_PATH = "/tmp/deal_bot_richmenu_default.jpg"
CLOSER_IMG_PATH = "/tmp/deal_bot_richmenu_closer.jpg"
# LINEのリッチメニュー画像は1MB上限。ノイズ主体の写真調画像はPNGだと余裕で超えるため
# JPEGで保存する（qualityは1MBに収まる範囲で自動的に下げる）

# 太字の方がロゴらしく見えるため、Noto Sans CJK Boldがあれば優先する
# （なければ従来のIPAGothic Regularにフォールバック。TTCの0番目がJP面）
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_INDEX = 0
if not Path(FONT_PATH).exists():
    FONT_PATH = "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"
    if not Path(FONT_PATH).exists():
        FONT_PATH = "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"

W, H = 2500, 1686
GOAL_TEXT = "今日の目標を見る"
ANALYSIS_TEXT = "直近の商談分析結果を見る"

DEFAULT_MENU_NAME = "営業分析BOT 今日の目標"
CLOSER_MENU_NAME = "営業分析BOT クローザー用"

# 赤系ファイヤーカラー（固定。プロフィール画像からの抽出はしない）
TEXT_GLOW = (255, 110, 30)

# 爆発の炎色グラデーション（0=明るめの赤 → 1=白熱に近い黄）。ノイズベースの爆発背景で使う
FIRE_STOPS = [
    (0.00, (74, 10, 10)),
    (0.22, (140, 16, 14)),
    (0.45, (214, 46, 18)),
    (0.66, (255, 104, 28)),
    (0.85, (255, 196, 96)),
    (1.00, (255, 248, 214)),
]

# 中央の「ボタン」（グロス調の円形ボタン。押せそうな見た目にする。中は無地）
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
        "Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def sb_get(env: dict, path: str) -> list:
    url = f"{env['ELP_SUPABASE_URL']}/rest/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": env["ELP_SUPABASE_SERVICE_ROLE_KEY"],
        "Authorization": f"Bearer {env['ELP_SUPABASE_SERVICE_ROLE_KEY']}",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


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


def make_explosion_bg(width: int, height: int, cx: int, cy: int, seed_offset: int = 0) -> Image.Image:
    """写真のような質感の爆発（フォトリアル寄り）を、放射状の距離場に乱流ノイズを
    重ねて生成する。ポリゴンの光条は使わず、輪郭・濃淡ともに不規則にする。
    """
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    dx = (xx - cx) / (width * 0.62)
    dy = (yy - cy) / (height * 0.62)
    r = np.sqrt(dx * dx + dy * dy)

    detail = _value_noise(width, height, octaves=5, seed=11 + seed_offset)
    turbulence = _value_noise(width, height, octaves=3, seed=29 + seed_offset)

    # 中心ほど明るく、ノイズで輪郭を不規則にゆらす（きれいな円にしない）
    intensity = 1.05 - r + (detail - 0.5) * 0.6 + (turbulence - 0.5) * 0.25
    intensity = np.clip(intensity, 0, 1) ** 1.5

    rgb = np.clip(_fire_colormap(intensity), 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


def draw_button(img: Image.Image, cx: int, cy: int, r: int) -> None:
    """押せそうな見た目のグロス調の円形ボタン（無地）。
    ドロップシャドウ→本体→縁取り→光沢ハイライトの順に重ねる。"""
    # ドロップシャドウ
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(
        [cx - r - 10, cy - r + 22, cx + r + 10, cy + r + 34], fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(30))
    img.paste(Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB"), (0, 0))

    # 本体（左上が明るく右下が暗いグラデーションで立体感を出す）
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


def make_default_image() -> None:
    """全員向けデフォルト: 「今日の目標を見る」1ボタン。"""
    cx, cy = W // 2, 760
    img = make_explosion_bg(W, H, cx, cy)
    draw_button(img, cx, cy, r=300)

    f_label = ImageFont.truetype(FONT_PATH, 132, index=FONT_INDEX)
    f_sub = ImageFont.truetype(FONT_PATH, 46, index=FONT_INDEX)

    draw_glow_text(img, GOAL_TEXT, f_label, cx, 1190, fill=(255, 255, 255), glow=TEXT_GLOW, blur=18)

    sub = "TAP & FIRE UP YOUR DAY"
    d = ImageDraw.Draw(img)
    sb = d.textbbox((0, 0), sub, font=f_sub)
    d.text((cx - (sb[2] - sb[0]) / 2, 1360), sub, font=f_sub, fill=(255, 176, 110))

    save_under_1mb(img, DEFAULT_IMG_PATH)


def make_closer_image() -> None:
    """クローザー用: 左右2分割で「今日の目標を見る」「直近の商談分析結果を見る」。"""
    half = W // 2
    left = make_explosion_bg(half, H, half // 2, 760, seed_offset=0)
    right = make_explosion_bg(half, H, half // 2, 760, seed_offset=100)
    img = Image.new("RGB", (W, H))
    img.paste(left, (0, 0))
    img.paste(right, (half, 0))

    # 中央の区切り線（うっすら）
    ImageDraw.Draw(img).line([(half, 0), (half, H)], fill=(40, 4, 4), width=4)

    cy = 760
    draw_button(img, half // 2, cy, r=230)
    draw_button(img, half + half // 2, cy, r=230)

    f_label = ImageFont.truetype(FONT_PATH, 78, index=FONT_INDEX)

    for cx, text in ((half // 2, GOAL_TEXT), (half + half // 2, ANALYSIS_TEXT)):
        d = ImageDraw.Draw(img)
        tb = d.textbbox((0, 0), text, font=f_label)
        tw = tb[2] - tb[0]
        if tw > half - 80:
            # 収まらない場合は少し小さいフォントで再計測する
            f_label2 = ImageFont.truetype(FONT_PATH, 62, index=FONT_INDEX)
            draw_glow_text(img, text, f_label2, cx, 1130, fill=(255, 255, 255), glow=TEXT_GLOW, blur=14)
        else:
            draw_glow_text(img, text, f_label, cx, 1100, fill=(255, 255, 255), glow=TEXT_GLOW, blur=14)

    save_under_1mb(img, CLOSER_IMG_PATH)


def save_under_1mb(img: Image.Image, path: str, limit: int = 950_000) -> None:
    """LINEのリッチメニュー画像は1MB上限。1MBを切るまでJPEG qualityを下げて保存する。"""
    quality = 90
    while quality >= 40:
        img.save(path, "JPEG", quality=quality, optimize=True)
        size = os.path.getsize(path)
        if size <= limit:
            print(f"image saved: {path} ({size} bytes, quality={quality})")
            return
        quality -= 10
    print(f"image saved: {path} ({os.path.getsize(path)} bytes, quality={quality}) — 1MB超の可能性あり")


def register_menu(token: str, name: str, chat_bar_text: str, areas: list, image_path: str) -> str:
    menu = {
        "size": {"width": W, "height": H},
        "selected": True,
        "name": name,
        "chatBarText": chat_bar_text,
        "areas": areas,
    }
    res = api(token, "POST", "richmenu", menu)
    rid = res["richMenuId"]
    print(f"created richmenu: {name} -> {rid}")
    status = api_data_upload(token, rid, open(image_path, "rb").read())
    print(f"  image upload status: {status}")
    return rid


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image-only", action="store_true",
                     help="画像生成のみ行い、LINEへの登録は行わない（確認用）")
    ap.add_argument("--backfill", action="store_true",
                     help="登録後、Supabase closer_line_usersのrole='closer'全員に"
                          "クローザー用メニューを個別リンクする")
    args = ap.parse_args()

    env = load_env()
    token = env.get("DEAL_LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        raise SystemExit("環境変数 DEAL_LINE_CHANNEL_ACCESS_TOKEN が未設定です。")

    make_default_image()
    make_closer_image()

    if args.image_only:
        print("--image-only のためLINEへの登録は行いません。")
        return

    existing = api(token, "GET", "richmenu/list").get("richmenus", [])
    for rm in existing:
        api(token, "DELETE", f"richmenu/{rm['richMenuId']}")
        print(f"deleted old richmenu {rm['richMenuId']}")

    default_id = register_menu(
        token, DEFAULT_MENU_NAME, "今日の目標",
        [{"bounds": {"x": 0, "y": 0, "width": W, "height": H},
          "action": {"type": "message", "text": GOAL_TEXT}}],
        DEFAULT_IMG_PATH,
    )
    closer_id = register_menu(
        token, CLOSER_MENU_NAME, "メニュー",
        [
            {"bounds": {"x": 0, "y": 0, "width": W // 2, "height": H},
             "action": {"type": "message", "text": GOAL_TEXT}},
            {"bounds": {"x": W // 2, "y": 0, "width": W - W // 2, "height": H},
             "action": {"type": "message", "text": ANALYSIS_TEXT}},
        ],
        CLOSER_IMG_PATH,
    )

    api(token, "POST", f"user/all/richmenu/{default_id}")
    print("set as default richmenu ✅ (全員)")
    print(f"クローザー用メニューID: {closer_id}（webhook.py がrole確定時に個別リンクする）")

    if args.backfill:
        if not env.get("ELP_SUPABASE_URL") or not env.get("ELP_SUPABASE_SERVICE_ROLE_KEY"):
            print("ELP_SUPABASE_URL/ELP_SUPABASE_SERVICE_ROLE_KEY が未設定のためbackfillをスキップします。")
            return
        closers = sb_get(env, "closer_line_users?role=eq.closer&select=line_user_id,closer_name")
        ok, ng = [], []
        for row in closers:
            uid = row.get("line_user_id")
            try:
                api(token, "POST", f"user/{uid}/richmenu/{closer_id}")
                ok.append(row.get("closer_name"))
            except Exception as e:  # noqa: BLE001 - 1人の失敗で他を止めない
                ng.append((row.get("closer_name"), str(e)))
        print(f"バックフィル完了: 成功{len(ok)}人 {ok}")
        if ng:
            print(f"失敗: {ng}")


if __name__ == "__main__":
    main()
