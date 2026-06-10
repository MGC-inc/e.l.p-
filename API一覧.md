# API一覧.md — 使用サービス・API台帳

> E.L.P業務で使う外部サービス・APIの台帳。
> **APIキー・パスワード等の実体は絶対にこのリポジトリへ書かない。** 保管場所の参照のみ記載する。

## 1. サービス一覧

<!-- キー値はリポジトリ直下の .env（git管理外）に保管。ここには参照のみ書く -->

| サービス | 用途 | エンドポイント/管理画面 | 認証情報の保管場所 | 担当 |
|----------|------|------------------------|-------------------|------|
| イノベラ（INNOVERA PBX 2.0） | 電話・通話録音・Web API | `pbdxa17.innov-era.com`（API: `/pbx/api/front/index/`、ログイン: `/pbx/open/login/`） | ローカル `.env` の `INNOVERA_API_KEY` | 東さん |
| Google API | <!-- TODO: 用途を特定（Maps/Calendar等） --> | https://console.cloud.google.com | ローカル `.env` の `GOOGLE_API_KEY` | |
| LINE | 通知・顧客連絡 | | | |
| GitHub | このリポジトリ | https://github.com/MGC-inc/e.l.p- | | |
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
- 変数名: `INNOVERA_HOST` / `INNOVERA_API_KEY` / `GOOGLE_API_KEY`
- サーバー側で自動化を動かす際はMGCのDopplerへ移行する

## 2. 記載ルール

- **書くもの**: サービス名、用途、管理画面URL、保管場所への参照、担当者
- **書かないもの**: APIキー、トークン、パスワード、シークレットの値そのもの
- 新サービス導入・解約時は即更新する
