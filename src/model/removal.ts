import type { MapAuthoringProject } from "./authoring.js";
import type { BuildingId, FloorId, PlaceId } from "./ids.js";
import type { VerticalConnector } from "./topology.js";

function withoutPlaceId(connector: VerticalConnector): VerticalConnector {
  const { placeId: _placeId, ...rest } = connector;
  return rest;
}

function removeFloors(
  project: MapAuthoringProject,
  floorIds: ReadonlySet<FloorId>,
  buildingIds: ReadonlySet<BuildingId>,
): MapAuthoringProject {
  const topology = project.topology;
  const placeBuilding = new Map(topology.places.map((place) => [place.id, place.buildingId]));
  const removedNodeIds = new Set(topology.nodes.filter((node) => floorIds.has(node.floorId)).map(({ id }) => id));
  const removedAccessPointIds = new Set(topology.accessPoints
    .filter((point) => floorIds.has(point.floorId) || (placeBuilding.get(point.placeId) !== undefined && buildingIds.has(placeBuilding.get(point.placeId)!)))
    .map(({ id }) => id));
  const candidatePlaceIds = new Set<PlaceId>(topology.accessPoints
    .filter((point) => removedAccessPointIds.has(point.id))
    .map(({ placeId }) => placeId));
  const remainingAccessPoints = topology.accessPoints.filter((point) => !removedAccessPointIds.has(point.id));
  const remainingPlaceIdsWithAccess = new Set(remainingAccessPoints.map(({ placeId }) => placeId));
  const removedPlaceIds = new Set(topology.places
    .filter((place) => buildingIds.has(place.buildingId) || (candidatePlaceIds.has(place.id) && !remainingPlaceIdsWithAccess.has(place.id)))
    .map(({ id }) => id));
  const accessPoints = remainingAccessPoints.filter((point) => !removedPlaceIds.has(point.placeId));
  const removedSegmentIds = new Set(topology.segments
    .filter((segment) => removedNodeIds.has(segment.fromNodeId) || removedNodeIds.has(segment.toNodeId))
    .map(({ id }) => id));

  const verticalConnectors = topology.verticalConnectors.flatMap((connector): VerticalConnector[] => {
    const stopNodeIds = connector.stopNodeIds.filter((nodeId) => !removedNodeIds.has(nodeId));
    if (!stopNodeIds.length) return [];
    const segmentIds = connector.segmentIds.filter((segmentId) => !removedSegmentIds.has(segmentId));
    const base = { ...connector, stopNodeIds, segmentIds };
    if (!connector.placeId || !removedPlaceIds.has(connector.placeId)) return [base];
    const replacementPlaceId = accessPoints.find((point) => point.connection === "network_node" && stopNodeIds.includes(point.nodeId))?.placeId;
    return [replacementPlaceId ? { ...base, placeId: replacementPlaceId } : withoutPlaceId(base)];
  });

  return {
    ...project,
    updatedAt: new Date().toISOString(),
    floorPlans: project.floorPlans.filter((asset) => !floorIds.has(asset.floorId)),
    topology: {
      ...topology,
      buildings: topology.buildings.filter((building) => !buildingIds.has(building.id)),
      floors: topology.floors.filter((floor) => !floorIds.has(floor.id)),
      places: topology.places.filter((place) => !removedPlaceIds.has(place.id)),
      accessPoints,
      nodes: topology.nodes.filter((node) => !removedNodeIds.has(node.id)),
      segments: topology.segments.filter((segment) => !removedSegmentIds.has(segment.id)),
      edges: topology.edges.filter((edge) => !removedSegmentIds.has(edge.segmentId) && !removedNodeIds.has(edge.fromNodeId) && !removedNodeIds.has(edge.toNodeId)),
      terminalAccessLinks: topology.terminalAccessLinks.filter((link) => !removedAccessPointIds.has(link.accessPointId) && !removedNodeIds.has(link.nodeId)),
      verticalConnectors,
    },
  };
}

export function removeFloorFromProject(project: MapAuthoringProject, floorId: FloorId): MapAuthoringProject {
  if (!project.topology.floors.some(({ id }) => id === floorId)) return project;
  return removeFloors(project, new Set([floorId]), new Set());
}

export function removeBuildingFromProject(project: MapAuthoringProject, buildingId: BuildingId): MapAuthoringProject {
  if (!project.topology.buildings.some(({ id }) => id === buildingId)) return project;
  const floorIds = new Set(project.topology.floors.filter((floor) => floor.buildingId === buildingId).map(({ id }) => id));
  return removeFloors(project, floorIds, new Set([buildingId]));
}
