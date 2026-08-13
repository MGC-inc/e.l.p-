import { useEffect, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import RoadmapCanvas from "./components/RoadmapCanvas";
import type { RoadmapNode } from "./types/roadmap";
import sampleData from "./data/roadmap.sample.json";

export default function App() {
  const [root, setRoot] = useState<RoadmapNode | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // ?agency=<代理店トークン> があれば、その代理店専用の部分木だけを取得する
    // （全社版roadmap.jsonへは絶対にフォールバックしない＝他代理店データの漏洩防止）
    const agency = new URLSearchParams(window.location.search).get("agency");
    const dataUrl = agency ? `./data/roadmap-${agency}.json` : "./data/roadmap.json";

    fetch(dataUrl)
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then((data: RoadmapNode) => setRoot(data))
      .catch(() => {
        if (agency) {
          setError("このリンクのデータを取得できませんでした。URLをご確認ください。");
          return;
        }
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
