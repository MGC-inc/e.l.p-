import { LEVEL_ICON, type RoadmapNode } from "../types/roadmap";

interface Props {
  /** root を含む、現在展開中のノードの祖先チェーン */
  path: RoadmapNode[];
  onJump: (id: string) => void;
}

export default function Breadcrumb({ path, onJump }: Props) {
  return (
    <div className="rm-breadcrumb" role="navigation" aria-label="階層パンくず">
      {path.map((n, i) => (
        <span key={n.id} className="rm-breadcrumb-item">
          {i > 0 && <span className="rm-breadcrumb-sep">›</span>}
          <button className="rm-breadcrumb-btn" onClick={() => onJump(n.id)}>
            {LEVEL_ICON[n.level]} {n.title}
          </button>
        </span>
      ))}
    </div>
  );
}
