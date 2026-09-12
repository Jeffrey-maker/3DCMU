import { useEffect, useMemo, useState } from "react";
import { getFloorplanGraph, getRoute, listFloorplans, rasterUrl } from "../api/client";
import type { RoutePathNode, RouteResponse } from "../api/client";
import type { FloorGraph, GraphNode } from "../types/graph";

// Stairs and lifts are worth picking too -- "take me to the lift".
const SELECTABLE_TYPES = new Set(["room", "stair", "elevator"]);
import { usesPassageLines } from "../types/graph";
import { FloorPlanCanvas } from "../components/FloorPlanCanvas";
import { RoomPicker } from "../components/RoomPicker";
import { DirectionsPanel } from "../components/DirectionsPanel";

interface RoutePageProps {
  floorplanId: string;
}

interface LoadedFloor {
  graph: FloorGraph;
  raster: string;
}

interface RouteLeg {
  building: string;
  floor: number;
  plan: LoadedFloor;
  points: RoutePathNode[];
  nodes: GraphNode[];
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
  // A route can leave this floor, so every floor's plan and graph are kept:
  // the pickers need their spaces, and a cross-floor route needs to draw
  // each leg on the floor it actually happens on.
  const [floors, setFloors] = useState<LoadedFloor[]>([]);

  useEffect(() => {
    getFloorplanGraph(floorplanId).then((res) => {
      setGraph(res.graph);
      setGraphSource(res.source);
      setRaster(res.raster_path);
    });
  }, [floorplanId]);

  useEffect(() => {
    let cancelled = false;
    listFloorplans()
      .then(async (floorplans) => {
        const loaded = await Promise.all(
          floorplans.map(async (f) => {
            const res = await getFloorplanGraph(f.floorplan_id).catch(() => null);
            return res ? { graph: res.graph, raster: res.raster_path } : null;
          }),
        );
        if (cancelled) return;
        setFloors(loaded.filter((f): f is LoadedFloor => f !== null && f.raster !== null));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // Fall back to the open floor until the full list arrives.
  const rooms = useMemo(
    () =>
      floors.length > 0
        ? floors.flatMap((f) => f.graph.nodes.filter((n) => SELECTABLE_TYPES.has(n.type)))
        : graph
          ? graph.nodes.filter((n) => SELECTABLE_TYPES.has(n.type))
          : [],
    [floors, graph],
  );
  const multiFloor = useMemo(() => new Set(rooms.map((r) => r.floor)).size > 1, [rooms]);

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

  /**
   * Split the route where it changes floor, keeping walking order.
   *
   * Each leg is drawn on its own floor's plan because the two drawings
   * share no coordinate frame -- a single canvas would put the upstairs
   * half at meaningless positions, and a line joining the two would cross
   * nothing real. Declared before the loading guard so hook order is stable.
   */
  const legs = useMemo<RouteLeg[]>(() => {
    if (!route) return [];
    const planKey = (building: string, floor: number) => `${building}:${floor}`;
    const byPlan = new Map(
      floors.map((f) => [planKey(f.graph.building, f.graph.floor), f]),
    );
    const runs: { building: string; floor: number; points: RoutePathNode[] }[] = [];
    for (const point of route.path) {
      const last = runs[runs.length - 1];
      if (!last || last.floor !== point.floor || last.building !== point.building) {
        runs.push({ building: point.building, floor: point.floor, points: [point] });
      }
      else last.points.push(point);
    }
    const marked = new Set(["room", "stair", "elevator"]);
    return runs.flatMap((run) => {
      const floor = byPlan.get(planKey(run.building, run.floor)) ?? (
        graph?.building === run.building && graph.floor === run.floor && raster
        ? { graph, raster }
        : null);
      if (!floor) return [];
      const ids = new Set(run.points.filter((p) => marked.has(p.type)).map((p) => p.node_id));
      return [{
        building: run.building,
        floor: run.floor,
        plan: floor,
        points: run.points,
        nodes: floor.graph.nodes.filter((n) => ids.has(n.id)),
      }];
    });
  }, [route, floors, graph, raster]);

  if (!graph || !raster) return <p>Loading floor plan...</p>;

  const highlightedPath = route?.path.map((p) => p.node_id) ?? [];
  const routePlans = [
    ...new Map(
      route?.path.map((p) => [
        `${p.building}:${p.floor}`,
        { building: p.building, floor: p.floor },
      ]) ?? [],
    ).values(),
  ];
  const transfers =
    route?.path.filter((p) => p.type === "stair" || p.type === "elevator").map((p) => p.label) ?? [];
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
        <RoomPicker
          label="From"
          rooms={rooms}
          selectedNodeId={fromId}
          onSelect={setFromId}
          showFloor={multiFloor}
        />
        <RoomPicker
          label="To"
          rooms={rooms}
          selectedNodeId={toId}
          onSelect={setToId}
          showFloor={multiFloor}
        />
        <button type="button" onClick={handleFindRoute} disabled={!fromId || !toId || loading || routingBlocked}>
          {loading ? "Finding route..." : "Find route"}
        </button>
        {route && (
          <p className="route-summary">
            Total distance: {route.total_weight.toFixed(1)} pt · directions:{" "}
            {route.directions_source}
          </p>
        )}
        {route && routePlans.length > 1 && (
          <p className="route-info">
            Crosses {routePlans.map((p) => `${p.building} floor ${p.floor}`).join(" \u2192 ")}
            {transfers.length > 0 && <> via {transfers.join(", ")}</>} — each floor is drawn below
            in walking order.
          </p>
        )}
        {error && <p className="error-text">{error}</p>}
      </div>

      <div className="route-body">
        {/* Until a route exists this is just the floor plan: no anchors, no
            passage lines, nothing to read past. Once one is found, each floor
            it passes through is drawn in walking order -- start floor first. */}
        <div className="route-floors">
          {legs.length === 0 ? (
            <FloorPlanCanvas
              rasterUrl={rasterUrl(raster)}
              nodes={[]}
              edges={[]}
              editable={false}
              showNodes={false}
              showCorridorNodes={false}
              passageLines={false}
              viewBoxWidth={graph.page_width}
              viewBoxHeight={graph.page_height}
              passageways={[]}
              doorAttachments={[]}
            />
          ) : (
            legs.map((leg, index) => (
              <figure className="route-floor" key={`${leg.building}-${leg.floor}-${index}`}>
                {legs.length > 1 && (
                  <figcaption>
                    {index === 0
                      ? `${leg.building} floor ${leg.floor} — start`
                      : index === legs.length - 1
                        ? `${leg.building} floor ${leg.floor} — destination`
                        : `${leg.building} floor ${leg.floor}`}
                  </figcaption>
                )}
                <FloorPlanCanvas
                  rasterUrl={rasterUrl(leg.plan.raster)}
                  nodes={leg.nodes}
                  edges={[]}
                  editable={false}
                  showNodes={leg.nodes.length > 0}
                  showCorridorNodes={false}
                  passageLines={false}
                  viewBoxWidth={leg.plan.graph.page_width}
                  viewBoxHeight={leg.plan.graph.page_height}
                  passageways={[]}
                  doorAttachments={[]}
                  highlightedPoints={leg.points}
                  highlightedPath={highlightedPath}
                  currentStepNodeId={currentStepNodeId}
                />
              </figure>
            ))
          )}
        </div>
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
