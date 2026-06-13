# Claude操作マニュアル — E.L.P業務システム

> Claude Code から E.L.P の業務システム（Webアプリ / LINE / イノベラ / Supabase）を
> 操作するための手順書。新しいセッションはまずこのファイルを読めば一通り動かせる。
> **鍵の値は絶対にこのファイルに書かない・出力しない。** 名前と保管場所のみ記載する。

---

## 0. 全体像（どこに何があるか）

| 要素 | 実体 | 場所 |
|---|---|---|
| 知識・スクリプト | リポジトリ `e.l.p-` | ローカル `…/04_クライアント/E.L.P/e.l.p-`、GitHub `MGC-inc/e.l.p-` |
| Webアプリ | リポジトリ `elp-goals`（Next.js 16） | ローカル `…/04_クライアント/E.L.P/elp-goals`、GitHub `MGC-inc/elp-goals` |
| 本番サイト | Vercel | https://elp-goals.vercel.app |
| データベース | Supabase | project ref `xhkcptfyjdbilhrpwcau` / `https://xhkcptfyjdbilhrpwcau.supabase.co` |
| LINE Bot | E.L.P ボット | basic ID `@124rnagj` |
| 電話 | INNOVERA PBX | host `pbdxa17.innov-era.com` |

設計思想は [運用設計.md](./運用設計.md)（知識はGitHub・データはSupabase・入力はUI・AIは両方読む）。

---

## 1. 認証情報の保管場所（値は書かない）

| ファイル | gitignore | 入っている変数名 |
|---|---|---|
| `e.l.p-/.env` | ✅ | `INNOVERA_HOST` `INNOVERA_API_KEY` `GOOGLE_API_KEY` `GEMINI_API_KEY` `ELP_SUPABASE_URL` `ELP_SUPABASE_ANON_KEY` `ELP_SUPABASE_SERVICE_ROLE_KEY` `ELP_SUPABASE_DB_PASSWORD` `ELP_LINE_CHANNEL_SECRET` `ELP_LINE_CHANNEL_ACCESS_TOKEN` `ELP_CRON_SECRET` |
| `elp-goals/.env.local` | ✅ | `NEXT_PUBLIC_SUPABASE_URL` `SUPABASE_SERVICE_ROLE_KEY` |
| Vercel 環境変数（本番） | — | `NEXT_PUBLIC_SUPABASE_URL` `SUPABASE_SERVICE_ROLE_KEY` `LINE_CHANNEL_SECRET` `LINE_CHANNEL_ACCESS_TOKEN` `CRON_SECRET` `NEXT_PUBLIC_APP_URL` |

ルール: **`.env` をコミットしない／値を出力しない。** スクリプトは `.env` を読み込んで使う。

---

## 1.5 クローン後のセットアップ（リポジトリだけでは動かない）

GitHub のコードには鍵(`.env`)を含めていないため、**クローン直後は動かない**。次で動く。

### A. 同じ E.L.P 環境を引き継ぐ場合（最短）
```bash
# Webアプリ
cd elp-goals
cp .env.example .env.local      # 値を記入（Supabase service_role キー）
npm install
npm run dev                     # ローカル起動（本番は git push で自動デプロイ）

# スクリプト
cd ../e.l.p-
cp .env.example .env            # 値を記入（Innovera/Gemini/Supabase/LINE）
```
- Supabase・Vercel・LINE Bot は**既存をそのまま使う**ので新規構築は不要（DBのマイグレーションも適用済み）。
- 鍵の値は現行の `.env` / Vercel環境変数 / 各管理画面から取得し、**安全な経路で共有**する（リポジトリには絶対入れない）。

### B. まったく別環境に複製する場合
- Supabase 新規プロジェクト作成 → `supabase db push` でマイグレーション適用
- Vercel 新規プロジェクト ＋ 環境変数登録
- LINE 新規チャネル作成 ＋ Webhook URL 設定 ＋ リッチメニュー登録
- Innovera / Gemini の各キー発行

> 本番サイト（https://elp-goals.vercel.app）は GitHub 連携で動いているので、
> ローカルにクローンしなくても **push さえできれば反映される**。

---

## 2. データベースを読む・書く（Claudeの標準手段）

**経路: service-role キー + Supabase REST（PostgREST）を Python から叩く。**
（Supabase MCP はこのプロジェクト未認可なので使わない。`.env` をシェルで `grep` すると権限ブロックされることがあるので、**Pythonで `.env` を読む**のが安定。）

最小スニペット（`e.l.p-` 直下で実行）:

```python
import json, urllib.request, urllib.parse
env = {}
for line in open(".env", encoding="utf-8"):
    line = line.rstrip("\n")
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); env[k.strip()] = v.strip()
URL = env["ELP_SUPABASE_URL"]; KEY = env["ELP_SUPABASE_SERVICE_ROLE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

def get(path):  # 例: get("tasks?select=*&status=neq.完了")
    return json.load(urllib.request.urlopen(urllib.request.Request(f"{URL}/rest/v1/{path}", headers=H), timeout=30))

def post(table, rows):  # rows は dict のリスト。全行で同じキーにすること（PostgRESTの制約）
    req = urllib.request.Request(f"{URL}/rest/v1/{table}", data=json.dumps(rows, ensure_ascii=False).encode(),
                                 method="POST", headers={**H, "Prefer": "return=representation"})
    return json.load(urllib.request.urlopen(req, timeout=30))
```

PostgREST の注意:
- 日本語の絞り込みは URLエンコードする（例 `name=eq.` + `urllib.parse.quote("東")`）。
- バルク INSERT は**全行で同じキー**にする（違うと 400）。
- NULL 判定は `col=not.is.null` / `col=is.null`。
- `tasks` は従業員FKが2本（`assignee_id`/`director_id`）あるので、埋め込みは
  **`employees!assignee_id(name)`** のようにFKを明示する（無印 `employees(name)` は曖昧で `PGRST201` エラー）。

### スキーマ変更（DDL）
`supabase` CLI を使う。`elp-goals/` で:

```bash
export SUPABASE_DB_PASSWORD="$( … ELP_SUPABASE_DB_PASSWORD の値 … )"   # .envから取得
supabase db push   # supabase/migrations/*.sql を適用
```

---

## 3. テーブル一覧（2026-06 時点）

| テーブル | 用途 |
|---|---|
| `employees` | 従業員（name, role, extension_num=内線, is_active, line_user_id） |
| `customers` | 顧客マスタ |
| `projects` | プロジェクト |
| `tasks` | タスク（assignee_id=作業者, director_id=指示者, kpi_id, parent_task_id, level=大/中/小, due_at, status） |
| `daily_reports` | 日報（employee_id+report_date でユニーク） |
| `sales_records` | 営業成績（月次） |
| `meeting_notes` | 議事録 |
| `call_logs` | イノベラ通話（19項目＋文字起こし。innovera_uniqid ユニーク） |
| `line_contacts` | Botを追加した人（line_user_id, display_name, employee_id, pending_action） |
| `kgis` / `divisions` / `kpis` | KGI→事業部→KPI の階層 |

---

## 4. デプロイ（コード変更の反映）

**GitHub の `main` に push すると Vercel が自動で本番デプロイする**（Git連携済み）。
手動の `vercel deploy` は不要。

```bash
cd elp-goals
git add -A && git commit -m "feat(...): ..."
git push origin main          # → 自動デプロイ
vercel ls elp-goals | head    # 状態確認（● Ready）
curl -s https://elp-goals.vercel.app/<path>   # 反映確認
```

注意: コミットの author email は GitHub に紐づくものにすること
（このリポは `190605767+koko1056-inv@users.noreply.github.com` を設定済み。
別メールだと Vercel が「commit email がGitHubに一致しない」でブロックする）。

---

## 5. イノベラ → Gemini 通話パイプライン

対象回線: circuit 1=車トラブル相談窓口(05088960087) / 2=車トラブル受付センター(05088906176)。

```bash
cd e.l.p-
python3 scripts/innovera_pipeline.py --limit 1            # 最新1件を処理
python3 scripts/innovera_pipeline.py --limit 5 --start "2026-06-12 00:00:00" --end "2026-06-12 23:59:59"
python3 scripts/innovera_pipeline.py --limit 1 --dry-run  # Gemini/DB保存せず録音DLまで確認
```

中身: CDR検索 → 録音DL → Gemini 2.5-flash で文字起こし+19項目抽出（プロンプトは
`scripts/gemini_prompt.txt`）→ `call_logs` へ upsert。アプリ `/calls` に反映。
仕様は [イノベラ.md](./イノベラ.md)。CDR API は `https://{host}/pbx/api/front/index/?ckey=cdr&akey=search`。

---

## 6. LINE 運用

| 操作 | 方法 |
|---|---|
| メンバーを連携 | アプリ `/staff` で従業員登録 → 本人がBot(@124rnagj)を友だち追加 → 名前を送信 |
| 誤った紐付けの修正 | `/staff` の「LINE友だちの紐付け」で割り当て直し |
| リッチメニュー更新 | `cd e.l.p- && python3 scripts/line_richmenu.py`（CELLSを編集して再実行） |
| リマインド手動起動 | `curl -H "Authorization: Bearer <ELP_CRON_SECRET>" "https://elp-goals.vercel.app/api/cron/line-reminders?mode=overdue"`（mode=daily で日報リマインド） |
| 通知の仕組み | Webhook `/api/line/webhook`、push は `lib/line.ts`。割当通知は `addTask`、定期は cron |

自動リマインド: 日報=毎日18:00、期限超過タスク=毎時（Vercel Cron、`vercel.json`）。

---

## 7. 資料（PPTX）生成

```bash
cd e.l.p-
python3 scripts/strategy_deck_pptx.py    # 事業戦略 全体図（6ページ）
python3 scripts/strategy_pptx.py         # 戦略マップ（1枚）
python3 scripts/people_graph_pptx.py     # 人の指示→作業つながり（実データ）
```

出力は親フォルダ直下（`E.L.P/*.pptx`）。リポジトリには含めない（スクリプトのみ管理）。
日本語フォントは `ヒラギノ角ゴシック W6`、プレビューは `soffice --headless --convert-to png`。

---

## 8. よくある落とし穴

- **`.env` をシェルで `grep` すると権限ブロック** → Python で `open(".env")` して読む。
- **`tasks` の `employees(name)` は曖昧（PGRST201）** → `employees!assignee_id(name)` とFK明示。
- **E.L.P の Google APIキー（GOOGLE_API_KEY）は Gemini ブロック済み** → 文字起こしは `GEMINI_API_KEY`（別キー）を使う。
- **MGCサーバへのSSH（Doppler取得）はサンドボックスで不可**。E.L.Pの鍵は `e.l.p-/.env` から。
- **PPTX等の生成物は `04_クライアント/E.L.P/` 直下**。フォルダごと移動されることがあるので絶対パスは固定しない。

---

## 9. 未整備（今後）

- ログイン認証（現状は認証なし・URLを知れば誰でも閲覧）
- KGI/事業部/KPI の管理UI（今はseedスクリプト `scripts/seed_kgi_tree.py`）
- DB→GitHub 自動ダイジェスト（運用設計.md Phase 3）
- イノベラの自動トリガー（今は手動実行）

詳細な現状と次の一手は [運用設計.md](./運用設計.md) を参照。
