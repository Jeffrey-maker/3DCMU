import type { AccessLinkId, FloorId, NodeId, PlaceId, SegmentId } from "../model/ids.js";
import type { MapTopology, PlaceAccessPoint } from "../model/topology.js";
import { auditVerticalConnectors } from "./audit.js";

export interface RouteTraversal {
  readonly fromNodeId: NodeId;
  readonly toNodeId: NodeId;
  readonly kind: "path" | "stairs" | "elevator" | "building_connection";
  readonly instruction: string;
  readonly cost: number;
  readonly segmentId?: SegmentId;
  readonly floorId: FloorId;
  readonly destinationFloorId?: FloorId;
}

export interface IndoorRoute {
  readonly fromPlaceId: PlaceId;
  readonly toPlaceId: PlaceId;
  readonly startNodeId: NodeId;
  readonly endNodeId: NodeId;
  readonly originInstruction?: string;
  readonly arrivalInstruction?: string;
  readonly originAccessLinkId?: AccessLinkId;
  readonly arrivalAccessLinkId?: AccessLinkId;
  readonly traversals: readonly RouteTraversal[];
  readonly floorIds: readonly FloorId[];
  readonly totalCost: number;
}

export interface RoutePreferences {
  /** Excludes stairs and edges explicitly marked as wheelchair-inaccessible. */
  readonly stepFree?: boolean;
}

export class RouteNotFoundError extends Error {
  readonly connectorIssues = [] as ReturnType<typeof auditVerticalConnectors>;
  constructor(message: string, connectorIssues: ReturnType<typeof auditVerticalConnectors>) {
    super(message);
    this.name = "RouteNotFoundError";
    this.connectorIssues = connectorIssues;
  }
}

interface EndpointOption {
  readonly nodeId: NodeId;
  readonly cost: number;
  readonly instruction?: string;
  readonly accessLinkId?: AccessLinkId;
}

interface Hop {
  readonly toNodeId: NodeId;
  readonly traversal: RouteTraversal;
}

function endpointOptions(topology: MapTopology, placeId: PlaceId, side: "origin" | "destination"): EndpointOption[] {
  const points = topology.accessPoints.filter((point) => point.placeId === placeId);
  return points.flatMap((point: PlaceAccessPoint): EndpointOption[] => {
    if (point.connection === "network_node") return [{ nodeId: point.nodeId, cost: 0 }];
    return topology.terminalAccessLinks
      .filter((link) => link.accessPointId === point.id)
      .map((link) => ({
        nodeId: link.nodeId,
        cost: link.cost,
        ...((side === "origin" ? link.fromPlaceInstruction : link.toPlaceInstruction)
          ? { instruction: (side === "origin" ? link.fromPlaceInstruction : link.toPlaceInstruction)! }
          : {}),
        accessLinkId: link.id,
      }));
  });
}

function buildAdjacency(topology: MapTopology, preferences: RoutePreferences): Map<NodeId, Hop[]> {
  const nodeMap = new Map(topology.nodes.map((node) => [node.id, node]));
  const floorMap = new Map(topology.floors.map((floor) => [floor.id, floor]));
  const segmentMap = new Map(topology.segments.map((segment) => [segment.id, segment]));
  const adjacency = new Map<NodeId, Hop[]>(topology.nodes.map(({ id }) => [id, []]));

  for (const edge of topology.edges) {
    if (edge.enabled === false) continue;
    const from = nodeMap.get(edge.fromNodeId);
    const to = nodeMap.get(edge.toNodeId);
    const segment = segmentMap.get(edge.segmentId);
    if (!from || !to || !segment) continue;
    if (preferences.stepFree && (segment.kind === "stairs" || edge.traversal?.wheelchairAccessible === "no")) continue;
    const changesSheet = from.floorId !== to.floorId;
    adjacency.get(edge.fromNodeId)?.push({
      toNodeId: edge.toNodeId,
      traversal: {
        fromNodeId: edge.fromNodeId,
        toNodeId: edge.toNodeId,
        kind: segment.kind === "building_connection" ? "building_connection" : "path",
        instruction: edge.instruction,
        cost: edge.cost,
        segmentId: edge.segmentId,
        floorId: from.floorId,
        ...(changesSheet ? { destinationFloorId: to.floorId } : {}),
      },
    });
  }

  for (const connector of topology.verticalConnectors) {
    if (preferences.stepFree && connector.kind === "stairs") continue;
    for (const fromId of connector.stopNodeIds) {
      for (const toId of connector.stopNodeIds) {
        if (fromId === toId) continue;
        const from = nodeMap.get(fromId);
        const to = nodeMap.get(toId);
        if (!from || !to || from.floorId === to.floorId) continue;
        const fromFloor = floorMap.get(from.floorId)?.level ?? from.floorId;
        const toFloor = floorMap.get(to.floorId)?.level ?? to.floorId;
        const numericDelta = Math.abs(Number(toFloor) - Number(fromFloor));
        const floorDelta = Number.isFinite(numericDelta) && numericDelta > 0 ? numericDelta : 1;
        const cost = (connector.kind === "stairs" ? 180 : 120) * floorDelta;
        adjacency.get(fromId)?.push({
          toNodeId: toId,
          traversal: {
            fromNodeId: fromId,
            toNodeId: toId,
            kind: connector.kind,
            instruction: `Take ${connector.name} from floor ${fromFloor} to floor ${toFloor}.`,
            cost,
            floorId: from.floorId,
            destinationFloorId: to.floorId,
          },
        });
      }
    }
  }
  return adjacency;
}

export function findIndoorRoute(topology: MapTopology, fromPlaceId: PlaceId, toPlaceId: PlaceId, preferences: RoutePreferences = {}): IndoorRoute {
  if (fromPlaceId === toPlaceId) throw new RouteNotFoundError("Choose two different destinations.", []);
  const origins = endpointOptions(topology, fromPlaceId, "origin");
  const destinations = endpointOptions(topology, toPlaceId, "destination");
  if (!origins.length || !destinations.length) {
    throw new RouteNotFoundError("Both destinations need at least one connected access point.", auditVerticalConnectors(topology));
  }

  const adjacency = buildAdjacency(topology, preferences);
  const nodeMap = new Map(topology.nodes.map((node) => [node.id, node]));
  const distances = new Map<NodeId, number>();
  const pending = new Set<NodeId>();
  const previous = new Map<NodeId, { readonly nodeId: NodeId; readonly traversal: RouteTraversal }>();
  const chosenOrigin = new Map<NodeId, EndpointOption>();
  for (const origin of origins) {
    if (origin.cost < (distances.get(origin.nodeId) ?? Infinity)) {
      distances.set(origin.nodeId, origin.cost);
      chosenOrigin.set(origin.nodeId, origin);
      pending.add(origin.nodeId);
    }
  }

  while (pending.size) {
    let current: NodeId | undefined;
    for (const candidate of pending) {
      if (!current || (distances.get(candidate) ?? Infinity) < (distances.get(current) ?? Infinity)) current = candidate;
    }
    if (!current) break;
    pending.delete(current);
    for (const hop of adjacency.get(current) ?? []) {
      const candidateDistance = (distances.get(current) ?? Infinity) + hop.traversal.cost;
      if (candidateDistance < (distances.get(hop.toNodeId) ?? Infinity)) {
        distances.set(hop.toNodeId, candidateDistance);
        previous.set(hop.toNodeId, { nodeId: current, traversal: hop.traversal });
        chosenOrigin.set(hop.toNodeId, chosenOrigin.get(current)!);
        pending.add(hop.toNodeId);
      }
    }
  }

  let best: { destination: EndpointOption; totalCost: number } | undefined;
  for (const destination of destinations) {
    const networkCost = distances.get(destination.nodeId);
    if (networkCost === undefined) continue;
    const totalCost = networkCost + destination.cost;
    if (!best || totalCost < best.totalCost) best = { destination, totalCost };
  }
  if (!best) throw new RouteNotFoundError(preferences.stepFree ? "No step-free route connects these destinations." : "No route connects these destinations.", auditVerticalConnectors(topology));

  const traversals: RouteTraversal[] = [];
  let cursor = best.destination.nodeId;
  while (previous.has(cursor)) {
    const step = previous.get(cursor)!;
    traversals.unshift(step.traversal);
    cursor = step.nodeId;
  }
  const origin = chosenOrigin.get(best.destination.nodeId) ?? origins.find(({ nodeId }) => nodeId === cursor)!;
  const floorIds: FloorId[] = [];
  const appendFloor = (floorId: FloorId | undefined) => {
    if (floorId && floorIds.at(-1) !== floorId) floorIds.push(floorId);
  };
  appendFloor(nodeMap.get(origin.nodeId)?.floorId);
  traversals.forEach((step) => { appendFloor(step.floorId); appendFloor(step.destinationFloorId); });
  appendFloor(nodeMap.get(best.destination.nodeId)?.floorId);

  return {
    fromPlaceId,
    toPlaceId,
    startNodeId: origin.nodeId,
    endNodeId: best.destination.nodeId,
    ...(origin.instruction ? { originInstruction: origin.instruction } : {}),
    ...(best.destination.instruction ? { arrivalInstruction: best.destination.instruction } : {}),
    ...(origin.accessLinkId ? { originAccessLinkId: origin.accessLinkId } : {}),
    ...(best.destination.accessLinkId ? { arrivalAccessLinkId: best.destination.accessLinkId } : {}),
    traversals,
    floorIds,
    totalCost: best.totalCost,
  };
}
