import type { RoadmapNode } from "../types/roadmap";

/** root から targetId までの祖先チェーン（自身を含む）。見つからなければ null。 */
export function findAncestorChain(
  node: RoadmapNode,
  targetId: string,
  trail: string[] = []
): string[] | null {
  const nextTrail = [...trail, node.id];
  if (node.id === targetId) return nextTrail;
  for (const child of node.children ?? []) {
    const r = findAncestorChain(child, targetId, nextTrail);
    if (r) return r;
  }
  return null;
}

export function findNodeById(node: RoadmapNode, id: string): RoadmapNode | null {
  if (node.id === id) return node;
  for (const child of node.children ?? []) {
    const r = findNodeById(child, id);
    if (r) return r;
  }
  return null;
}

export interface LaidOutNode {
  node: RoadmapNode;
  x: number;
  y: number;
  hasChildren: boolean;
  isExpanded: boolean;
}

export interface LaidOutEdge {
  id: string;
  source: string;
  target: string;
}

const NODE_W = 240;
const NODE_VGAP = 190;

/**
 * expandedIds に含まれるノードだけ子を展開して描画する木レイアウト。
 * サブツリー幅で中央寄せする単純なツリーレイアウト（外部ライブラリ不使用）。
 */
export function layoutTree(
  root: RoadmapNode,
  expandedIds: Set<string>
): { nodes: LaidOutNode[]; edges: LaidOutEdge[] } {
  const nodes: LaidOutNode[] = [];
  const edges: LaidOutEdge[] = [];
  const cursor = { value: 0 };

  function visit(node: RoadmapNode, depth: number): { minX: number; maxX: number } {
    const hasChildren = !!node.children?.length;
    const isExpanded = hasChildren && expandedIds.has(node.id);

    if (!isExpanded) {
      const x = cursor.value * NODE_W;
      cursor.value += 1;
      nodes.push({ node, x, y: depth * NODE_VGAP, hasChildren, isExpanded });
      return { minX: x, maxX: x };
    }

    const childCenters: number[] = [];
    for (const child of node.children ?? []) {
      edges.push({ id: `e-${node.id}-${child.id}`, source: node.id, target: child.id });
      const r = visit(child, depth + 1);
      childCenters.push((r.minX + r.maxX) / 2);
    }
    const x = (childCenters[0] + childCenters[childCenters.length - 1]) / 2;
    nodes.push({ node, x, y: depth * NODE_VGAP, hasChildren, isExpanded });
    return { minX: childCenters[0], maxX: childCenters[childCenters.length - 1] };
  }

  visit(root, 0);
  return { nodes, edges };
}
