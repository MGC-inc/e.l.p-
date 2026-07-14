# 🗺️ 組織ゴールマップ（tools/roadmap/）

会社→組織→チーム→個人と、Googleマップのようにズーム（クリックで展開）しながら辿れる組織全体のゴールマップ。個人レベルまで来ると、既存の [`tools/goalmap/goalmap-studio.html`](../goalmap/goalmap-studio.html)（アバター成長・大枠/中目標の北極星・MT議事録録音・連続ズームなど）にそのまま遷移する。

> 個人ゴールマップ（`tools/goalmap/`）は**引き続きオフライン単体HTMLとして独立に配布・編集**できる。今回追加した組織ビュー（このディレクトリ）は**ビルドが必要**で、Vercel等にホスティングして使う別アプリ。

## 現在の実装状況（Phase A完了・Phase B進行中）

- ✅ React + TypeScript + [`@xyflow/react`](https://reactflow.dev/)（React Flow）でLevel0〜3（会社/組織/チーム/個人）の展開・パン/ズーム・パンくずを実装
- ✅ カードをドラッグして自由に位置調整できる（見た目のみ・保存なし。2026-07-09追加）
- ✅ 個人（Level3）ノードをクリックすると `goalmap-studio.html?member=<名前>` へ遷移し、studio側で自動的にそのメンバーが選択される
- ✅ Notion「🏢 組織ロードマップ」DB作成・実データ投入（会社/組織/チーム10行、自己リレーション「親」でツリー化）。既存🧭目標マップDBに「所属チーム」リレーションを追加し、実データ15行に設定済み（Phase B項目1・2）
- ✅ データは `public/data/roadmap.json`（静的JSON）を起動時にfetch。**実データ**（会社KGI：売上30億円=代理店30社×平均年商1億円・期限2029-03、営業部/施工部の2組織、営業部内にピタサチ/wannyチーム＋今川（マネジメント）を直接配置）に更新済み（Phase B項目3。2026-07-09に管理部/マネジメントチームを廃止し営業部へ統合）
- ✅ `sync_notion_to_json.py`：Notion→`roadmap.json`を生成するバッチを追加し、実際の`NOTION_TOKEN`で実行確認済み（Phase B項目4完了）。チームごとにメンバー名で重複除去し、`tools/goalmap/members/*.json`の実ファイル名（〇〇／〇〇（育成）等）に基づいてリーフを割り当てる
- ⬜ Vercel等への実デプロイ（Phase B項目5・Vercelコネクタの認可が必要）
- ⬜ Level4（タスク）/Level5（サブタスク）専用UI（当面は個人ゴールマップ内のフェーズ/タスクで代替・Phase C）

## セットアップ・起動

```bash
cd tools/roadmap/app
npm install
npm run dev       # http://localhost:5173 で開発サーバ
```

```bash
npm run build      # 本番ビルド → dist/
npm run preview    # ビルド結果をローカルで確認
```

`npm run dev` / `npm run build` の前に自動で `tools/goalmap/goalmap-studio.html` が `public/goalmap-studio.html` へコピーされる（`scripts/copy-studio.mjs`）。正データは常に `tools/goalmap/` 側なので、個人ゴールマップを更新したら組織ゴールマップも再ビルドで自動的に最新化される。

## データモデル（`RoadmapNode`）

`src/types/roadmap.ts`。`children` を持つ再帰構造で、将来どの階層も無限に深くできる汎用スキーマ。

```ts
interface RoadmapNode {
  id: string;
  level: 0|1|2|3|4|5;           // 会社/組織/チーム/個人/タスク/サブタスク
  title: string;
  description?: string;
  progress?: number;             // 0-100
  deadline?: string;
  owner?: string;
  priority?: "low"|"medium"|"high";
  status?: "not_started"|"in_progress"|"done"|"at_risk";
  externalLink?: string;         // Level3のみ: goalmap-studio.htmlへのリンク
  children?: RoadmapNode[];
}
```

## 操作

- **クリック**：子を持つノードをクリックすると展開（子ノードが現れ、自動でその範囲にズーム）。もう一度クリックで折りたたみ
- **個人ノードをクリック**：`goalmap-studio.html` へ遷移（このアプリ内では作り込まず、既存のリッチな個人ツールをそのまま使う）
- **最後に見ていた場所を自動復元**：この端末で最後に展開していたノードを`localStorage`に覚えておき、再訪時は会社ノードまで折りたたまれた初期状態ではなくそこから再開する（2026-07-14追加。全員が同じリンクを共有しても各自の端末ごとに自分が最後に見ていた場所が復元される＝毎回一覧をたどり直すストレスがない）。個人スタジオ側（`goalmap-studio.html`）も同様に、`?member=`無しで開くとその端末が最後に見ていたメンバーを自動選択する。
- **パンくず**（左上）：今どの階層にいるか常時表示。クリックでその階層まで一気に戻れる
- **背景のスクロール/ピンチ/ドラッグ**：地図のような連続パン・ズーム（React Flow標準機能）
- **カード自体のドラッグ**：好きな位置に動かして見やすく調整できる（見た目のみ・Notionには保存されない。ページ再読込で自動レイアウトに戻る）

## Phase B（進行中）

1. ✅ Notionに新DB「🏢 組織ロードマップ」を作成（名称／レベル／説明／進捗率／期限／責任者／優先度／ステータス／親・子（自己リレーション））。配置：「🧭 個人ゴールマップ」ページ直下
2. ✅ 既存🧭目標マップDBに「所属チーム」リレーションを追加し、Level2→Level3を接続。接続先はLevel2（チーム）に限らずLevel1（組織）も可（代理店チームを持たない今川は営業部に直接接続）
3. ✅ 実際の会社目標・組織名・チーム名をヒアリングし投入（`public/data/roadmap.json` を実データに更新済み。会社KGI：売上30億円＝代理店30社×平均年商1億円、期限2029-03-31）
4. ✅ `sync_notion_to_json.py`（`tools/goalmap/generate_goalmap.py` と同じ立て付け）でNotion→`roadmap.json`を生成するバッチを追加。実際の`NOTION_TOKEN`で実行確認済み（`public/data/roadmap.json`・`src/data/roadmap.sample.json`ともNotion発のデータに更新済み）
5. ⬜ Vercelへデプロイ（要 Vercel コネクタの認可）

### `sync_notion_to_json.py` の使い方

```bash
export NOTION_TOKEN=secret_xxx   # Notion Internal Integrationのトークン（要事前作成・DB共有）
python3 tools/roadmap/sync_notion_to_json.py
```
スクリプト冒頭のセットアップ手順（インテグレーション作成・DB共有）を参照。

### 新規メンバー・代理店を追加する手順

**A. 既存チームに新しいメンバーを1人追加する場合**
1. 🧭目標マップDBに新規ページを追加（テーマ／メンバー／ゴール等を通常どおり入力）
2. その行の「所属チーム」に、所属させたいチーム（例：wanny）を選択
3. `export NOTION_TOKEN=secret_xxx && python3 tools/roadmap/sync_notion_to_json.py` を実行 → `roadmap.json` に反映される

**B. 新しい代理店・チームを丸ごと追加する場合**
1. 🏢組織ロードマップDBに新規ページを追加：**名称**＝代理店名、**レベル**＝チーム、**責任者**＝担当者名、**親**＝営業部（または該当する組織）
2. そのチームの各メンバーの🧭目標マップ行で「所属チーム」に今追加したチームを設定（Aと同じ）
3. sync実行（Aと同じ）

**C. 新しい部署（組織）を追加する場合**
1. 🏢組織ロードマップDBに新規ページを追加：**名称**＝部署名、**レベル**＝組織、**親**＝E.L.P（会社）
2. 部署直下にチームを作る場合はBの手順で追加（親をその部署にする）
3. sync実行

いずれもコードは変更不要。Notion上の入力とsync実行だけで完結する。

## 関連

- 個人ゴールマップの仕様・運用：[`tools/goalmap/README.md`](../goalmap/README.md)、[`../../ゴールマップ運用.md`](../../ゴールマップ運用.md)
