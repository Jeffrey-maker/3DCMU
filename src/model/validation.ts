import type { MapTopology, PlaceAccessPoint } from "./topology.js";

export interface ValidationIssue { readonly code: string; readonly path: string; readonly message: string }

function duplicateIds(collection: readonly { readonly id: string }[], path: string): ValidationIssue[] {
  const seen = new Set<string>();
  return collection.flatMap((item, index) => {
    if (seen.has(item.id)) return [{ code: "duplicate_id", path: `${path}[${index}].id`, message: `Duplicate ID: ${item.id}` }];
    seen.add(item.id);
    return [];
  });
}

export function validateMapTopology(map: MapTopology): readonly ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const collections = [
    [map.buildings, "buildings"], [map.floors, "floors"], [map.places, "places"],
    [map.accessPoints, "accessPoints"], [map.nodes, "nodes"], [map.segments, "segments"],
    [map.edges, "edges"], [map.terminalAccessLinks, "terminalAccessLinks"], [map.verticalConnectors, "verticalConnectors"],
  ] as const;
  for (const [collection, path] of collections) issues.push(...duplicateIds(collection, path));

  const buildings = new Set<string>(map.buildings.map(({ id }) => id));
  const floors = new Map(map.floors.map((item) => [item.id, item]));
  const places = new Map(map.places.map((item) => [item.id, item]));
  const accessPoints = new Map<string, PlaceAccessPoint>(map.accessPoints.map((item) => [item.id, item]));
  const nodes = new Map(map.nodes.map((item) => [item.id, item]));
  const segments = new Map(map.segments.map((item) => [item.id, item]));

  map.floors.forEach((floor, index) => {
    if (!buildings.has(floor.buildingId)) issues.push({ code: "missing_building", path: `floors[${index}].buildingId`, message: `Unknown building: ${floor.buildingId}` });
  });
  map.places.forEach((place, index) => {
    if (!buildings.has(place.buildingId)) issues.push({ code: "missing_building", path: `places[${index}].buildingId`, message: `Unknown building: ${place.buildingId}` });
  });
  map.nodes.forEach((node, index) => {
    if (!floors.has(node.floorId)) issues.push({ code: "missing_floor", path: `nodes[${index}].floorId`, message: `Unknown floor: ${node.floorId}` });
  });
  map.accessPoints.forEach((point, index) => {
    const place = places.get(point.placeId);
    const floor = floors.get(point.floorId);
    if (!place) issues.push({ code: "missing_place", path: `accessPoints[${index}].placeId`, message: `Unknown place: ${point.placeId}` });
    if (!floor) issues.push({ code: "missing_floor", path: `accessPoints[${index}].floorId`, message: `Unknown floor: ${point.floorId}` });
    if (place && floor && place.buildingId !== floor.buildingId) issues.push({ code: "building_mismatch", path: `accessPoints[${index}].floorId`, message: "Access point floor must belong to the place's building" });
    if (point.connection === "network_node" && !nodes.has(point.nodeId)) issues.push({ code: "missing_node", path: `accessPoints[${index}].nodeId`, message: `Unknown node: ${point.nodeId}` });
  });
  map.segments.forEach((segment, index) => {
    const from = nodes.get(segment.fromNodeId);
    const to = nodes.get(segment.toNodeId);
    if (!from || !to) issues.push({ code: "missing_node", path: `segments[${index}]`, message: "Segment references an unknown node" });
    if (segment.fromNodeId === segment.toNodeId) issues.push({ code: "self_segment", path: `segments[${index}]`, message: "A path segment must connect different nodes" });
    if (from && to) {
      const fromBuilding = floors.get(from.floorId)?.buildingId;
      const toBuilding = floors.get(to.floorId)?.buildingId;
      if (segment.kind === "building_connection" && fromBuilding === toBuilding) {
        issues.push({ code: "building_connection_same_building", path: `segments[${index}]`, message: "A building connection must join nodes in different buildings" });
      }
      if (segment.kind === "building_connection" && (from.kind !== "building_portal" || to.kind !== "building_portal")) {
        issues.push({ code: "building_connection_node_kind", path: `segments[${index}]`, message: "Building connections must use building_portal nodes at both ends" });
      }
      if (segment.kind !== "building_connection" && fromBuilding && toBuilding && fromBuilding !== toBuilding) {
        issues.push({ code: "cross_building_segment_kind", path: `segments[${index}].kind`, message: "Paths between buildings must use building_connection" });
      }
    }
  });
  map.edges.forEach((edge, index) => {
    const segment = segments.get(edge.segmentId);
    if (!segment) issues.push({ code: "missing_segment", path: `edges[${index}].segmentId`, message: `Unknown segment: ${edge.segmentId}` });
    else {
      const matches = (edge.fromNodeId === segment.fromNodeId && edge.toNodeId === segment.toNodeId)
        || (edge.fromNodeId === segment.toNodeId && edge.toNodeId === segment.fromNodeId);
      if (!matches) issues.push({ code: "edge_segment_mismatch", path: `edges[${index}]`, message: "Edge endpoints must match its segment" });
    }
    if (!Number.isFinite(edge.cost) || edge.cost <= 0) issues.push({ code: "invalid_cost", path: `edges[${index}].cost`, message: "Edge cost must be positive" });
  });
  map.terminalAccessLinks.forEach((link, index) => {
    const point = accessPoints.get(link.accessPointId);
    const node = nodes.get(link.nodeId);
    if (!point) issues.push({ code: "missing_access_point", path: `terminalAccessLinks[${index}].accessPointId`, message: "Unknown access point" });
    else if (point.connection !== "terminal") issues.push({ code: "non_terminal_access_link", path: `terminalAccessLinks[${index}].accessPointId`, message: "Access link must reference a terminal point" });
    if (!node) issues.push({ code: "missing_node", path: `terminalAccessLinks[${index}].nodeId`, message: "Unknown node" });
    else if (point && point.floorId !== node.floorId) issues.push({ code: "floor_mismatch", path: `terminalAccessLinks[${index}].nodeId`, message: "Terminal link cannot cross floors" });
    if (!Number.isFinite(link.cost) || link.cost <= 0) issues.push({ code: "invalid_cost", path: `terminalAccessLinks[${index}].cost`, message: "Terminal access cost must be positive" });
  });
  map.verticalConnectors.forEach((connector, index) => {
    const connectorBuildingIds = new Set<string>();
    if (connector.placeId && !places.has(connector.placeId)) issues.push({ code: "missing_place", path: `verticalConnectors[${index}].placeId`, message: "Connector references an unknown place" });
    connector.stopNodeIds.forEach((nodeId, stopIndex) => {
      const node = nodes.get(nodeId);
      const expectedKind = connector.kind === "stairs" ? "stair_landing" : "elevator_lobby";
      if (!node) issues.push({ code: "missing_node", path: `verticalConnectors[${index}].stopNodeIds[${stopIndex}]`, message: "Unknown connector stop node" });
      else {
        const buildingId = floors.get(node.floorId)?.buildingId;
        if (buildingId) connectorBuildingIds.add(buildingId);
        if (node.kind !== expectedKind) issues.push({ code: "connector_node_kind", path: `verticalConnectors[${index}].stopNodeIds[${stopIndex}]`, message: `${connector.kind} stops must use ${expectedKind} nodes` });
      }
    });
    if (connectorBuildingIds.size > 1) issues.push({ code: "vertical_connector_crosses_buildings", path: `verticalConnectors[${index}].stopNodeIds`, message: "A stair or elevator group cannot span different buildings; use a building connection instead" });
    connector.segmentIds.forEach((segmentId, segmentIndex) => {
      const segment = segments.get(segmentId);
      if (!segment) issues.push({ code: "missing_segment", path: `verticalConnectors[${index}].segmentIds[${segmentIndex}]`, message: "Unknown connector segment" });
      else if (segment.kind !== connector.kind) issues.push({ code: "connector_segment_kind", path: `verticalConnectors[${index}].segmentIds[${segmentIndex}]`, message: `Connector segments must have kind ${connector.kind}` });
    });
  });
  return issues;
}
