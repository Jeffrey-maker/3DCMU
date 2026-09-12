import type { FloorGraph, RoutingSource } from "../types/graph";

const API_BASE = "http://localhost:8000";

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export interface UploadResponse {
  floorplan_id: string;
  room_count: number;
  raw_geometry_count: number;
  raster_path: string;
  existing_graph_preserved: boolean;
  corridor_graph: "auto_generated" | "preserved";
}

export interface StoredFloorplan {
  floorplan_id: string;
  building: string;
  floor: number;
  raster_path: string | null;
  has_graph: boolean;
  routing_source: RoutingSource | null;
  node_count: number;
  edge_count: number;
}

export async function listFloorplans(): Promise<StoredFloorplan[]> {
  const res = await fetch(`${API_BASE}/api/floorplans`);
  return (await unwrap<{ floorplans: StoredFloorplan[] }>(res)).floorplans;
}

export async function uploadFloorplan(
  building: string,
  floor: number,
  files: { base: File; dept?: File; type?: File },
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("building", building);
  form.append("floor", String(floor));
  form.append("base", files.base);
  if (files.dept) form.append("dept", files.dept);
  if (files.type) form.append("type", files.type);

  const res = await fetch(`${API_BASE}/api/floorplans/upload`, { method: "POST", body: form });
  return unwrap<UploadResponse>(res);
}

export interface GraphResponse {
  source: "draft" | "graph";
  graph: FloorGraph;
  raster_path: string;
}

export async function getFloorplanGraph(floorplanId: string): Promise<GraphResponse> {
  const res = await fetch(`${API_BASE}/api/floorplans/${floorplanId}/graph`);
  return unwrap<GraphResponse>(res);
}

export interface SaveGraphResponse {
  saved: boolean;
  node_count: number;
  edge_count: number;
}

export async function saveFloorplanGraph(
  floorplanId: string,
  graph: FloorGraph,
): Promise<SaveGraphResponse> {
  const res = await fetch(`${API_BASE}/api/floorplans/${floorplanId}/graph`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(graph),
  });
  return unwrap<SaveGraphResponse>(res);
}

export interface GeminiAnalysisResponse {
  saved: boolean;
  status: "advisory_only";
  model: string;
  room_node_count: number;
  door_count: number;
  pathway_count: number;
  barrier_count: number;
  analysis: unknown;
}

export async function analyzeFloorplanWithGemini(
  floorplanId: string,
): Promise<GeminiAnalysisResponse> {
  const res = await fetch(`${API_BASE}/api/floorplans/${floorplanId}/analyze-gemini`, {
    method: "POST",
  });
  return unwrap<GeminiAnalysisResponse>(res);
}

export interface PassageBuildResponse {
  saved: boolean;
  graph: FloorGraph;
  model: string;
  created_at: string;
  used_cached_analysis: boolean;
  report: {
    pathway_count: number;
    connected_room_count: number;
    unconnected_room_count: number;
    warnings: string[];
  };
}

export async function buildPassageRoutes(
  floorplanId: string, graph: FloorGraph, refresh = false,
): Promise<PassageBuildResponse> {
  return unwrap<PassageBuildResponse>(await fetch(
    `${API_BASE}/api/floorplans/${floorplanId}/generate-pathways`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ graph, refresh }),
    },
  ));
}

export interface RoutePathNode {
  node_id: string;
  label: string | null;
  type: string;
  x: number;
  y: number;
  building: string;
  floor: number;
}

export interface DirectionStep {
  text: string;
  node_id: string;
}

export interface RouteResponse {
  path: RoutePathNode[];
  total_weight: number;
  directions_source: "deterministic" | "k2_horizon";
  deterministic_directions: DirectionStep[];
  phrased_directions: string | null;
}

export async function getRoute(from: string, to: string): Promise<RouteResponse> {
  const params = new URLSearchParams({ from, to });
  const res = await fetch(`${API_BASE}/api/route?${params.toString()}`);
  return unwrap<RouteResponse>(res);
}

export function rasterUrl(path: string): string {
  return `${API_BASE}${path}`;
}
