// 会社→組織→チーム→個人→タスク→サブタスク まで無限に階層化できる汎用ノード型。
// Level0=会社 / 1=組織 / 2=チーム / 3=個人 / 4=タスク / 5=サブタスク
export type RoadmapLevel = 0 | 1 | 2 | 3 | 4 | 5;

export type RoadmapStatus = "not_started" | "in_progress" | "done" | "at_risk";
export type RoadmapPriority = "low" | "medium" | "high";

export interface RoadmapNode {
  id: string;
  level: RoadmapLevel;
  title: string;
  description?: string;
  /** 0-100 */
  progress?: number;
  deadline?: string;
  owner?: string;
  priority?: RoadmapPriority;
  status?: RoadmapStatus;
  /**
   * Level3（個人）ノードのみ：クリック時に遷移する先
   * 例 "/goalmap-studio.html?member=岡野"
   */
  externalLink?: string;
  /** Level3（個人）のみ：成長レベル（鯉→龍・0-9）。studio/members/*.json の level が正。 */
  growthLevel?: number;
  /** Level3（個人）のみ：成長ステージ名（例 "青龍"）。growthLevel と対応。 */
  growthForm?: string;
  children?: RoadmapNode[];
}

/** 成長ステージ（Lv0-9）の絵文字。育成ロードマップ基準書と同一。 */
export const GROWTH_EMOJI = [
  "🥚", "🐟", "🐠", "🐡", "🎏", "🎨", "🌊", "🐲", "🐉", "👑",
] as const;

export const LEVEL_LABEL: Record<RoadmapLevel, string> = {
  0: "会社",
  1: "組織",
  2: "チーム",
  3: "個人",
  4: "タスク",
  5: "サブタスク",
};

export const LEVEL_ICON: Record<RoadmapLevel, string> = {
  0: "🏢",
  1: "🧭",
  2: "👥",
  3: "🧑",
  4: "📋",
  5: "🔹",
};
