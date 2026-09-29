# tools/shuuji — 週次MT用PDF（Phase 1: 決定的集計ロジック＋PDF生成）

Notionエージェント（`/shuuji`コマンド、2026-09-29付で廃止）に代わる、**決定的コード**（Python）による
週次MT資料の生成。詳細な設計・全体像は [`../../商談分析運用.md`](../../商談分析運用.md) を参照。

**Phase 1の範囲はここまで**: 集計・判定ロジックとPDF生成を、サンプルデータ（`sample_data.py`固定値）で
検証する。Notion本番データへの接続・新規DB作成・GitHub Actions・LINE配信はPhase 2以降（未着手）。

## 要件

```
pip install reportlab
```

日本語フォントはreportlab組み込みのCIDフォント（HeiseiKakuGo-W5／HeiseiMin-W3）を使うため、
上記以外に外部フォントファイル・システムパッケージのインストールは不要（GitHub Actions等
どの環境でも`pip install reportlab`だけで日本語PDFが出る）。

## 使い方（サンプルデータでの検証）

```bash
python3 -m tools.shuuji.render_sample
```

- 判定ロジック（平均比の計算・70/90/110%の閾値・クーリングオフ率の1.5/1.2倍閾値）を、
  手計算した期待値とassertで突き合わせる
- 代理店別PDF（`out/weekly_<代理店>_<週初日>.pdf`）と全社MT用PDF（`out/meeting_<週初日>.pdf`）を生成し、
  ページ数（人数×2＋共通4ページ／全社MT用は2ページ固定）を検証する
- 全チェックが通れば「全チェック成功。」と表示される

## ファイル構成

- `models.py` — データの入れ物（DailyRecord・DealRecord・MemberWeekStats等）。Notion本番接続時も
  そのまま使う想定（Phase 2でNotion REST APIの生データをこれらに詰め替えるだけにする）
- `aggregate.py` — 集計・判定ロジック本体（依頼の3章をそのまま実装）。LLMを一切使わない決定的コード
- `pdf_agency.py` — 成果物A（代理店別配信PDF、A4縦）
- `pdf_meeting.py` — 成果物B（全社MT画面共有用、16:9横×2枚）
- `pdf_common.py` — フォント登録・色・判定ラベルの色分けなど共通部品
- `sample_data.py` — Phase 1検証用の固定サンプルデータ（Notion接続なし）
- `render_sample.py` — 上記を一気通貫で動かすCLI

## 判定ロジックの要点（aggregate.py）

- 比較対象は同じ役割（アポインター／クローザー）全員。**通過率は分子・分母を全員分合算して計算**
  （個々の率の単純平均ではない）
- 判定閾値: 平均比70%未満=大幅に低い／90%未満=やや低い／110%超=好調／それ以外=平均
- クーリングオフ率・審査落ち率は「少ない方が良い」項目として別ロジックで判定
  （平均の1.5倍超=要注意／1.2倍超=やや多い）。**実数（クーリングオフ数・審査落ち数）自体には
  判定を付けない**（参考表示のみ。件数が少ない＝良いことなので「大幅に低い」等の判定を付けると誤解を招くため）
- 分母3件未満の率、平均が0の項目は判定しない
- 自動コメントは「訪問→アポ通算」「商談→契約率(計)」（複合項目）を候補から除外する

## 既知の制約・Phase 2以降でやること

- Notion実績DB・商談分析DB・予算DBからのREST API読み取り（`weekly_goal_push.py`と同じ枯れた方式）
- 週次設定DB・週次PDCA記録DB・配信ログDBの新規作成
- GitHub Actions（毎週水曜9:00 JST）・Supabase Storageへの公開・LINE配信
- `/shuuji` `/shuuji send` `/shuuji check` `/shuuji review` のsales-commandsスキル化
