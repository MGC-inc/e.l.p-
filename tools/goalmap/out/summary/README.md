# out/summary — 週次の1枚カード（スマホ・LINE共有用）

`summary_cards.py` で全員分を書き出す置き場。ファイルは `<名前>.{svg,png}`。
スマホ縦サイズに、ゴール・小アバター・現在地・**今週の最優先タスク**・全タスク✓を凝縮した1枚。

```bash
python tools/goalmap/summary_cards.py          # 全員 → out/summary/
python tools/goalmap/summary_cards.py 岡野 宮腰  # 指定メンバーのみ
```

- studio(goalmap-studio.html) の「📱 1枚表示 →（任意で）PNG出力」と同じ1枚。
- **生成物（*.png / *.svg）は毎週作り直す前提**なので、基本コミット不要（必要な週だけ残す）。
- PNGは cairosvg があれば出力（`pip install cairosvg` ＋ CJKフォント）。
