-- 商談録音分析: LINE受信まわりのテーブル定義
-- Supabaseダッシュボードの SQL Editor でこのファイルの内容を実行してください。
-- 実行後、Storageで「deal-recordings」バケットを作成してください（SQLでは作れません。
-- Storage > New bucket > 名前: deal-recordings。Publicにする必要はありません）。

create table if not exists closer_line_users (
  id bigint generated always as identity primary key,
  line_user_id text not null unique,
  display_name text,               -- LINEの表示名（自動取得・仮登録時に埋まる）
  closer_name text,                -- クローザーとしての氏名。今川さんが手動で確定させる
  goalmap_member_name text,        -- tools/goalmap/members/<この名前>.json に対応
  created_at timestamptz not null default now()
);

create table if not exists deal_recordings (
  id bigint generated always as identity primary key,
  line_user_id text not null,
  closer_name text,
  appointer text,
  customer_name text,              -- 「〇〇邸」の形式
  result text,                     -- 契約/保留/失注/クーリングオフ/審査落ち/キャンセル
  storage_path text not null,      -- deal-recordings バケット内のパス
  status text not null default 'awaiting_appointer',
  -- awaiting_appointer -> awaiting_customer -> awaiting_result -> awaiting_confirm -> ready/skipped -> done
  notion_url text,
  received_at timestamptz not null default now()
);

create index if not exists deal_recordings_line_user_status_idx
  on deal_recordings (line_user_id, status);

create index if not exists deal_recordings_status_idx
  on deal_recordings (status);
