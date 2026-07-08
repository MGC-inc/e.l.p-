# 🗺️ 組織ゴールマップ（tools/roadmap/）

会社→組織→チーム→個人と、Googleマップのようにズーム（クリックで展開）しながら辿れる組織全体のゴールマップ。個人レベルまで来ると、既存の [`tools/goalmap/goalmap-studio.html`](../goalmap/goalmap-studio.html)（アバター成長・大枠/中目標の北極星・MT議事録録音・連続ズームなど）にそのまま遷移する。

> 個人ゴールマップ（`tools/goalmap/`）は**引き続きオフライン単体HTMLとして独立に配布・編集**できる。今回追加した組織ビュー（このディレクトリ）は**ビルドが必要**で、Vercel等にホスティングして使う別アプリ。

## 現在の実装状況（Phase A）

- ✅ React + TypeScript + [`@xyflow/react`](https://reactflow.dev/)（React Flow）でLevel0〜3（会社/組織/チーム/個人）の展開・パン/ズーム・パンくずを実装
- ✅ 個人（Level3）ノードをクリックすると `goalmap-studio.html?member=<名前>` へ遷移し、studio側で自動的にそのメンバーが選択される
- ✅ データは `public/data/roadmap.json`（静的JSON）を起動時にfetch。**サンプル/プレースホルダデータ**（会社目標・組織4部署・チーム分けは仮）
- ⬜ Notion「🏢 組織ロードマップ」DBの実作成・実データ投入（Phase B）
- ⬜ Vercel等への実デプロイ（Phase B・Vercelコネクタの認可が必要）
- ⬜ Level4（タスク）/Level5（サブタスク）専用UI（当面は個人ゴールマップ内のフェーズ/タスクで代替）

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
- **パンくず**（左上）：今どの階層にいるか常時表示。クリックでその階層まで一気に戻れる
- **スクロール/ピンチ/ドラッグ**：地図のような連続パン・ズーム（React Flow標準機能）

## Phase B（次のステップ・未着手）

1. Notionに新DB「🏢 組織ロードマップ」を作成（タイトル／レベル／説明／進捗率／期限／担当／優先度／ステータス／親（自己リレーション））
2. 既存🧭目標マップDBに「所属チーム」リレーションを追加し、Level2→Level3を接続
3. 実際の会社目標・組織名・チーム名をヒアリングし投入（`public/data/roadmap.json` はプレースホルダ）
4. `sync_notion_to_json.py`（`tools/goalmap/generate_goalmap.py` と同じ立て付け）でNotion→`roadmap.json`を生成するバッチを追加
5. Vercelへデプロイ（要 Vercel コネクタの認可）

## 関連

- 個人ゴールマップの仕様・運用：[`tools/goalmap/README.md`](../goalmap/README.md)、[`../../ゴールマップ運用.md`](../../ゴールマップ運用.md)
