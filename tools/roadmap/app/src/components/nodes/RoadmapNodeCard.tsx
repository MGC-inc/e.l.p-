import { Handle, Position, type NodeProps } from "@xyflow/react";
import { GROWTH_EMOJI, LEVEL_ICON, LEVEL_LABEL, type RoadmapNode } from "../../types/roadmap";

export interface RoadmapNodeData {
  node: RoadmapNode;
  hasChildren: boolean;
  isExpanded: boolean;
  [key: string]: unknown;
}

const STATUS_LABEL: Record<string, string> = {
  not_started: "未着手",
  in_progress: "進行中",
  done: "完了",
  at_risk: "遅延",
};

export default function RoadmapNodeCard({ data }: NodeProps & { data: RoadmapNodeData }) {
  const { node, hasChildren, isExpanded } = data;
  const isLeafPerson = node.level === 3 && !hasChildren && node.externalLink;
  const hasGrowth =
    node.level === 3 && typeof node.growthLevel === "number";
  const growthEmoji = hasGrowth
    ? GROWTH_EMOJI[Math.max(0, Math.min(9, node.growthLevel as number))]
    : "";

  return (
    <div className={`rm-card rm-lvl-${node.level}${isExpanded ? " rm-expanded" : ""}`}>
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <div className="rm-card-head">
        <span className="rm-card-icon">{LEVEL_ICON[node.level]}</span>
        <span className="rm-card-level">{LEVEL_LABEL[node.level]}</span>
        {hasGrowth && (
          <span
            className="rm-card-growth"
            title={`成長レベル Lv.${node.growthLevel} ${node.growthForm ?? ""}`}
          >
            {growthEmoji} Lv.{node.growthLevel}
            {node.growthForm ? ` ${node.growthForm}` : ""}
          </span>
        )}
        {hasChildren && <span className="rm-card-toggle">{isExpanded ? "－" : "＋"}</span>}
        {isLeafPerson && <span className="rm-card-toggle">↗</span>}
      </div>
      <div className="rm-card-title">{node.title}</div>
      {node.description && <div className="rm-card-desc">{node.description}</div>}
      <div className="rm-card-meta">
        {typeof node.progress === "number" && (
          <div className="rm-card-progress">
            <div className="rm-card-progress-bar" style={{ width: `${node.progress}%` }} />
            <span>{node.progress}%</span>
          </div>
        )}
        {node.status && <span className={`rm-badge rm-badge-${node.status}`}>{STATUS_LABEL[node.status] ?? node.status}</span>}
        {node.deadline && <span className="rm-card-deadline">〆{node.deadline}</span>}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}
