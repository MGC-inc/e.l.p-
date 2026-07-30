---
name: 商談録音分析
description: LINEで送られてきた商談録音（PLAUD NOTE等のMP3）をGeminiで一括分析し、Notion「DB 商談分析＆アポ分析」に結果を反映する。「商談録音を分析して」で週1回まとめて実行するのが基本。「今週”分析しない”にした商談を見せて」「〇〇の商談も分析して」で管理者の一任による上書きにも対応する。
---

# 商談録音分析スキル

商談分析運用.md の全体フロー参照。営業マンはLINE（ユメイク営業分析bot、@124rnagjとは別アカウント）で録音を送り、Botの聞き返し（アポインター→お客様名→結果→分析する/しない）に答えるだけ。分析・Notion反映・LINE通知はこのスキルが行う。

**実行頻度は週1回が基本**（例: 月曜午後締切→火曜実行）。商談数自体が少ない（平日1件程度、土日はクローザー5人×平均2件）ため、頻繁な実行は不要。今川さんが「商談録音を分析して」と指示したタイミングで、その時点で溜まっている`status='ready'`の録音をまとめて処理する。

## 前提

- LINE Webhook（`ユメイク営業分析bot`。実装は`elp-goals`側または新規の軽量Webhook）が録音を受信した瞬間にSupabase Storage（バケット `deal-recordings`）へ保存し、Supabaseテーブル `deal_recordings` に行を作成している。アポインター→お客様名（〇〇邸形式）→結果→分析する/しない、の聞き返しが完了すると `status` が `ready`（分析する）または `skipped`（分析しない）になる。
- この前提がまだ実装されていない場合、このスキルは「未分析の商談はありません」と応答するだけになる。実装状況は `商談分析運用.md` を確認する。

## 手順（通常実行: 「商談録音を分析して」）

1. **Supabaseから対象の録音を取得する**

   `.env` の `ELP_SUPABASE_URL` / `ELP_SUPABASE_SERVICE_ROLE_KEY` を読み込み、`deal_recordings?status=eq.ready&select=*` をPostgREST経由で取得する（`Claude操作マニュアル.md` §2 のPythonスニペットのパターンに従う）。

   0件なら「分析対象の商談はありません」とだけ答えて終了する。

2. **各録音について、音声ファイルを取得する**

   `storage_path` を使い、Supabase Storageの署名付きURL（`/storage/v1/object/sign/...`）を発行するか、service roleキーで直接ダウンロードし、`scratchpad` ディレクトリに一時保存する。

3. **Geminiで分析する**

   ```bash
   python3 scripts/deal_analysis.py <一時保存パス> \
     --closer <deal_recordings.closer_employee_idから解決した氏名> \
     --customer <deal_recordings.customer_name（〇〇邸の形式）> \
     --result <deal_recordings.result> \
     --appointer <deal_recordings.appointer（LINEで確定済み）> \
     --date <deal_recordings.received_atの日付>
   ```

   標準出力に `{"content_markdown": "...", "properties": {...}}` のJSONが1行で返る。3時間超の音声は分析に数分かかる（アップロード＋Gemini処理）。

   **失敗した場合**（Gemini APIエラー・ファイル破損等）: この録音はスキップし、`status='ready'`のまま残す（次回再試行できるように）。手順7で管理者に通知する対象としてリストしておく。

4. **Notionにページを作成する**

   `mcp__Notion__notion-create-pages` を使い、`parent` に `{"type": "data_source_id", "data_source_id": "8958bbaf-2c24-4e94-97b2-4c801e60cb37"}`（「DB 商談分析＆アポ分析」データソース）を指定してページを作成する。

   - `properties` はステップ3で得た `properties` オブジェクトをそのまま渡す（キーはNotionのプロパティ名と一致させてある）。日付型の `商談日時` は `date:商談日時:start` 形式でセットする必要がある点に注意（Notion側のSQLiteスキーマを`fetch`で確認してから合わせる）。
   - `content` にステップ3の `content_markdown` を渡す。
   - 作成したページのURLを控えておく。

   **失敗した場合**（Notion API制限・プロパティ不一致等）: 手順3同様にスキップし、`status='ready'`のまま残す。

5. **Supabaseを更新する**

   成功した行を `status='done'`, `notion_url=<作成したページURL>` に更新する（PostgRESTのPATCHで）。

6. **LINEで完了通知を送る**

   `ELP_LINE_CHANNEL_ACCESS_TOKEN` を使い、`https://api.line.me/v2/bot/message/push` にPOSTして `deal_recordings.line_user_id` 宛てに「〇〇邸の分析が完了しました: <Notionリンク>」を送る（`scripts/line_richmenu.py` のurllib直叩きパターンを踏襲する）。

7. **失敗があれば管理者に通知する**

   手順3・4で失敗した録音があれば、管理者（今川さん）のLINE宛に「〇〇邸の分析に失敗しました。原因: 〜。再実行してください」とpush通知する。

8. **失注理由の急増をチェックする（商談分析運用.md セクション6-2）**

   今回のバッチで新たにNotionへ登録した商談のうち、`結果`が契約以外のもの（保留/失注/クーリングオフ/審査落ち）について、`properties.ネック・保留理由`（または`備考`）の内容を突き合わせる。**似た理由が2件以上あれば**（厳密な文字列一致でなく、内容が同じ趣旨と判断できるものをまとめる。例:「ハウスメーカーの保証と被る」「セキスイの保証があるから」は同じ括りにする）、管理者（今川さん）にLINEで「今週、失注理由に“〇〇”が複数件（n件）見られました」と通知する。1件以下なら通知しない。

9. **ユーザーへの応答**

   処理した件数と、各件のお客様名・結果・Notionリンクを一覧で報告する。失敗・失注理由アラートがあれば併せて報告する。

## 管理者の一任による上書き（追加の使い方）

- 「今週 “分析しない” にした商談を見せて」と聞かれたら: `deal_recordings?status=eq.skipped&select=*` を照会し、お客様名・クローザー・結果・受信日を一覧表示する。
- 「〇〇の商談も分析して」と言われたら: 該当行を `status='ready'` に更新してから、上記「手順」の2以降と同じ流れで分析する。

## 注意事項

- 顧客の住所・電話番号・支払い口座情報は分析結果に含めない（プロンプト側で禁止済みだが、Notion登録前に目視でも確認する）。
- Notionの選択肢系プロパティ（商材・給湯器・決裁者・年齢層・訪販経験・課題関心事・商談相手・築年数）は、既存データベースの選択肢文字列と完全一致させる。一致しない値が来た場合はそのプロパティを省略し、他は反映する（Notion側で新しい選択肢が追加されてしまうのを避ける）。
- 売上金額・支払方法・入金状況・入金日・完工日・報酬反映月・支払い反映済み は、この分析では設定しない（既存の後工程運用のまま）。
- 録音データ（Supabase Storage上のファイル）は分析後も削除しない。
