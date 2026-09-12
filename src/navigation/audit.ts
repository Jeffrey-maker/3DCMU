import type { MapTopology, NodeKind, RoutingNode } from "../model/topology.js";
import type { ConnectorId, NodeId } from "../model/ids.js";

export interface ConnectorAuditIssue {
  readonly connectorId: ConnectorId;
  readonly connectorName: string;
  readonly stopNodeId: NodeId;
  readonly floorId: string;
  readonly code: "isolated_stop" | "missing_stop" | "wrong_stop_kind";
  readonly message: string;
  readonly replacementNodeId?: NodeId;
}

export interface ConnectorRepair {
  readonly connectorId: ConnectorId;
  readonly connectorName: string;
  readonly floorId: string;
  readonly previousNodeId: NodeId;
  readonly replacementNodeId: NodeId;
}

function expectedNodeKind(kind: "stairs" | "elevator"): NodeKind {
  return kind === "stairs" ? "stair_landing" : "elevator_lobby";
}

function routedDegree(topology: MapTopology): Map<NodeId, number> {
  const degrees = new Map<NodeId, number>(topology.nodes.map(({ id }) => [id, 0]));
  for (const edge of topology.edges) {
    if (edge.enabled === false) continue;
    degrees.set(edge.fromNodeId, (degrees.get(edge.fromNodeId) ?? 0) + 1);
    degrees.set(edge.toNodeId, (degrees.get(edge.toNodeId) ?? 0) + 1);
  }
  return degrees;
}

export function auditVerticalConnectors(topology: MapTopology): readonly ConnectorAuditIssue[] {
  const nodes = new Map(topology.nodes.map((node) => [node.id, node]));
  const degrees = routedDegree(topology);
  const issues: ConnectorAuditIssue[] = [];

  for (const connector of topology.verticalConnectors) {
    const expectedKind = expectedNodeKind(connector.kind);
    for (const stopNodeId of connector.stopNodeIds) {
      const stop = nodes.get(stopNodeId);
      if (!stop) {
        issues.push({
          connectorId: connector.id,
          connectorName: connector.name,
          stopNodeId,
          floorId: "unknown",
          code: "missing_stop",
          message: `${connector.name} references a missing stop node.`,
        });
        continue;
      }
      if (stop.kind !== expectedKind) {
        issues.push({
          connectorId: connector.id,
          connectorName: connector.name,
          stopNodeId,
          floorId: stop.floorId,
          code: "wrong_stop_kind",
          message: `${connector.name} uses ${stop.kind} on floor ${stop.floorId}; expected ${expectedKind}.`,
        });
        continue;
      }
      if ((degrees.get(stop.id) ?? 0) > 0) continue;

      const candidates = topology.nodes.filter((node) =>
        node.id !== stop.id
        && node.floorId === stop.floorId
        && node.kind === expectedKind
        && (degrees.get(node.id) ?? 0) > 0,
      );
      const replacementNodeId = candidates.length === 1 ? candidates[0]?.id : undefined;
      issues.push({
        connectorId: connector.id,
        connectorName: connector.name,
        stopNodeId,
        floorId: stop.floorId,
        code: "isolated_stop",
        message: replacementNodeId
          ? `${connector.name} stop is isolated on floor ${stop.floorId}; one connected ${expectedKind} can replace it.`
          : `${connector.name} stop is isolated on floor ${stop.floorId}; connect it to a hallway before routing.`,
        ...(replacementNodeId ? { replacementNodeId } : {}),
      });
    }
  }
  return issues;
}

export function repairUnambiguousConnectorStops(topology: MapTopology): {
  readonly topology: MapTopology;
  readonly repairs: readonly ConnectorRepair[];
} {
  const repairable = auditVerticalConnectors(topology).filter(
    (issue): issue is ConnectorAuditIssue & { replacementNodeId: NodeId } => issue.code === "isolated_stop" && issue.replacementNodeId !== undefined,
  );
  if (!repairable.length) return { topology, repairs: [] };

  const replacements = new Map<NodeId, NodeId>(repairable.map(({ stopNodeId, replacementNodeId }) => [stopNodeId, replacementNodeId]));
  const replacedIds = new Set(replacements.keys());
  const referencedBySegment = new Set(topology.segments.flatMap(({ fromNodeId, toNodeId }) => [fromNodeId, toNodeId]));
  const nodes: RoutingNode[] = topology.nodes
    .filter((node) => !replacedIds.has(node.id) || referencedBySegment.has(node.id))
    .map((node) => {
      const repair = repairable.find(({ replacementNodeId }) => replacementNodeId === node.id);
      return repair ? { ...node, label: repair.connectorName } : node;
    });

  return {
    topology: {
      ...topology,
      nodes,
      accessPoints: topology.accessPoints.map((point) => point.connection === "network_node" && replacements.has(point.nodeId)
        ? { ...point, nodeId: replacements.get(point.nodeId)! }
        : point),
      verticalConnectors: topology.verticalConnectors.map((connector) => ({
        ...connector,
        stopNodeIds: connector.stopNodeIds.map((nodeId) => replacements.get(nodeId) ?? nodeId),
      })),
    },
    repairs: repairable.map((issue) => ({
      connectorId: issue.connectorId,
      connectorName: issue.connectorName,
      floorId: issue.floorId,
      previousNodeId: issue.stopNodeId,
      replacementNodeId: issue.replacementNodeId,
    })),
  };
}
