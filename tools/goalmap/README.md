# tools/goalmap — ゴールマップ図解レンダラー

各メンバー1人分の目標データ（JSON）から、「下から上へ登る地図」の図解（SVG / PNG）を生成する。
週次MTGの運用ルールは [`../../ゴールマップ運用.md`](../../ゴールマップ運用.md) を参照。

## 使い方

```bash
# 1人分を生成（out/<名前>.svg と .png を出力）
python tools/goalmap/generate_goalmap.py tools/goalmap/members/岡野.json

# 出力先を指定
python tools/goalmap/generate_goalmap.py tools/goalmap/members/岡野.json -o /tmp/okano

# 標準入力から
cat data.json | python tools/goalmap/generate_goalmap.py -
```

- **SVG** は常に出力（ブラウザで開ける／印刷・PDF化も可）。フォントはMac/Win/Linux向けのフォールバック順で埋め込む。
- **PNG** は `cairosvg` があれば出力（`pip install cairosvg`）。日本語ラスタライズには CJK フォントが必要：
  - Ubuntu: `apt-get install -y fonts-noto-cjk`
  - PNG描画時はインストール済みの `Noto Sans CJK JP` を先頭に置いた版で描画する（cairoのCJKフォールバック対策）。

## データモデル（`members/<名前>.json`）

```jsonc
{
  "name": "岡野",
  "note": "wanny",                       // 肩書/状況（任意）
  "theme": "契約を安定させる（クローザー）",
  "goal": "客層を問わず自分の型で契約まで運び、成約が安定する",  // 観察できる状態で書く
  "why": "…（任意）",
  "currentStage": 2,                       // 1..5（今ここ）
  "phases": [                              // 必ず5要素。index0=①(下) … index4=⑤(上)
    { "name": "型を知る", "doneDef": "勝ち負けを言語化できる",
      "tasks": [ { "name": "…", "done": true } ] }
  ]
}
```

- **達成率 ＝ `done: true` のタスク数 ÷ 全タスク数 ×100**（自動計算）。
- 時間軸ラベル（クリア/今/＋N週/達成）は `currentStage` から自動。
- `members/_template.json` を雛形にする。

## 図解の確定仕様（厳守）

座標系 viewBox `0 0 680 H`（H可変）。上→下に goal, ⑤, ④, ③, ②, ① の順、矢印は上向き。

| 要素 | 仕様 |
|---|---|
| ヘッダー | 左：`名前（補足）｜テーマ` ／ 右：達成率%＋バー |
| ゴール箱 | 最上部・パープル・2px枠。「ゴール」ラベル＋ゴール文 |
| フェーズ箱 | x=130, w=210。フェーズ名＋「完了：〜」。左に状態マーカー円。高さ=`max(60, 24+18×タスク数)` |
| 時間軸ピル | 左 x=20, w=58。クリア/今/＋N週/達成 |
| タスク | 箱の右 x=360〜。チェックボックス＋タスク名。済=塗り＋チェック＋取り消し線 |
| 色 | 済=#1D9E75 / 今=#EF9F27 / これから=#9AA3AD / ゴール=#6357CC |
| フォント | Hiragino Sans → Noto Sans CJK JP → Yu Gothic フォールバック |

## Notion との対応

実データは Notion の 🧭 目標マップ DB / ✅ タスク DB（[`../../ゴールマップ運用.md`](../../ゴールマップ運用.md) §4）。
Notion側のフェーズ「完了の定義」は保存されないため、`doneDef` はこの JSON 側で持つ。

| JSON | Notion 目標マップ |
|---|---|
| `name` | メンバー(select) |
| `theme` | テーマ(title) |
| `goal` | ゴール（観察できる状態）(rich_text) |
| `why` | なぜ今これか(rich_text) |
| `currentStage` | 現在ステージ(select ①..⑤) |
| `phases[].tasks[]` | ✅ タスク DB（ステージ select、ステータス status＝完了で `done:true`） |
