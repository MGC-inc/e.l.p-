# tools/shuuji — 週次MT用PDF（代理店別配信・全社MT用）

Notionエージェント（`/shuuji`コマンド、2026-09-29付で廃止）に代わる、**決定的コード**（Python、LLM不使用）
による週次MT資料の生成・公開・LINE配信。詳細な設計・全体像は
[`../../商談分析運用.md`](../../商談分析運用.md) セクション8を参照。

**今川さんから提供された「LINE配信キット」（[`../../reference/line-kit/`](../../reference/line-kit/)）を
そのまま移植したもの**。PDF生成・判定ロジック・LINE配信の本体（`build_all.py`／`build_meeting.py`／
`deliver.py`）はキットの実装をほぼそのまま使い、Notion／Supabaseに接続する部分（`notion_to_csv.py`）
だけを新規に書いた（キットは元々Googleスプレッドシート前提だったため）。

## 全体の流れ

```
notion_to_csv.py  (Notion 3DB + Supabase → members/results/config の3CSV)
        ↓
build_all.py      (代理店別配信PDF。人数×2ページ＋共通4ページ、ランダムファイル名)
build_meeting.py  (全社MT用PDF、16:9×2枚)
        ↓
publish_supabase.py  (Supabase Storageに公開。バケット名 weekly-pdca)
        ↓
deliver.py         (代理店の全員へLINE配信。--test-toで今川さんだけにテスト送信可)
deliver_meeting.py  (全社MT用PDFを今川さんだけに送信)
```

毎週水曜9:00 JSTの無人実行は [`../../.github/workflows/weekly-shuuji.yml`](../../.github/workflows/weekly-shuuji.yml)
が行う（Claude／LLMを一切経由しない。現時点では安全のため手動実行=`workflow_dispatch`のみ有効。
scheduleを有効化する手順は運用.mdを参照）。

## 要件

```
pip install reportlab matplotlib
sudo apt-get install -y fonts-ipafont-gothic   # 日本語フォント（matplotlibのグラフ用）
```

## ローカルでの試し方（サンプルデータ、Notion接続なし）

```bash
cd tools/shuuji
python3 build_all.py sample /tmp/out
python3 build_meeting.py sample /tmp/out/meeting.pdf
python3 deliver.py /tmp/out "https://公開URL" --dry-run
```

## Notion本番データで試す（読み取りのみ・送信なし）

```bash
cd tools/shuuji
python3 notion_to_csv.py /tmp/notion_out   # NOTION_TOKEN・ELP_SUPABASE_URL・ELP_SUPABASE_SERVICE_ROLE_KEYが必要
python3 build_all.py /tmp/notion_out /tmp/out --strict
python3 build_meeting.py /tmp/notion_out /tmp/out/meeting.pdf
python3 deliver.py /tmp/out "https://公開URL" --dry-run
```

## ファイル構成

- `notion_to_csv.py` — **新規実装**。Notion（📉営業部実績DB・DB商談分析＆アポ分析・💰予算と達成率）と
  Supabase（`closer_line_users`）から、キットが読める3CSVを生成する
- `build_all.py` — 代理店別配信PDF生成（キットそのまま。ランダムファイル名で推測不可に）
- `build_meeting.py` — 全社MT用PDF生成（キットそのまま）
- `deliver.py` — LINE配信（キットを元に、環境変数名を`DEAL_LINE_CHANNEL_ACCESS_TOKEN`に統一し、
  `--test-to <line_user_id>`（1人だけにテスト送信）を追加）
- `deliver_meeting.py` — **新規実装**。全社MT用PDFを今川さんだけに送る
- `publish_supabase.py` — **新規実装**。生成したPDFをSupabase Storageに公開する
  （キット原案のVercelではなくSupabase Storageを使う理由は運用.md参照）
- `sample/` — キット付属のサンプルCSV（動作確認用）

## 判定ロジックの要点（build_all.py内）

- 比較対象は同じ役割（アポインター／クローザー）全員。通過率は分子・分母を全員分合算して計算
- 判定閾値: 平均比70%未満=大幅に低い／90%未満=やや低い／110%超=好調／それ以外=平均
- クーリングオフ率・審査落ち率は「少ない方が良い」項目として別ロジックで判定
  （平均の1.5倍超=要注意／1.2倍超=やや多い）。実数自体は「参考」表示のみで判定しない
- 分母3件未満の率、平均が0の項目は判定しない
- 自動コメントは「訪問→アポ率（通算）」「商談→契約率（計）」（複合項目）を候補から除外する

## 仮置きにした点（要確認）

- メンバーの絞り込み: 💰予算と達成率DBの当月行（代理店・予算が入っている）と、Supabaseの
  `closer_line_users`（role・line_user_id登録済み）の両方に存在する人だけを対象にしている。
  予算はあるがLINE未登録の人などは対象外（実行時に標準エラー出力に一覧が出る）
- 代理店ごとの「管理者」フラグ（is_manager）に相当するデータが無いため、一律`no`にしている
  （`--managers-only`は今のところ機能しない。人手で決めるなら予算DB等に管理者フラグを追加する必要がある）
- 月間契約目標（社内合計）は、月間予算合計 ÷ 65万円（1件あたりの目安単価。webhook.py等
  既存の運用で使われている値を踏襲）で算出している
- 対象週は「月曜始まり・日曜終わり」（依頼の指定どおり。週次実績配信スキルの
  「水曜始まり火曜終わり」とは別軸）
