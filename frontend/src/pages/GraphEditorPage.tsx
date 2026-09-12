import { useEffect, useRef, useState } from "react";
import {
  buildPassageRoutes,
  getFloorplanGraph,
  rasterUrl,
  saveFloorplanGraph,
} from "../api/client";
import type { FloorGraph } from "../types/graph";
import { usesPassageLines } from "../types/graph";
import { FloorPlanCanvas } from "../components/FloorPlanCanvas";
import { FloorTabs } from "../components/FloorTabs";

interface GraphEditorPageProps {
  floorplanId: string;
}

type Status = "loading" | "ready" | "saving" | "error";

export function GraphEditorPage({ floorplanId }: GraphEditorPageProps) {
  const [graph, setGraph] = useState<FloorGraph | null>(null);
  const [raster, setRaster] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [message, setMessage] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const activeFloor = useRef(floorplanId);

  useEffect(() => {
    let cancelled = false;
    activeFloor.current = floorplanId;
    setMessage(null);
    setAnalyzing(false);
    setStatus("loading");
    getFloorplanGraph(floorplanId)
      .then((res) => {
        if (cancelled) return;
        setGraph(res.graph);
        setRaster(res.raster_path);
        setStatus("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setStatus("error");
        setMessage(err instanceof Error ? err.message : "Failed to load floorplan.");
      });
    return () => {
      cancelled = true;
      activeFloor.current = "";
    };
  }, [floorplanId]);

  async function handleSave() {
    if (!graph) return;
    setStatus("saving");
    try {
      const result = await saveFloorplanGraph(floorplanId, graph);
      setStatus("ready");
      setMessage(`Saved: ${result.node_count} nodes, ${result.edge_count} edges.`);
    } catch (err) {
      setStatus("ready");
      setMessage(err instanceof Error ? err.message : "Save failed.");
    }
  }

  async function handleGeminiAnalysis(refresh = false) {
    if (!graph) return;
    setAnalyzing(true);
    setMessage("Tracing passage lines and connecting rooms through their doors...");
    try {
      const result = await buildPassageRoutes(floorplanId, graph, refresh);
      if (activeFloor.current !== floorplanId) return;
      setGraph(result.graph);
      setMessage(
        `${result.used_cached_analysis ? "Reused saved" : "Gemini traced"} passage lines. ` +
          `${result.report.connected_room_count} rooms connected; ` +
          `${result.report.unconnected_room_count} need attention. Routes saved (${result.model}).`,
      );
    } catch (err) {
      if (activeFloor.current !== floorplanId) return;
      setMessage(err instanceof Error ? err.message : "Gemini analysis failed.");
    } finally {
      if (activeFloor.current === floorplanId) setAnalyzing(false);
    }
  }

  if (status === "loading") return <p>Loading floor plan...</p>;
  if (status === "error" || !graph || !raster) {
    return <p className="error-text">{message ?? "Error loading floor plan."}</p>;
  }

  return (
    <div className="graph-editor-page">
      <div className="editor-header">
        <h2>
          Graph editor — {graph.building} floor {graph.floor}
        </h2>
        <FloorTabs floors={[graph.floor]} selectedFloor={graph.floor} onSelectFloor={() => {}} />
        <button type="button" onClick={handleSave} disabled={status === "saving" || analyzing}>
          {status === "saving" ? "Saving..." : "Save graph"}
        </button>
        <button type="button" onClick={() => handleGeminiAnalysis()} disabled={analyzing || status === "saving"}>
          {analyzing ? "Building passage routes..." : "Build passage routes"}
        </button>
        {usesPassageLines(graph.routing_source) && (
          <>
            <button type="button" onClick={() => handleGeminiAnalysis(true)} disabled={analyzing || status === "saving"}>
              Retrace passages
            </button>
          </>
        )}
      </div>
      <p className="page-hint">
        Blue nodes belong inside rooms; green nodes mark doors; purple marks stairs and
        elevators, which is how a route reaches another floor. You can move any of them, add a
        missing room or door, or correct a room-to-door edge. Public passageways are stored as
        lines, not orange corridor nodes or direct door-to-door edges.
      </p>
      <p className="page-hint">
        With a space-type PDF uploaded, passage lines are computed directly from its Public
        Corridor areas — exact and identical on every run. Without one, Gemini traces them from the
        floor-plan image instead. Building routes then connects each room through its door to the
        closest reachable line and saves the result.
      </p>
      {usesPassageLines(graph.routing_source) ? (
        <p className="route-info">Blue: rooms · Green: doors · Purple: stairs/elevators · Amber lines: passages. Routes follow room → door → passage → door → room.</p>
      ) : graph.auto_generated && (
        <p className="route-info">
          Room and door anchors are ready. Build the passage routes to trace and store the
          whole-floor public circulation network.
        </p>
      )}
      {message && <p className="editor-message">{message}</p>}
      {!!graph.routing_warnings?.length && (
        <details className="route-warning">
          <summary>Passage connections to check ({graph.routing_warnings.length})</summary>
          <ul>{graph.routing_warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>
        </details>
      )}
      <FloorPlanCanvas
        rasterUrl={rasterUrl(raster)}
        nodes={graph.nodes}
        edges={graph.edges}
        viewBoxWidth={graph.page_width}
        viewBoxHeight={graph.page_height}
        editable={!analyzing && status !== "saving"}
        showCorridorNodes={false}
        passageLines={usesPassageLines(graph.routing_source)}
        hideHallwayEdges={!usesPassageLines(graph.routing_source)}
        passageways={graph.passageways}
        doorAttachments={graph.door_attachments}
        onNodesChange={(nodes) => setGraph((current) => current ? { ...current, nodes } : current)}
        onEdgesChange={(edges) => setGraph((current) => current ? { ...current, edges } : current)}
      />
    </div>
  );
}
