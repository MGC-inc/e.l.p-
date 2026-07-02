# out/archive — 周回アーカイブ（過去の達成地図）

ゴールを達成して次のゴールへ進む前に、その周回の最終ゴールマップをここに残す。
ファイル名は `<名前>_cycle<N>.{json,svg,png}`（N＝何周目）。

- **PNG**：そのまま開いて見返せる達成地図（朝礼/LINE/Notion貼付にも使える）。
- **SVG**：拡大しても綺麗な版。ブラウザで開ける。
- **JSON**：その周回の完全データ（どのタスクを達成したか／ゴール文／ステージ）。studioに読み込めばその時点を再現できる。

## 残し方
- **studio**：「🎉 ゴール達成 → 次のゴールへ」を押すと PNG/JSON が書き出されるので、この `out/archive/` に保存してコミット。
- **CLI**：`python tools/goalmap/archive_cycle.py tools/goalmap/members/<名前>.json`

コミットすれば GitHub 上で誰でも過去の達成地図を見返せる（＝リポジトリが履歴台帳）。
