# line-webhook — 商談録音分析のLINE受信プログラム

「ユメイク営業分析bot」宛てに送られた商談録音を受け取り、アポインター→お客様名→結果→分析する/しないの
4問クイックリプライで必要事項を確定させ、Supabaseに記録する。今川さん個人のVercelアカウントで運用する
（詳細な設計・全体像は [`../商談分析運用.md`](../商談分析運用.md) を参照）。

標準ライブラリのみのPython実装（Vercel Python Functions）。分析処理本体（Gemini・Notion登録）は
このリポジトリの `.claude/skills/商談録音分析/SKILL.md` が別途行う。ここで作るのは「受信して記録するだけ」の部分。

## セットアップ手順

### 1. Supabase

1. [`supabase/schema.sql`](supabase/schema.sql) の内容を、SupabaseダッシュボードのSQL Editorで実行する
2. 「Storage」→「New bucket」で `deal-recordings` という名前のバケットを作成する（Publicにしなくてよい）
3. `closer_line_users` テーブルに、クローザー・アポインター（社内＋代理店）の分だけ行を用意する。
   最初は `line_user_id` が分からないので空でよい。各自が一度「ユメイク営業分析bot」に何かメッセージを送ると、
   Webhookが自動で `line_user_id` と `display_name`（LINEの表示名）を仮登録する。
   その後、Supabase側で該当行の `closer_name`（正式な氏名）を埋めれば、その人はBotを使えるようになる

### 2. Vercelへのデプロイ

1. [vercel.com](https://vercel.com) で今川さんの個人アカウント（GitHub連携済み）にログイン
2. 「Add New」→「Project」→「Import Git Repository」で `MGC-inc/e.l.p-` を選択
3. 「Root Directory」を `line-webhook` に設定する（これを忘れるとリポジトリ全体をNext.jsとしてビルドしようとして失敗する）
4. 「Environment Variables」に以下を設定する（値はこのリポジトリやチャットには書かない）:
   - `DEAL_LINE_CHANNEL_SECRET`
   - `DEAL_LINE_CHANNEL_ACCESS_TOKEN`
   - `ELP_SUPABASE_URL`
   - `ELP_SUPABASE_SERVICE_ROLE_KEY`
5. Deployをクリックする

### 3. LINE側の設定

1. デプロイ完了後に発行されるURL（例: `https://<プロジェクト名>.vercel.app`）を控える
2. LINE Official Account Manager →「ユメイク営業分析bot」→ 設定 →「Messaging API」→「LINE Developers」経由で
   チャネルのMessaging API設定画面を開く
3. 「Webhook URL」に `https://<プロジェクト名>.vercel.app/api/webhook` を設定し、「検証」でエラーが出ないことを確認する
4. 「Webhookの利用」を **ON** にする
5. LINE Official Account Manager側で「応答メッセージ」を **OFF** にする（このWebhookの返信と二重にならないように）

### 4. 動作確認

1. 社内クローザーの誰か（またはテスト用のLINEアカウント）から「ユメイク営業分析bot」にテキストを送る
   → `担当者名が未登録です` と返ってくれば疎通OK
2. Supabaseの `closer_line_users` にその人の行ができているのを確認し、`closer_name` を埋める
3. もう一度何か送ると、担当者として認識される
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
- `APPOINTER_OPTIONS`（`api/webhook.py`冒頭）はメンバー構成が変わったら手動で更新する
