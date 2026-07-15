import { useEffect, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import RoadmapCanvas from "./components/RoadmapCanvas";
import type { RoadmapNode } from "./types/roadmap";
import sampleData from "./data/roadmap.sample.json";

export default function App() {
  const [root, setRoot] = useState<RoadmapNode | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("./data/roadmap.json")
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then((data: RoadmapNode) => setRoot(data))
      .catch(() => {
        // public/data/roadmap.json が無い/読めない場合はバンドル同梱のサンプルにフォールバック
        setRoot(sampleData as unknown as RoadmapNode);
        setError("data/roadmap.json を取得できなかったため、同梱サンプルを表示しています。");
      });
  }, []);

  return (
    <div className="rm-app">
      <header className="rm-app-header">
        <h1>🗺️ E.L.P 組織ゴールマップ</h1>
        <span className="rm-sub">
          会社 › 組織 › チーム › 個人（クリックで個人ゴールマップへ）
        </span>
        {error && <span className="rm-sub" style={{ color: "#DC2626" }}>{error}</span>}
      </header>
      {root && (
        <ReactFlowProvider>
          <RoadmapCanvas root={root} />
        </ReactFlowProvider>
      )}
    </div>
  );
}
