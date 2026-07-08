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
  children?: RoadmapNode[];
}

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
