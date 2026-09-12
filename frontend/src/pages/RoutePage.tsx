import { useEffect, useMemo, useState } from "react";
import { getFloorplanGraph, getRoute, rasterUrl } from "../api/client";
import type { RouteResponse } from "../api/client";
import type { FloorGraph } from "../types/graph";
import { usesPassageLines } from "../types/graph";
import { FloorPlanCanvas } from "../components/FloorPlanCanvas";
import { RoomPicker } from "../components/RoomPicker";
import { DirectionsPanel } from "../components/DirectionsPanel";

interface RoutePageProps {
  floorplanId: string;
}

export function RoutePage({ floorplanId }: RoutePageProps) {
  const [graph, setGraph] = useState<FloorGraph | null>(null);
  const [graphSource, setGraphSource] = useState<"draft" | "graph" | null>(null);
  const [raster, setRaster] = useState<string | null>(null);
  const [fromId, setFromId] = useState<string | null>(null);
  const [toId, setToId] = useState<string | null>(null);
  const [route, setRoute] = useState<RouteResponse | null>(null);
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showNodes, setShowNodes] = useState(true);

  useEffect(() => {
    getFloorplanGraph(floorplanId).then((res) => {
      setGraph(res.graph);
      setGraphSource(res.source);
      setRaster(res.raster_path);
    });
  }, [floorplanId]);

  const rooms = useMemo(() => (graph ? graph.nodes.filter((n) => n.type === "room") : []), [graph]);

  async function handleFindRoute() {
    if (!fromId || !toId) return;
    setLoading(true);
    setError(null);
    setRoute(null);
    try {
      const result = await getRoute(fromId, toId);
      setRoute(result);
      setCurrentStepIndex(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Route lookup failed.");
    } finally {
      setLoading(false);
    }
  }

  if (!graph || !raster) return <p>Loading floor plan...</p>;

  const highlightedPath = route?.path.map((p) => p.node_id) ?? [];
  const currentStepNodeId = route?.deterministic_directions[currentStepIndex]?.node_id ?? null;

  const noGraphSaved = graphSource === "draft";
  const graphHasNoEdges = graphSource === "graph" && graph.edges.length === 0;
  const routingBlocked = noGraphSaved || graphHasNoEdges || !usesPassageLines(graph.routing_source);

  return (
    <div className="route-page">
      {routingBlocked && (
        <p className="route-warning">
          {noGraphSaved
            ? "No saved floor graph yet — open the Graph Editor and build the whole-floor passage routes with Gemini before routing."
            : !usesPassageLines(graph.routing_source)
              ? "Build the whole-floor passage routes with Gemini in the Graph Editor before routing."
              : "This floor's saved graph has no connections yet — open the Graph Editor and rebuild its passage routes."}
        </p>
      )}
      {!routingBlocked && usesPassageLines(graph.routing_source) ? (
        <p className="route-info">Routes follow the saved passage lines through room doors.</p>
      ) : !routingBlocked && graph.auto_generated && (
        <p className="route-info">
          This corridor graph was auto-generated from detected doors, not hand-verified — routes
          may take a slightly indirect path. Open the Graph Editor to refine specific connections
          if one looks wrong.
        </p>
      )}
      <div className="route-controls">
        <RoomPicker label="From" rooms={rooms} selectedNodeId={fromId} onSelect={setFromId} />
        <RoomPicker label="To" rooms={rooms} selectedNodeId={toId} onSelect={setToId} />
        <button type="button" onClick={handleFindRoute} disabled={!fromId || !toId || loading || routingBlocked}>
          {loading ? "Finding route..." : "Find route"}
        </button>
        <button
          type="button"
          className={showNodes ? "active" : ""}
          aria-pressed={showNodes}
          onClick={() => setShowNodes((visible) => !visible)}
        >
          {showNodes ? "Hide nodes" : "Show nodes"}
        </button>
        {route && (
          <p className="route-summary">
            Total distance: {route.total_weight.toFixed(1)} pt · directions:{" "}
            {route.directions_source}
          </p>
        )}
        {error && <p className="error-text">{error}</p>}
      </div>

      <div className="route-body">
        <FloorPlanCanvas
          rasterUrl={rasterUrl(raster)}
          nodes={graph.nodes}
          edges={graph.edges}
          editable={false}
          showNodes={showNodes}
          showCorridorNodes={false}
          passageLines={usesPassageLines(graph.routing_source)}
          viewBoxWidth={graph.page_width}
          viewBoxHeight={graph.page_height}
          passageways={graph.passageways}
          doorAttachments={graph.door_attachments}
          highlightedPoints={route?.path}
          highlightedPath={highlightedPath}
          currentStepNodeId={currentStepNodeId}
        />
        {route && (
          <DirectionsPanel
            steps={route.deterministic_directions}
            phrasedText={route.phrased_directions}
            currentStepIndex={currentStepIndex}
            onStepSelect={setCurrentStepIndex}
          />
        )}
      </div>
    </div>
  );
}
