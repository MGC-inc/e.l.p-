import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { RoadmapNode } from "../types/roadmap";
import { findAncestorChain, findNodeById, layoutTree } from "../lib/tree";
import CompanyNode from "./nodes/CompanyNode";
import DivisionNode from "./nodes/DivisionNode";
import TeamNode from "./nodes/TeamNode";
import PersonNode from "./nodes/PersonNode";
import Breadcrumb from "./Breadcrumb";
import type { RoadmapNodeData } from "./nodes/RoadmapNodeCard";

const nodeTypes = {
  0: CompanyNode,
  1: DivisionNode,
  2: TeamNode,
  3: PersonNode,
  4: PersonNode,
  5: PersonNode,
} as const;

function levelToType(level: number): string {
  return String(level);
}

interface Props {
  root: RoadmapNode;
}

export default function RoadmapCanvas({ root }: Props) {
  // 初期状態は空＝会社ノードのみ表示（部署は折りたたまれている）
  const [expandedPath, setExpandedPath] = useState<string[]>([]);
  const { fitView } = useReactFlow();
  const [zoomPct, setZoomPct] = useState(100);

  const expandedIds = useMemo(() => new Set(expandedPath), [expandedPath]);

  const { nodes: laidOut, edges: laidOutEdges } = useMemo(
    () => layoutTree(root, expandedIds),
    [root, expandedIds]
  );

  const rfNodes: Node[] = useMemo(
    () =>
      laidOut.map((n) => ({
        id: n.node.id,
        type: levelToType(n.node.level),
        position: { x: n.x, y: n.y },
        data: { node: n.node, hasChildren: n.hasChildren, isExpanded: n.isExpanded } as RoadmapNodeData,
      })),
    [laidOut]
  );

  const rfEdges: Edge[] = useMemo(
    () =>
      laidOutEdges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: "smoothstep",
        style: { stroke: "#9AA3AD", strokeWidth: 1.6 },
      })),
    [laidOutEdges]
  );

  const breadcrumbNodes: RoadmapNode[] = useMemo(() => {
    const p = expandedPath.map((id) => findNodeById(root, id)).filter((n): n is RoadmapNode => !!n);
    return p.length ? p : [root]; // 何も展開していない時も「会社」だけは常に表示
  }, [expandedPath, root]);

  const applyPath = useCallback(
    (nextPath: string[]) => {
      setExpandedPath(nextPath);
      requestAnimationFrame(() => fitView({ duration: 420, padding: 0.25 }));
    },
    [fitView]
  );

  const handleNodeClick = useCallback(
    (_: unknown, node: Node) => {
      const data = node.data as RoadmapNodeData;
      const target = data.node;
      if (!data.hasChildren) {
        if (target.externalLink) window.location.href = target.externalLink;
        return;
      }
      const chain = findAncestorChain(root, target.id);
      if (!chain) return;
      const isSameAsCurrentDeepest =
        expandedPath.length === chain.length && expandedPath[expandedPath.length - 1] === target.id;
      applyPath(isSameAsCurrentDeepest ? chain.slice(0, -1) : chain);
    },
    [root, expandedPath, applyPath]
  );

  const handleBreadcrumbJump = useCallback(
    (id: string) => {
      const chain = findAncestorChain(root, id);
      if (chain) applyPath(chain);
    },
    [root, applyPath]
  );

  useEffect(() => {
    requestAnimationFrame(() => fitView({ duration: 0, padding: 0.25 }));
  }, [fitView]);

  return (
    <div className="rm-canvas-wrap">
      <Breadcrumb path={breadcrumbNodes} onJump={handleBreadcrumbJump} />
      <div className="rm-hud">
        <span className="rm-hud-word">
          {breadcrumbNodes[breadcrumbNodes.length - 1]?.title ?? root.title}
        </span>
        <span className="rm-hud-pct">{zoomPct}%</span>
        <span className="rm-hud-hint">クリックで展開・スクロール/ピンチでズーム</span>
      </div>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        onMove={(_, viewport) => setZoomPct(Math.round(viewport.zoom * 100))}
        nodesConnectable={false}
        elementsSelectable={true}
        minZoom={0.15}
        maxZoom={2}
        fitView
      >
        <Background gap={28} color="#E3E7EB" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
