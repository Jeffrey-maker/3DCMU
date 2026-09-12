import type {
  AccessLinkId, AccessPointId, BuildingId, ConnectorId, EdgeId, FloorId,
  MapId, NodeId, PlaceId, SegmentId,
} from "./ids.js";

export interface Point2D { readonly x: number; readonly y: number }
export interface Building { readonly id: BuildingId; readonly name: string; readonly aliases?: readonly string[] }
export interface Floor {
  readonly id: FloorId;
  readonly buildingId: BuildingId;
  readonly level: string;
  readonly name?: string;
  readonly canvas?: { readonly width: number; readonly height: number; readonly units: "pixels" | "meters" | "relative" };
}

interface PlaceBase {
  readonly id: PlaceId;
  readonly buildingId: BuildingId;
  readonly name: string;
  readonly aliases?: readonly string[];
  readonly description?: string;
}
export interface RoomPlace extends PlaceBase { readonly kind: "room"; readonly roomNumber: string }
export interface NamedPlace extends PlaceBase {
  readonly kind: "elevator" | "stairs" | "entrance" | "lobby" | "restroom" | "landmark" | "other";
}
export type Place = RoomPlace | NamedPlace;

export type NodeKind = "hallway_endpoint" | "junction" | "turn" | "stair_landing" | "elevator_lobby" | "building_portal";
export interface RoutingNode {
  readonly id: NodeId;
  readonly floorId: FloorId;
  readonly kind: NodeKind;
  readonly position: Point2D;
  readonly label?: string;
  readonly description?: string;
  readonly arrivalCue?: string;
}

export type SegmentKind = "hallway" | "doorway" | "stairs" | "elevator" | "building_connection" | "outdoor_path";
export interface PathSegment {
  readonly id: SegmentId;
  readonly kind: SegmentKind;
  readonly fromNodeId: NodeId;
  readonly toNodeId: NodeId;
  readonly geometry?: readonly Point2D[];
  readonly label?: string;
}

export type CardinalDirection = "north" | "northeast" | "east" | "southeast" | "south" | "southwest" | "west" | "northwest" | "up" | "down";
export type AccessStatus = "yes" | "no" | "unknown";
export interface TraversalProperties {
  readonly wheelchairAccessible?: AccessStatus;
  readonly publicAccess?: "public" | "restricted" | "unknown";
  readonly requiresKey?: boolean;
  readonly notes?: string;
}
export interface DirectedEdge {
  readonly id: EdgeId;
  readonly segmentId: SegmentId;
  readonly fromNodeId: NodeId;
  readonly toNodeId: NodeId;
  readonly cost: number;
  readonly direction?: CardinalDirection;
  readonly instruction: string;
  readonly traversal?: TraversalProperties;
  readonly enabled?: boolean;
}

interface AccessPointBase {
  readonly id: AccessPointId;
  readonly placeId: PlaceId;
  readonly floorId: FloorId;
  readonly label?: string;
  readonly arrivalInstruction?: string;
}
export interface NetworkAccessPoint extends AccessPointBase { readonly connection: "network_node"; readonly nodeId: NodeId }
export interface TerminalAccessPoint extends AccessPointBase { readonly connection: "terminal"; readonly position: Point2D }
export type PlaceAccessPoint = NetworkAccessPoint | TerminalAccessPoint;

/** Endpoint-only: never add these links to ordinary graph adjacency. */
export interface TerminalAccessLink {
  readonly id: AccessLinkId;
  readonly accessPointId: AccessPointId;
  readonly nodeId: NodeId;
  readonly cost: number;
  readonly toPlaceInstruction: string;
  readonly fromPlaceInstruction?: string;
  readonly directionToPlace?: CardinalDirection;
}

export interface VerticalConnector {
  readonly id: ConnectorId;
  readonly kind: "stairs" | "elevator";
  readonly name: string;
  readonly stopNodeIds: readonly NodeId[];
  readonly segmentIds: readonly SegmentId[];
  readonly placeId?: PlaceId;
}

export interface MapTopology {
  readonly schemaVersion: 1;
  readonly id: MapId;
  readonly name: string;
  readonly buildings: readonly Building[];
  readonly floors: readonly Floor[];
  readonly places: readonly Place[];
  readonly accessPoints: readonly PlaceAccessPoint[];
  readonly nodes: readonly RoutingNode[];
  readonly segments: readonly PathSegment[];
  readonly edges: readonly DirectedEdge[];
  readonly terminalAccessLinks: readonly TerminalAccessLink[];
  readonly verticalConnectors: readonly VerticalConnector[];
}
