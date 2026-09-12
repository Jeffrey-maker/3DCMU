// Mirrors backend/app/models/graph.py exactly (hand-kept in sync -- no
// codegen step for this app). Field names are snake_case to match the
// Pydantic JSON wire format directly, with no transformation either side.

export type NodeType =
  | "room"
  | "corridor"
  | "door"
  | "stair"
  | "elevator"
  | "entrance"
  | "outdoor";

export type EdgeType = "hallway" | "door" | "stairs" | "elevator" | "outdoor_path";

// Named GraphNode/GraphEdge, not Node/Edge, to avoid colliding with the DOM's
// global `Node` type.
export interface GraphNode {
  id: string;
  building: string;
  floor: number;
  x: number;
  y: number;
  type: NodeType;
  label: string | null;
  room_type: string | null;
  department: string | null;
}

export interface GraphEdge {
  id: string;
  from_node: string;
  to_node: string;
  weight: number;
  type: EdgeType;
  points?: [number, number][];
}

export interface PassageLine {
  id: string;
  points: [number, number][];
}

export interface DoorPassageAttachment {
  door_node_id: string;
  pathway_point: [number, number];
  points: [number, number][];
}

export type RoutingSource =
  | "legacy"
  | "anchors_only"
  | "gemini_pathways"
  | "space_type_centerlines";

/**
 * Whether a graph stores its corridors as passage polylines (rather than as
 * persisted corridor nodes). Both the geometric centerline builder and the
 * older vision tracer produce that shape, so the UI treats them the same:
 * it draws passage lines and allows routing.
 */
export function usesPassageLines(source: RoutingSource | null | undefined): boolean {
  return source === "gemini_pathways" || source === "space_type_centerlines";
}

export interface FloorGraph {
  building: string;
  floor: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  auto_generated: boolean;
  routing_source: RoutingSource;
  page_width: number;
  page_height: number;
  routing_warnings: string[];
  unconnected_room_ids: string[];
  passageways: PassageLine[];
  door_attachments: DoorPassageAttachment[];
}
