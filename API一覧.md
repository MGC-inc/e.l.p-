# API一覧.md — 使用サービス・API台帳

> E.L.P業務で使う外部サービス・APIの台帳。
> **APIキー・パスワード等の実体は絶対にこのリポジトリへ書かない。** 保管場所の参照のみ記載する。

## 1. サービス一覧

<!-- キー値はリポジトリ直下の .env（git管理外）に保管。ここには参照のみ書く -->

| サービス | 用途 | エンドポイント/管理画面 | 認証情報の保管場所 | 担当 |
|----------|------|------------------------|-------------------|------|
| イノベラ（INNOVERA PBX 2.0） | 電話・通話録音・Web API | `pbdxa17.innov-era.com`（API: `/pbx/api/front/index/`、ログイン: `/pbx/open/login/`） | ローカル `.env` の `INNOVERA_API_KEY` | 東さん |
| Google API | <!-- TODO: 用途を特定（Maps/Calendar等） --> | https://console.cloud.google.com | ローカル `.env` の `GOOGLE_API_KEY` | |
| Gemini API | 通話の文字起こし（イノベラ）／商談録音の一括分析（商談分析運用.md） | https://aistudio.google.com | ローカル `.env` の `GEMINI_API_KEY`（有料課金プロジェクトのキーを使う） | MGC |
| PLAUD NOTE | 商談の録音（無料スタータープラン。文字起こしは使わずMP3書き出しのみ） | https://jp.plaud.ai ＋ スマホアプリ | APIなし（アプリログインのみ） | 各クローザー |
| Notion（DB 商談分析＆アポ分析） | 商談分析の蓄積・ダッシュボード（商談分析運用.md） | Notionワークスペース内 | Claude Code の Notion connector（MCP）経由でアクセス。このリポジトリ・elp-goalsにトークンは置かない | 今川 |
| LINE（@124rnagj） | タスク通知・日報リマインド等（社内限定） | Webhook/push実装は `MGC-inc/elp-goals`（`/api/line/webhook`, `lib/line.ts`） | ローカル `.env` の `ELP_LINE_CHANNEL_SECRET` / `ELP_LINE_CHANNEL_ACCESS_TOKEN`（Vercel環境変数にも同値） | MGC |
| LINE（商談録音Bot・新規） | 商談録音の受付（社内クローザー＋代理店）。@124rnagjとは別アカウント（商談分析運用.md セクション0） | 未作成（今後アカウント発行） | 未定（作成時に追記） | 今川 |
| GitHub | このリポジトリ | https://github.com/MGC-inc/e.l.p- | | |
| Supabase（elp） | 組織データDB（タスク/日報/営業成績/議事録/通話ログ／商談録音の受付台帳 `deal_recordings`・Storageバケット `deal-recordings`） | https://supabase.com/dashboard/project/xhkcptfyjdbilhrpwcau | ローカル `.env` の `ELP_SUPABASE_*` | MGC |
| <!-- TODO --> | | | | |

## イノベラ Web API 技術仕様（仕様書 2024-10-23版より）

- **プロトコル**: HTTPS、HTTP POST（`application/x-www-form-urlencoded`）、レスポンスはJSON（`result` / `error_code` / `data`）
- **認証**: 全APIで `api_key` パラメータ必須
- **レート制限**: 同一IPから5分あたり200リクエスト（超過時 HTTP 429）
- **主要API**（通話要約パイプラインで使うもの）:
  - 電話履歴検索: `?ckey=cdr&akey=search` — `uniqid`・期間・回線・発着信種別で検索。`record_file_flg` で録音有無が分かる
  - 録音ファイルURL検索: `?ckey=cdr&akey=record` — `cdr_id` を渡すと録音ファイルURLを返す。**通話中に実行すると録音ファイルが壊れる**ので終話後のみ
  - 発信API・ユーザー管理API等もあり（詳細は仕様書PDF参照）
- **着信連携**: 鳴動時/応答時/応答終了時/不在終了時に外部webAPIを実行可能（回線ごとに設定、URL設定はプロディライト側作業）。通知連携はE-mail/Teams/Chatwork/Slack/LINE/LINE WORKSに対応
- **仕様書PDF**: 「INNOVERA2.0 web API仕様書」「発着信連携イメージ(v3.0)」— プロディライト社CONFIDENTIAL資料のためリポジトリには置かない（MGC側でローカル保管）

## シークレットの保管ルール

- キー値は**リポジトリ直下の `.env`（.gitignoreで除外済み）**に置く
- 変数名: `INNOVERA_HOST` / `INNOVERA_API_KEY` / `GOOGLE_API_KEY` / `GEMINI_API_KEY` / `ELP_SUPABASE_URL` / `ELP_SUPABASE_ANON_KEY` / `ELP_SUPABASE_SERVICE_ROLE_KEY` / `ELP_SUPABASE_DB_PASSWORD`
- サーバー側で自動化を動かす際はMGCのDopplerへ移行する

## 2. 記載ルール

- **書くもの**: サービス名、用途、管理画面URL、保管場所への参照、担当者
- **書かないもの**: APIキー、トークン、パスワード、シークレットの値そのもの
- 新サービス導入・解約時は即更新する
