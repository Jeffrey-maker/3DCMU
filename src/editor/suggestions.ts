import type { FloorPlanSuggestions, MapAuthoringProject } from "../model/authoring.js";
import {
  asAccessPointId, asEdgeId, asNodeId, asPlaceId, asSegmentId,
} from "../model/ids.js";
import type { CardinalDirection, Point2D, RoutingNode } from "../model/topology.js";

const uid = (prefix: string): string => `${prefix}-${crypto.randomUUID().slice(0, 8)}`;
const distance = (a: Point2D, b: Point2D): number => Math.max(1, Math.hypot(b.x - a.x, b.y - a.y));

function directionBetween(a: Point2D, b: Point2D): CardinalDirection {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (Math.abs(dx) > Math.abs(dy) * 1.8) return dx > 0 ? "east" : "west";
  if (Math.abs(dy) > Math.abs(dx) * 1.8) return dy > 0 ? "south" : "north";
  if (dx >= 0 && dy >= 0) return "southeast";
  if (dx >= 0) return "northeast";
  if (dy >= 0) return "southwest";
  return "northwest";
}

function withoutCategory(
  project: MapAuthoringProject,
  floorId: string,
  category: "rooms" | "hallways" | "all",
): MapAuthoringProject["floorPlans"] {
  return project.floorPlans.map((asset) => {
    if (asset.floorId !== floorId || !asset.suggestions) return asset;
    const suggestions: FloorPlanSuggestions = category === "all"
      ? { rooms: [], hallways: [], warnings: [] }
      : { ...asset.suggestions, [category]: [] };
    return { ...asset, suggestions };
  });
}

export function acceptRoomSuggestions(project: MapAuthoringProject, floorId: string): MapAuthoringProject {
  const asset = project.floorPlans.find((item) => item.floorId === floorId);
  const floor = project.topology.floors.find((item) => item.id === floorId);
  if (!asset?.suggestions || !floor) return project;
  const accessOnFloor = project.topology.accessPoints.filter((point) => point.floorId === floorId);
  const existingRoomNumbers = new Set(
    project.topology.places.flatMap((place) =>
      place.kind === "room" && accessOnFloor.some((point) => point.placeId === place.id)
        ? [place.roomNumber.toUpperCase()]
        : [],
    ),
  );
  const places = [...project.topology.places];
  const accessPoints = [...project.topology.accessPoints];
  for (const suggestion of asset.suggestions.rooms) {
    if (existingRoomNumbers.has(suggestion.roomNumber.toUpperCase())) continue;
    const placeId = asPlaceId(uid("suggested-room"));
    places.push({
      id: placeId,
      buildingId: floor.buildingId,
      kind: "room",
      roomNumber: suggestion.roomNumber,
      name: suggestion.roomNumber,
      aliases: [],
    });
    accessPoints.push({
      id: asAccessPointId(uid("suggested-access")),
      placeId,
      floorId: floor.id,
      connection: "terminal",
      position: suggestion.position,
      label: "Suggested room position",
      arrivalInstruction: `Stop at room ${suggestion.roomNumber}.`,
    });
    existingRoomNumbers.add(suggestion.roomNumber.toUpperCase());
  }
  return {
    ...project,
    updatedAt: new Date().toISOString(),
    floorPlans: withoutCategory(project, floorId, "rooms"),
    topology: { ...project.topology, places, accessPoints },
  };
}

export function acceptHallwaySuggestions(project: MapAuthoringProject, floorId: string): MapAuthoringProject {
  const asset = project.floorPlans.find((item) => item.floorId === floorId);
  if (!asset?.suggestions) return project;
  const nodes = [...project.topology.nodes];
  const segments = [...project.topology.segments];
  const edges = [...project.topology.edges];
  const newNodeIds = new Set<string>();
  const nodeByKey = new Map<string, RoutingNode>();
  const keyFor = (point: Point2D): string => `${Math.round(point.x * 2) / 2}:${Math.round(point.y * 2) / 2}`;

  for (const node of nodes) {
    if (node.floorId === floorId && ["hallway_endpoint", "junction", "turn"].includes(node.kind)) {
      nodeByKey.set(keyFor(node.position), node);
    }
  }
  const nodeAt = (position: Point2D): RoutingNode => {
    const key = keyFor(position);
    const current = nodeByKey.get(key);
    if (current) return current;
    const node: RoutingNode = {
      id: asNodeId(uid("suggested-node")),
      floorId: floorId as RoutingNode["floorId"],
      kind: "turn",
      position,
      label: "Suggested hallway",
    };
    nodes.push(node);
    nodeByKey.set(key, node);
    newNodeIds.add(node.id);
    return node;
  };

  const pairKeys = new Set(segments.map((segment) =>
    [segment.fromNodeId, segment.toNodeId].sort().join(":"),
  ));
  for (const hallway of asset.suggestions.hallways) {
    for (const [a, b] of hallway.points.slice(0, -1).map((point, index) => [point, hallway.points[index + 1]!] as const)) {
      const from = nodeAt(a);
      const to = nodeAt(b);
      const pairKey = [from.id, to.id].sort().join(":");
      if (from.id === to.id || pairKeys.has(pairKey)) continue;
      pairKeys.add(pairKey);
      const segmentId = asSegmentId(uid("suggested-hallway"));
      const cost = Math.round(distance(from.position, to.position) * 10) / 10;
      const forward = directionBetween(from.position, to.position);
      const reverse = directionBetween(to.position, from.position);
      segments.push({ id: segmentId, kind: "hallway", fromNodeId: from.id, toNodeId: to.id, label: "Suggested hallway" });
      edges.push(
        { id: asEdgeId(uid("suggested-edge")), segmentId, fromNodeId: from.id, toNodeId: to.id, cost, direction: forward, instruction: `Continue ${forward}.` },
        { id: asEdgeId(uid("suggested-edge")), segmentId, fromNodeId: to.id, toNodeId: from.id, cost, direction: reverse, instruction: `Continue ${reverse}.` },
      );
    }
  }

  const degree = new Map<string, number>();
  for (const segment of segments) {
    degree.set(segment.fromNodeId, (degree.get(segment.fromNodeId) ?? 0) + 1);
    degree.set(segment.toNodeId, (degree.get(segment.toNodeId) ?? 0) + 1);
  }
  const classifiedNodes = nodes.map((node) => {
    if (!newNodeIds.has(node.id)) return node;
    const count = degree.get(node.id) ?? 0;
    return { ...node, kind: count >= 3 ? "junction" as const : count <= 1 ? "hallway_endpoint" as const : "turn" as const };
  });
  return {
    ...project,
    updatedAt: new Date().toISOString(),
    floorPlans: withoutCategory(project, floorId, "hallways"),
    topology: { ...project.topology, nodes: classifiedNodes, segments, edges },
  };
}

export function dismissSuggestions(project: MapAuthoringProject, floorId: string): MapAuthoringProject {
  return {
    ...project,
    updatedAt: new Date().toISOString(),
    floorPlans: withoutCategory(project, floorId, "all"),
  };
}
