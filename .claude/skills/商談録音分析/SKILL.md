---
name: 商談録音分析
description: LINEで送られてきた商談録音（PLAUD NOTE等のMP3）をGeminiで一括分析し、Notion「DB 商談分析＆アポ分析」に結果を反映する。「商談録音を分析して」「未分析の商談をNotionに反映して」等で呼び出す。
---

# 商談録音分析スキル

商談分析運用.md の全体フロー参照。営業マンはLINEで録音を送り、Botの聞き返しに1〜2往復答えるだけ。分析・Notion反映・LINE通知はこのスキルがオンデマンドで行う（定期実行はしない。ユーザーが明示的に指示したタイミングでのみ動く）。

## 前提

- LINE Webhook（`elp-goals`）が音声/ファイルメッセージを受信し、Supabase Storage（バケット `deal-recordings`）に保存し、Supabaseテーブル `deal_recordings` に行を作成している。クイックリプライで顧客名・結果が埋まると `status='ready'` になる。
- この前提がまだ実装されていない場合、このスキルは「未分析の商談はありません」と応答するだけになる。実装状況は `商談分析運用.md` を確認する。

## 手順

1. **Supabaseから未処理の録音を取得する**

   `.env` の `ELP_SUPABASE_URL` / `ELP_SUPABASE_SERVICE_ROLE_KEY` を読み込み、`deal_recordings?status=eq.ready&select=*` をPostgREST経由で取得する（`Claude操作マニュアル.md` §2 のPythonスニペットのパターンに従う）。

   0件なら「未分析の商談はありません」とだけ答えて終了する。

2. **各録音について、音声ファイルを取得する**

   `storage_path` を使い、Supabase Storageの署名付きURL（`/storage/v1/object/sign/...`）を発行するか、service roleキーで直接ダウンロードし、`scratchpad` ディレクトリに一時保存する。

3. **Geminiで分析する**

   ```bash
   python3 scripts/deal_analysis.py <一時保存パス> \
     --closer <deal_recordings.closer_employee_idから解決した氏名> \
     --customer <deal_recordings.customer_name> \
     --result <deal_recordings.result> \
     --date <deal_recordings.received_atの日付>
   ```

   標準出力に `{"content_markdown": "...", "properties": {...}}` のJSONが1行で返る。3時間超の音声は分析に数分かかる（アップロード＋Gemini処理）。

4. **Notionにページを作成する**

   `mcp__Notion__notion-create-pages` を使い、`parent` に `{"type": "data_source_id", "data_source_id": "8958bbaf-2c24-4e94-97b2-4c801e60cb37"}`（「DB 商談分析＆アポ分析」データソース）を指定してページを作成する。

   - `properties` はステップ3で得た `properties` オブジェクトをそのまま渡す（キーはNotionのプロパティ名と一致させてある）。日付型の `商談日時` は `date:商談日時:start` 形式でセットする必要がある点に注意（Notion側のSQLiteスキーマを`fetch`で確認してから合わせる）。
   - `content` にステップ3の `content_markdown` を渡す。
   - 作成したページのURLを控えておく。

5. **Supabaseを更新する**

   該当行を `status='done'`, `notion_url=<作成したページURL>` に更新する（PostgRESTのPATCHで）。

6. **LINEで完了通知を送る**

   `ELP_LINE_CHANNEL_ACCESS_TOKEN` を使い、`https://api.line.me/v2/bot/message/push` にPOSTして `deal_recordings.line_user_id` 宛てに「分析が完了しました: <Notionリンク>」を送る（`scripts/line_richmenu.py` のurllib直叩きパターンを踏襲する）。

7. **ユーザーへの応答**

   処理した件数と、各件のお客様名・結果・Notionリンクを一覧で報告する。分析に失敗した録音があれば理由とともに報告し、Supabase側は `status='ready'` のまま残す（再実行できるように）。

## 注意事項

- 顧客の住所・電話番号・支払い口座情報は分析結果に含めない（プロンプト側で禁止済みだが、Notion登録前に目視でも確認する）。
- Notionの選択肢系プロパティ（商材・給湯器・決裁者・年齢層・訪販経験・課題関心事・商談相手・築年数）は、既存データベースの選択肢文字列と完全一致させる。一致しない値が来た場合はそのプロパティを省略し、他は反映する（Notion側で新しい選択肢が追加されてしまうのを避ける）。
- 売上金額・支払方法・入金状況・入金日・完工日・報酬反映月・支払い反映済み は、この分析では設定しない（既存の後工程運用のまま）。
