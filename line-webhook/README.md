# line-webhook — 商談録音分析のLINE受信プログラム

「ユメイク営業分析bot」宛てのメッセージを受け付ける。クローザー（商談録音を送る人）・
アポインター（週次の実績配信を受け取るだけの人）・管理者（週次のチーム全体の結果だけ
受け取る人）いずれも、初回メッセージであいさつ→お名前（苗字）→役割選択、の自己登録フローに
乗る（表示名がwebhook.pyのKNOWN_CLOSERS/KNOWN_APPOINTERS/KNOWN_ADMINSに一致する人は
即時登録され、この問答は省略される）。
登録済みのクローザーが録音を送ると、アポインター→お客様名→結果→分析する/しないの
4問クイックリプライで必要事項を確定させ、Supabaseに記録する。今川さん個人のVercelアカウントで運用する
（詳細な設計・全体像は [`../商談分析運用.md`](../商談分析運用.md) を参照）。

標準ライブラリのみのPython実装（Vercel Python Functions）。分析処理本体（Gemini・Notion登録）は
このリポジトリの `.claude/skills/商談録音分析/SKILL.md` が別途行う。ここで作るのは「受信して記録するだけ」の部分。

## セットアップ手順

### 1. Supabase

1. [`supabase/schema.sql`](supabase/schema.sql) の内容を、SupabaseダッシュボードのSQL Editorで実行する
   （既存環境で `closer_line_users` に `role` 列がまだない場合は、schema.sql末尾の
   `alter table ... add column if not exists role text;` も忘れず実行する）
2. 「Storage」→「New bucket」で `deal-recordings` という名前のバケットを作成する（Publicにしなくてよい）
3. 各自が「ユメイク営業分析bot」を友だち追加して何かメッセージを送ると、`closer_line_users` に
   `line_user_id` と `display_name`（LINEの表示名）が仮登録され、続けてbotからのあいさつに
   従って苗字→クローザー/アポインター/管理者の役割を答えるだけで本登録が完了する。
   表示名が `api/webhook.py` の `KNOWN_CLOSERS`/`KNOWN_APPOINTERS`/`KNOWN_ADMINS` に一致する人は
   この問答なしで初回メッセージから即登録される（一致しない新規メンバーだけこの問答が出る）。
   新規メンバーの追加・退社時の削除は `scripts/add_closer.py`・`scripts/remove_closer.py`
   を使う（`Claude操作マニュアル.md` セクション9）。自己登録がうまくいかない場合のみ、
   Supabase側で該当行の `closer_name`・`role` を手動で埋める

### 2. Vercelへのデプロイ

1. [vercel.com](https://vercel.com) で今川さんの個人アカウント（GitHub連携済み）にログイン
2. 「Add New」→「Project」→「Import Git Repository」で `MGC-inc/e.l.p-` を選択
3. 「Root Directory」を `line-webhook` に設定する（これを忘れるとリポジトリ全体をNext.jsとしてビルドしようとして失敗する）
4. 「Environment Variables」に以下を設定する（値はこのリポジトリやチャットには書かない）:
   - `DEAL_LINE_CHANNEL_SECRET`
   - `DEAL_LINE_CHANNEL_ACCESS_TOKEN`
   - `DEAL_SUPABASE_URL`
   - `DEAL_SUPABASE_SERVICE_ROLE_KEY`

   （`ELP_SUPABASE_URL`等の名前は使わない。Vercelチーム共有変数として既に別用途で使われており、
   Vercelの通常デプロイでは共有変数がFile Upload APIデプロイに反映されない問題が確認されたため、
   このプロジェクト専用の変数名で直接追加する）
5. Deployをクリックする

### 3. LINE側の設定

1. デプロイ完了後に発行されるURL（例: `https://<プロジェクト名>.vercel.app`）を控える
2. LINE Official Account Manager →「ユメイク営業分析bot」→ 設定 →「Messaging API」→「LINE Developers」経由で
   チャネルのMessaging API設定画面を開く
3. 「Webhook URL」に `https://<プロジェクト名>.vercel.app/api/webhook` を設定し、「検証」でエラーが出ないことを確認する
4. 「Webhookの利用」を **ON** にする
5. LINE Official Account Manager側で「応答メッセージ」を **OFF** にする（このWebhookの返信と二重にならないように）

### 4. 動作確認

1. テスト用のLINEアカウント（表示名がKNOWN_CLOSERS/KNOWN_APPOINTERS/KNOWN_ADMINSに
   一致しないもの）から「ユメイク営業分析bot」にテキストを送る
   → あいさつ＋苗字を尋ねるメッセージが返れば疎通OK
2. 苗字を送る → クローザー/アポインター/管理者の役割を尋ねるクイックリプライが返る
3. 「クローザー」を選ぶ → 登録完了メッセージが返り、Supabaseの `closer_line_users` にその人の行
   （`closer_name`・`role='closer'` 設定済み）ができていることを確認する
4. 実際に音声ファイル（MP3等）を送り、アポインター→お客様名→結果→分析する/しない、の4問に順番に答えて
   `deal_recordings` の行が `ready` または `skipped` になることを確認する
5. Supabase Storageの `deal-recordings` バケットに音声ファイルが保存されていることを確認する

## ファイル構成

- `api/webhook.py` — LINE Webhook本体（署名検証・音声受信・4問クイックリプライの状態遷移）
- `supabase/schema.sql` — `deal_recordings` / `closer_line_users` テーブル定義
- `vercel.json` — Vercel Python Functionsの設定
- `requirements.txt` — 外部依存なし（Vercelがpythonプロジェクトと認識するためのプレースホルダー）

## 注意事項

- ここで受け取った録音は消さない（分析後も含め、Supabase Storageに保持し続ける方針。商談分析運用.md参照）
- チャネルシークレット・アクセストークンの値は、Vercelの環境変数以外（このリポジトリ・チャット等）に書かない
- `KNOWN_CLOSERS`・`KNOWN_APPOINTERS`・`KNOWN_ADMINS`（`api/webhook.py`冒頭）はメンバー構成が
  変わったら `scripts/add_closer.py`（追加）・`scripts/remove_closer.py`（削除）で更新する
  （`従業員.md`・Supabaseの仮登録行の即時反映も一緒に行う。手でこの3つのPython定数を
  編集する必要はない）
- 商談録音時に聞く「アポインターは誰ですか？」は自由入力（Notion側の「アポインター」選択肢が25名あり、
  LINEのクイックリプライ上限13個を超えるためボタン化していない）
