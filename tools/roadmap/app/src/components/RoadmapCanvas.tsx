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

// このブラウザ（＝この端末を使う本人）が最後に見ていたノードを覚えておき、
// 再訪時に会社ノードまで折りたたまれた状態へ毎回リセットされないようにする。
const LAST_NODE_KEY = "elp-roadmap:lastNodeId";

function loadInitialPath(root: RoadmapNode): string[] {
  try {
    const savedId = localStorage.getItem(LAST_NODE_KEY);
    if (!savedId) return [];
    return findAncestorChain(root, savedId) ?? [];
  } catch {
    return [];
  }
}

export default function RoadmapCanvas({ root }: Props) {
  // 初期状態は前回このブラウザで最後に開いていた場所を復元（無ければ会社ノードのみ）
  const [expandedPath, setExpandedPath] = useState<string[]>(() => loadInitialPath(root));
  const { fitView } = useReactFlow();
  const [zoomPct, setZoomPct] = useState(100);
  // ドラッグで動かした位置（見た目の調整のみ・保存はしない。ページ再読込で自動レイアウトに戻る）
  const [dragOverrides, setDragOverrides] = useState<Record<string, { x: number; y: number }>>({});

  useEffect(() => {
    setDragOverrides({});
  }, [root]);

  useEffect(() => {
    try {
      if (expandedPath.length > 0) {
        localStorage.setItem(LAST_NODE_KEY, expandedPath[expandedPath.length - 1]);
      } else {
        localStorage.removeItem(LAST_NODE_KEY);
      }
    } catch {
      // localStorageが使えない環境（プライベートブラウズ等）では何もしない
    }
  }, [expandedPath]);

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
        position: dragOverrides[n.node.id] ?? { x: n.x, y: n.y },
        data: { node: n.node, hasChildren: n.hasChildren, isExpanded: n.isExpanded } as RoadmapNodeData,
      })),
    [laidOut, dragOverrides]
  );

  const handleNodeDragStop = useCallback((_: unknown, node: Node) => {
    setDragOverrides((prev) => ({ ...prev, [node.id]: node.position }));
  }, []);

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
        <span className="rm-hud-hint">クリックで展開・ドラッグで位置調整・スクロール/ピンチでズーム</span>
      </div>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        onNodeDragStop={handleNodeDragStop}
        onMove={(_, viewport) => setZoomPct(Math.round(viewport.zoom * 100))}
        nodesDraggable={true}
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
