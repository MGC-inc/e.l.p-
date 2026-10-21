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
notion_to_csv.py  (Notion 4DB + Supabase → members/results/config/pdca の4CSV)
        ↓
build_all.py      (代理店別配信PDF。人数×2ページ＋共通4ページ、ランダムファイル名。
                    ⑤先週の振り返りページに、pdca.csvの前回アクションプランを自動転記)
build_slides.py   (週次MTで映すスライド。代理店別・16:9。宿題の答え合わせ・全社ランキング・
                    宣言達成者・個人スライド・みんなの学び・BOTへの送り方。進行はマニュアル/週次MT.md)
build_meeting.py  (全社MT用PDF、16:9×2枚)
        ↓
publish_supabase.py  (Supabase Storageに公開。バケット名 weekly-pdca)
        ↓
deliver.py         (代理店の全員へPDFリンク＋週次PDCA記入テンプレをLINE配信。
                    --test-toで今川さんだけにテスト送信可。本番配信時のみSupabaseの
                    pdca_pending_weekをセットする)
deliver_meeting.py  (全社MT用PDFを今川さんだけに送信)
        ↓
（本人がLINEでテンプレに記入して返信）
        ↓
line-webhook/api/webhook.py の handle_pdca_reply が受け取り、
週次PDCA記録DBに保存 → pdca_pending_weekをNULLに戻す → 翌週のnotion_to_csv.py実行時に
pdca.csvへ反映される
        ↓
（3日後の土曜、まだpdca_pending_weekが残っている＝未回答の人だけに）
pdca_reminder.py    (記入テンプレのリマインドを1通LINE配信。.github/workflows/
                    weekly-pdca-reminder.yml が土曜9:00 JSTに自動実行)
```

**2026-10追記: 未回答者リマインドを追加した経緯**: 実データで、配信は全員にHTTP 200で
届いている（LINEのInsight API・プロフィール確認でブロックもなしと確認済み）のに、
週次PDCA記録DBへの記入は15人中7人程度しか来ていないことが判明。「送られていない」
ように見えるが実際は「届いているが記入されていない」が主因だったため、記入を促す
`pdca_reminder.py`を追加した（Notionを見に行かず、`pdca_pending_week`が配信時のまま
残っているか＝未回答かだけで判定する。line-webhook側が返信を受け取るとこの列をNULLに
戻す実装のため）。

**週次PDCA記入テンプレの仕組み**: PDFと一緒に「①なぜ低いと思うか（原因）／②今週何をするか
（アクションプラン）／③今週の目標（数字で。訪問数・アポ数・商談数・契約数。クローザーは
商談数・契約数を自アポ/他アポ別に）」の3問のテンプレを送り、本人がコピーして返信する
（③の項目は`deliver.py`が役割ごとに出し分ける）。返信は本番LINE Bot（line-webhook）側で
受け取り、Notion「📝 週次PDCA記録DB」に保存する。翌週のPDF生成時に`notion_to_csv.py`が
前回分を拾い、「⑤先週アクションの振り返り」の「先週決めたアクション」欄に、アクション文と
「【目標】訪問60・アポ12・商談5+3・契約2+1」の形で数値目標も自動で入る（「やった？」
「次にどうする」は当日のMTで手書き。数字の結果はPDF内の他の表に既に出ているため、
テンプレでは聞かない。ユーザー指示）。

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

- `notion_to_csv.py` — **新規実装**。Notion（📉営業部実績DB・DB商談分析＆アポ分析・💰予算と達成率・
  📝週次PDCA記録DB）とSupabase（`closer_line_users`）から、キットが読める4CSVを生成する
- `build_all.py` — 代理店別配信PDF生成（キットをベースに、⑤振り返りページをpdca.csvで
  自動転記するよう変更。ランダムファイル名で推測不可に）
- `build_slides.py` — **新規実装**。週次MTで画面に映す代理店別スライド（16:9）。`slides_manifest.csv`に
  代理店→ファイル名を書き、`deliver.py`がそのリンクを配信メッセージに入れる。pdca.csvの`cause`
  （LINE返信の①先週の振り返り・学び。Notionの列名は旧称の「原因」）を「みんなの学び」に出す
- `build_meeting.py` — 全社MT用PDF生成（キットそのまま）
- `deliver.py` — LINE配信（キットを元に、環境変数名を`DEAL_LINE_CHANNEL_ACCESS_TOKEN`に統一し、
  `--test-to <line_user_id>`（1人だけにテスト送信）と、週次PDCA記入テンプレの同時送信・
  Supabase `pdca_pending_week`の更新を追加）
- `deliver_meeting.py` — **新規実装**。全社MT用PDFを今川さんだけに送る
- `publish_supabase.py` — **新規実装**。生成したPDFをSupabase Storageに公開する
  （キット原案のVercelではなくSupabase Storageを使う理由は運用.md参照）
- `pdca_reminder.py` — **新規実装**。週次PDCAに未回答のまま数日経った人へ記入テンプレの
  リマインドを送る（`.github/workflows/weekly-pdca-reminder.yml`が土曜9:00 JSTに自動実行）
- `sample/` — キット付属のサンプルCSV（動作確認用。`pdca.csv`は無くてもエラーにならず
  「（記録なし）」表示になる）

## 数字の数え方と確認（2026-10-20、ユーザー指摘「スライドの数字がいい加減」で全面見直し）

数字の正は次のとおり。どの資料（代理店別PDF・全社MT用PDF・MTスライド）も同じ値になる。

| 数字 | 出典 | 数え方 |
|---|---|---|
| 訪問・アポ（個人・代理店・全社） | 📉実績DB | 期間の合計（休みの日の行は除く） |
| 売上（個人） | 📉実績DB | 実質売上＝契約金額−キャンセル金額。他アポは半額ずつ入っているので本人の取り分。💰予算と達成率の実績と一致 |
| 売上（代理店・全社） | 📉実績DB | 全行の実質売上の合計（半額ずつの配分なので全行の合計＝会社の売上）。代理店は実績DBの所属 |
| 商談・契約（個人） | 商談一覧 | クローザーは自アポ/他アポ別、アポインターは自分がアポを取った件 |
| 商談・契約（代理店・全社） | 商談一覧 | **1件1回**（クローザーの所属で数える）。契約は結果＝契約のみ（クーリングオフになった分は入らない） |
| 月の実績 | 同上 | 対象週の日曜が属する月の1日〜その日曜（週と同じ締め日） |
| 月の売上目標 | 💰予算と達成率 | 当月の全行の予算の合計 |
| 月の契約目標 | 同上＋商談一覧 | 売上目標 ÷ 全社の平均契約単価（前月までの3か月。月初予算チェックと同じ計算） |
| 週の目安 | 月目標 | 月目標 ÷ 月の日数 × 先週のうち当月の日数 |

以前は、個人の数字を足して代理店・全社の合計にしていたため、1件の契約がクローザーとアポインターの両方で数えられ、
契約・売上が二重になっていた（10/12〜10/18の全社契約が13件・売上1,939万と、月の実績より多く出ていた）。
今は`notion_to_csv.py`の`build_totals`が合計を別に数えて`totals.csv`に出し、`build_all.agency_totals`経由で全資料が使う。

**配信前の突き合わせ**（`verify_numbers.py`）: 商談一覧の契約・クーリングオフ・審査落ちを、実績DBの配分ルール
（自己アポ＝クローザーに全額・件数は契約と契約（アポ）の両方に1、他アポ＝クローザーとアポインターに半額ずつ）で
人別の「入っているべき金額・件数」に直し、実績DBの実際の値と比べる。先週と今月（1日〜先週日曜）の両方を見る。
- 火曜18:00 JST（`weekly-shuuji-precheck.yml`）: 結果を今川さんにLINEで送る（一致でも送る）
- 水曜8:00 JST（`weekly-shuuji.yml`の最初の手順）: ずれがあれば今川さんに通知して**配信を止める**。直してから手動実行する
- スライド全ページの下に、出典・取得時刻・突き合わせ結果を表示する

**資料を作った後の再確認**（`audit_outputs.py`）: notion_to_csv.pyが出した数字を、Notionから読み直して
別の計算方法で出し直した値と照合する（売上はNotionの数式列「実質売上（万円）」、全社の契約数は
実績DBの「契約−クーリン−審査落」と商談一覧の両方、月の売上目標は予算DBを読み直し、先週の宿題は
週初日の完全一致で読み直す）。個人17名×6項目・代理店の和・全社・月・宿題の有無まで1つでも合わなければ
水曜の配信を止めて今川さんに通知する。火曜18時の事前チェックでも同じ照合結果を送る。

**改善項目が無い人の「伸ばすならここ」**（`build_all.growth_levers`）: 平均の90%未満の項目が無い人には、
同じ役割の全社トップの値（訪問系の率は分母20件以上、商談→契約率は3件以上の人から選ぶ。量は訪問数・商談数）
まで上げた場合に、先週の数字で成果が何件増えたかを計算し、増える件数が一番大きい項目をスライドとPDFに出す
（アポインターはアポ、クローザーは契約の増加を優先）。率も量も全社トップなら「1割増やすと+何件」を出す。
元の数字は再確認（audit_outputs.py）でNotionと一致を確認済みのCSVだけを使う。

**週次の対象外**（`notion_to_csv.WEEKLY_HIDDEN`）: 藤江（催事稼働）は個人シート・スライド・ランキング・LINE配信に出さない。
実績DBの行は全社の合計には含め、代理店の合計からは「週次対象外」という別枠に分ける。

**Notionの読み取りは3回読んで合わせる**（`notion_to_csv.notion_query_all`・`scripts/monthly_budget_floor.query_all`）:
2026-10-20、作成から間もない行が読み取りのたびに1件欠ける（欠ける行が毎回変わる）ことを実データで確認した
（203行中202行しか返らない状態が10分以上続いた。再確認＝audit_outputs.pyが不一致として検出）。1回の結果を信用せず、
3回読んで、どれかに出た行をすべて使う。
2026-10-21、同じ時刻にまとめて作った2行（村田旭の10/14と10/17）が、読むたびにどちらか一方しか返らない状態が
1日以上続き、3回読んでも両方そろわないことがあった（並び順を指定しても直らない。行はNotionに残っていた）。
その行のタイトルを同じ値で書き直すと、以後は毎回返るようになった。そこで、3回の読み取りで出たり欠けたりする行が
あれば、タイトルを同じ値で書き直してから読み直す（値は変えない）。

**データが無いときの表示**: 先週の宿題が無い人は、Supabase `closer_line_users.pdca_pending_week` から理由を出す
（キーが残っている＝記入テンプレは届いたが未返信／空＝配信対象外）。実績DBに1日分も行が無い人は、
個人スライドを「データがありません：日報が未入力」にする。先週の宿題は週初日が「対象週の前週の月曜」の
記録だけを使う（以前は直近の記録を拾っていたため、2週以上前の宿題が先週分として出ていた）。

**売上 直近4週との比較**: スライド②の次に、自社と全社の週ごとの売上（先週＋その前4週）を出す（`build_slides.py` の `sales_history`、データは `notion_to_csv.py` の `weekly_sales.csv`）。MTは全体の数字を見る場で、個人の推移はリッチメニューのKPIページ「推移」タブ（`line-webhook/api/kpi.py`）。平均は実績DBに行がある週だけで計算し、平均が50万未満のときは%ではなく増減（万円）で出す。`audit_outputs.py` が全社・個人の週ごとの売上をNotionと照合する。

**ランキングの率は対面→アポ率**: スライド③の率ランキングは「対面→アポ率」（対面20件以上のアポインター。`build_slides.py` の `RANK_MIN_FACE`）。訪問→アポ率は数を追うほど下がるため、2026-10-21の週次MTで変更を決めた。対面数は `audit_outputs.py` でもNotionと照合する。

**先週の宿題は全文表示**: スライド①の宿題は省略しない（2026-10-21 今川さん指示）。3行を超える文は文字を小さくして
行を高くし、1ページに入る人数を減らす（`build_slides.py` の `action_block`）。PDCAシート（PDF）は元から全文。

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
- 代理店ごとの「管理者」フラグ（is_manager）に相当するデータがNotion/Supabaseに無いため、
  `notion_to_csv.py`の`MANAGER_NAMES`にハードコードしている（2026-10時点: 今川・岡野・門田・柚木。
  管理メインで訪問・アポの数字が恒常的に0近辺になる人をユーザーが指定）。`is_manager=yes`の人は
  個人シートの活動量（訪問・アポ等）セクションと関連する通過率を出さず商談以降のみに絞り、
  代理店ランキングの訪問数・アポ数からも除外し、他メンバーの活動量平均の算出からも外す
  （`build_all.py`の`rows_spec`・`sheet`・`common_pages`参照）。人の異動があれば
  `MANAGER_NAMES`を直接編集する（`--managers-only`は依然未対応。代理店の管理者＝is_managerは
  今のところこの用途専用で、`--managers-only`が意図する「代理店ごとの窓口役」とは別概念）
- 月間契約目標（社内合計）は、月間予算合計 ÷ 65万円（1件あたりの目安単価。webhook.py等
  既存の運用で使われている値を踏襲）で算出している
- 対象週は「月曜始まり・日曜終わり」（依頼の指定どおり。週次実績配信スキルの
  「水曜始まり火曜終わり」とは別軸）
