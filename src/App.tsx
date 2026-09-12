import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, PointerEvent as ReactPointerEvent, WheelEvent as ReactWheelEvent } from "react";

import { renderFloorPlanFiles } from "./editor/import-floorplans.js";
import { createEmptyProject, downloadProject, readProjectFile } from "./editor/project.js";
import { loadSavedProject, saveProject } from "./editor/storage.js";
import type { FloorPlanAsset, MapAuthoringProject } from "./model/authoring.js";
import { removeBuildingFromProject, removeFloorFromProject } from "./model/removal.js";
import {
  asAccessLinkId, asAccessPointId, asBuildingId, asConnectorId, asEdgeId, asFloorId,
  asNodeId, asPlaceId, asSegmentId,
} from "./model/ids.js";
import type { BuildingId, FloorId, NodeId, PlaceId } from "./model/ids.js";
import type {
  CardinalDirection, DirectedEdge, MapTopology, NodeKind, PathSegment,
  Place, Point2D, RoutingNode, SegmentKind, TerminalAccessPoint,
} from "./model/topology.js";
import { validateMapTopology } from "./model/validation.js";
import { auditVerticalConnectors } from "./navigation/audit.js";
import { findIndoorRoute, RouteNotFoundError } from "./navigation/router.js";
import type { IndoorRoute } from "./navigation/router.js";

type Tool = "select" | "pan" | "node" | "hallway" | "room" | "stairs" | "elevator" | "building" | "access";
type AppMode = "annotate" | "navigate";
type Selection = { kind: "node" | "segment" | "place"; id: string } | null;
type DeleteRequest = { kind: "floor"; id: FloorId } | { kind: "building"; id: BuildingId };
interface ViewBox { x: number; y: number; width: number; height: number }

const TOOL_INFO: Record<Tool, { icon: string; label: string; hint: string }> = {
  select: { icon: "↖", label: "Select", hint: "Inspect an annotation" },
  pan: { icon: "✥", label: "Pan", hint: "Drag the drawing" },
  node: { icon: "●", label: "Node", hint: "Place a routing point" },
  hallway: { icon: "╱", label: "Hallway", hint: "Click endpoints to chain paths" },
  room: { icon: "▰", label: "Room", hint: "Place a terminal destination" },
  stairs: { icon: "≋", label: "Stairs", hint: "Place a stair landing" },
  elevator: { icon: "⇅", label: "Elevator", hint: "Place an elevator lobby" },
  building: { icon: "⇄", label: "Connector", hint: "Link portal points across buildings" },
  access: { icon: "⌁", label: "Room link", hint: "Connect selected room to nodes" },
};

const DIRECTIONS: CardinalDirection[] = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest", "up", "down"];
const NODE_KINDS: NodeKind[] = ["hallway_endpoint", "junction", "turn", "stair_landing", "elevator_lobby", "building_portal"];
const SEGMENT_KINDS: SegmentKind[] = ["hallway", "doorway", "stairs", "elevator", "building_connection", "outdoor_path"];

const uid = (prefix: string): string => `${prefix}-${crypto.randomUUID().slice(0, 8)}`;
const round = (value: number): number => Math.round(value * 10) / 10;
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

function updateTopology(project: MapAuthoringProject, update: (topology: MapTopology) => MapTopology): MapAuthoringProject {
  return { ...project, topology: update(project.topology), updatedAt: new Date().toISOString() };
}

function IconMark({ kind }: { kind: Place["kind"] }) {
  if (kind === "stairs") return <text className="place-symbol" textAnchor="middle" y="5">≋</text>;
  if (kind === "elevator") return <text className="place-symbol" textAnchor="middle" y="5">⇅</text>;
  return <rect x="-7" y="-7" width="14" height="14" rx="2" />;
}

export default function App() {
  const [project, setProject] = useState<MapAuthoringProject>(createEmptyProject);
  const [ready, setReady] = useState(false);
  const [saveState, setSaveState] = useState<"saved" | "saving" | "error">("saved");
  const [selectedBuildingId, setSelectedBuildingId] = useState<BuildingId | null>(null);
  const [selectedFloorId, setSelectedFloorId] = useState<FloorId | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [mode, setMode] = useState<AppMode>("annotate");
  const [route, setRoute] = useState<IndoorRoute | null>(null);
  const [routeFrom, setRouteFrom] = useState<PlaceId | null>(null);
  const [routeTo, setRouteTo] = useState<PlaceId | null>(null);
  const [routeError, setRouteError] = useState<string | null>(null);
  const [stepFree, setStepFree] = useState(false);
  const [tool, setTool] = useState<Tool>("select");
  const [pendingHallNodeId, setPendingHallNodeId] = useState<NodeId | null>(null);
  const [pendingBuildingNodeId, setPendingBuildingNodeId] = useState<NodeId | null>(null);
  const [cursorPoint, setCursorPoint] = useState<Point2D | null>(null);
  const [viewBox, setViewBox] = useState<ViewBox>({ x: 0, y: 0, width: 1000, height: 700 });
  const [panStart, setPanStart] = useState<{ clientX: number; clientY: number; viewBox: ViewBox } | null>(null);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [deleteRequest, setDeleteRequest] = useState<DeleteRequest | null>(null);
  const floorInputRef = useRef<HTMLInputElement>(null);
  const projectInputRef = useRef<HTMLInputElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    loadSavedProject()
      .then((saved) => {
        if (saved) {
          setProject(saved);
          setSelectedBuildingId(saved.topology.buildings[0]?.id ?? null);
          setSelectedFloorId(saved.topology.floors[0]?.id ?? null);
        } else {
          setSelectedBuildingId(project.topology.buildings[0]?.id ?? null);
        }
      })
      .catch(() => setNotice("Local autosave could not be loaded."))
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!ready) return;
    setSaveState("saving");
    const timer = window.setTimeout(() => {
      saveProject(project).then(() => setSaveState("saved")).catch(() => setSaveState("error"));
    }, 450);
    return () => window.clearTimeout(timer);
  }, [project, ready]);

  const activeFloor = project.topology.floors.find(({ id }) => id === selectedFloorId);
  const activeBuildingId = activeFloor?.buildingId ?? selectedBuildingId ?? project.topology.buildings[0]?.id ?? null;
  const activeBuilding = project.topology.buildings.find(({ id }) => id === activeBuildingId);
  const activeAsset = project.floorPlans.find(({ floorId }) => floorId === selectedFloorId);
  const buildingFloors = useMemo(() => project.topology.floors.filter(({ buildingId }) => buildingId === activeBuildingId), [project, activeBuildingId]);
  const floorNodes = useMemo(() => project.topology.nodes.filter(({ floorId }) => floorId === selectedFloorId), [project, selectedFloorId]);
  const nodeMap = useMemo(() => new Map(project.topology.nodes.map((node) => [node.id, node])), [project]);
  const floorSegments = useMemo(() => project.topology.segments.filter((segment) => {
    const from = nodeMap.get(segment.fromNodeId);
    const to = nodeMap.get(segment.toNodeId);
    return from?.floorId === selectedFloorId && to?.floorId === selectedFloorId;
  }), [project, nodeMap, selectedFloorId]);
  const floorBuildingConnections = useMemo(() => project.topology.segments.filter((segment) => {
    if (segment.kind !== "building_connection") return false;
    const from = nodeMap.get(segment.fromNodeId);
    const to = nodeMap.get(segment.toNodeId);
    return from?.floorId === selectedFloorId || to?.floorId === selectedFloorId;
  }), [project, nodeMap, selectedFloorId]);
  const floorAccessPoints = useMemo(() => project.topology.accessPoints.filter(({ floorId }) => floorId === selectedFloorId), [project, selectedFloorId]);
  const issues = useMemo(() => [
    ...validateMapTopology(project.topology),
    ...auditVerticalConnectors(project.topology).map((issue) => ({
      code: issue.code,
      path: `verticalConnectors.${issue.connectorName}.${issue.floorId}`,
      message: issue.message,
    })),
  ], [project]);
  const routeSegmentIds = useMemo(() => new Set(route?.traversals.flatMap((step) => step.segmentId ? [step.segmentId] : []) ?? []), [route]);
  const routeNodeIds = useMemo(() => new Set(route?.traversals.flatMap((step) => [step.fromNodeId, step.toNodeId]) ?? []), [route]);
  const routeAccessLinkIds = useMemo(() => new Set([route?.originAccessLinkId, route?.arrivalAccessLinkId].filter(Boolean)), [route]);
  const routePlaceIds = useMemo(() => new Set(route ? [route.fromPlaceId, route.toPlaceId] : []), [route]);

  useEffect(() => {
    if (!activeAsset) return;
    setViewBox({ x: 0, y: 0, width: activeAsset.width, height: activeAsset.height });
    setPendingHallNodeId(null);
    setSelection(null);
  }, [activeAsset?.id]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (deleteRequest) setDeleteRequest(null);
        setPendingHallNodeId(null);
        setPendingBuildingNodeId(null);
        setTool("select");
      }
      if ((event.key === "Delete" || event.key === "Backspace") && selection && !(event.target instanceof HTMLInputElement) && !(event.target instanceof HTMLTextAreaElement)) {
        event.preventDefault();
        deleteSelection(selection);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const importFloorPlans = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = [...(event.target.files ?? [])];
    event.target.value = "";
    if (!files.length) return;
    setImporting(true);
    setNotice(`Preparing ${files.length} source file${files.length === 1 ? "" : "s"}…`);
    try {
      const rendered = await renderFloorPlanFiles(files);
      const building = project.topology.buildings.find(({ id }) => id === activeBuildingId);
      if (!building) throw new Error("The project needs a building before floors can be imported.");
      const newFloors = rendered.map((plan) => {
        const floorId = asFloorId(uid(`floor-${plan.suggestedLevel.toLowerCase()}`));
        return {
          floor: {
            id: floorId,
            buildingId: building.id,
            level: plan.suggestedLevel,
            name: `Floor ${plan.suggestedLevel}`,
            canvas: { width: plan.width, height: plan.height, units: "pixels" as const },
          },
          asset: {
            id: uid("plan"), floorId, sourceFileName: plan.sourceFileName,
            sourceMediaType: plan.sourceMediaType,
            ...(plan.sourcePage === undefined ? {} : { sourcePage: plan.sourcePage }),
            renderedImageDataUrl: plan.imageDataUrl, width: plan.width, height: plan.height,
          } satisfies FloorPlanAsset,
        };
      });
      setProject((current) => ({
        ...current,
        updatedAt: new Date().toISOString(),
        topology: { ...current.topology, floors: [...current.topology.floors, ...newFloors.map(({ floor }) => floor)] },
        floorPlans: [...current.floorPlans, ...newFloors.map(({ asset }) => asset)],
      }));
      setSelectedBuildingId(building.id);
      setSelectedFloorId(newFloors[0]?.floor.id ?? null);
      setNotice(`${newFloors.length} floor${newFloors.length === 1 ? "" : "s"} ready to annotate.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Floorplan import failed.");
    } finally {
      setImporting(false);
    }
  };

  const importProject = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const imported = await readProjectFile(file);
      setProject(imported);
      setSelectedBuildingId(imported.topology.floors[0]?.buildingId ?? imported.topology.buildings[0]?.id ?? null);
      setSelectedFloorId(imported.topology.floors[0]?.id ?? null);
      setRoute(null);
      setRouteFrom(null);
      setRouteTo(null);
      setRouteError(null);
      setNotice("Project imported.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Project import failed.");
    }
  };

  const svgPoint = (event: ReactPointerEvent<SVGSVGElement>): Point2D => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const transformed = point.matrixTransform(svg.getScreenCTM()?.inverse());
    return { x: round(transformed.x), y: round(transformed.y) };
  };

  const addNode = useCallback((position: Point2D, kind: NodeKind = "hallway_endpoint"): NodeId | null => {
    if (!selectedFloorId) return null;
    const id = asNodeId(uid("node"));
    const node: RoutingNode = { id, floorId: selectedFloorId, kind, position, label: kind.replaceAll("_", " ") };
    setProject((current) => updateTopology(current, (topology) => ({ ...topology, nodes: [...topology.nodes, node] })));
    return id;
  }, [selectedFloorId]);

  const addHallwayAt = (position: Point2D, explicitNodeId?: NodeId) => {
    const unitPerPixel = viewBox.width / Math.max(svgRef.current?.clientWidth ?? 1, 1);
    const nearest = explicitNodeId ?? floorNodes
      .map((node) => ({ node, d: distance(node.position, position) }))
      .filter(({ d }) => d < 18 * unitPerPixel)
      .sort((a, b) => a.d - b.d)[0]?.node.id;
    const nodeId = nearest ?? addNode(position);
    if (!nodeId) return;
    if (!pendingHallNodeId) {
      setPendingHallNodeId(nodeId);
      return;
    }
    if (pendingHallNodeId === nodeId) return;
    const from = nodeMap.get(pendingHallNodeId) ?? floorNodes.find(({ id }) => id === pendingHallNodeId);
    const to = nodeMap.get(nodeId) ?? (nodeId === nearest ? floorNodes.find(({ id }) => id === nodeId) : { position });
    if (!from || !to) return;
    const segmentId = asSegmentId(uid("segment"));
    const segment: PathSegment = { id: segmentId, kind: "hallway", fromNodeId: pendingHallNodeId, toNodeId: nodeId, label: "Hallway" };
    const forward = directionBetween(from.position, to.position);
    const reverse = directionBetween(to.position, from.position);
    const cost = round(distance(from.position, to.position));
    const edges: DirectedEdge[] = [
      { id: asEdgeId(uid("edge")), segmentId, fromNodeId: pendingHallNodeId, toNodeId: nodeId, cost, direction: forward, instruction: `Continue ${forward}.` },
      { id: asEdgeId(uid("edge")), segmentId, fromNodeId: nodeId, toNodeId: pendingHallNodeId, cost, direction: reverse, instruction: `Continue ${reverse}.` },
    ];
    setProject((current) => updateTopology(current, (topology) => ({
      ...topology, segments: [...topology.segments, segment], edges: [...topology.edges, ...edges],
    })));
    setSelection({ kind: "segment", id: segmentId });
    setPendingHallNodeId(nodeId);
  };

  const addRoom = (position: Point2D) => {
    if (!selectedFloorId) return;
    const building = activeFloor?.buildingId;
    if (!building) return;
    const placeId = asPlaceId(uid("room"));
    const pointId = asAccessPointId(uid("access"));
    const count = project.topology.places.filter(({ kind }) => kind === "room").length + 1;
    const place: Place = { id: placeId, buildingId: building, kind: "room", roomNumber: `Room ${count}`, name: `Room ${count}`, aliases: [] };
    const accessPoint: TerminalAccessPoint = {
      id: pointId, placeId, floorId: selectedFloorId, connection: "terminal", position,
      label: "Door", arrivalInstruction: `Stop at ${place.name}.`,
    };
    setProject((current) => updateTopology(current, (topology) => ({
      ...topology, places: [...topology.places, place], accessPoints: [...topology.accessPoints, accessPoint],
    })));
    setSelection({ kind: "place", id: placeId });
    setTool("access");
  };

  const addVerticalStop = (position: Point2D, kind: "stairs" | "elevator") => {
    if (!selectedFloorId || !activeFloor) return;
    const placeId = asPlaceId(uid(kind));
    const nodeId = asNodeId(uid(`${kind}-stop`));
    const pointId = asAccessPointId(uid("access"));
    const connectorId = asConnectorId(uid("connector"));
    const number = project.topology.verticalConnectors.filter((item) => item.kind === kind).length + 1;
    const name = `${kind === "stairs" ? "Stair" : "Elevator"} ${number}`;
    setProject((current) => updateTopology(current, (topology) => ({
      ...topology,
      places: [...topology.places, { id: placeId, buildingId: activeFloor.buildingId, kind, name }],
      nodes: [...topology.nodes, { id: nodeId, floorId: selectedFloorId, kind: kind === "stairs" ? "stair_landing" : "elevator_lobby", position, label: name }],
      accessPoints: [...topology.accessPoints, { id: pointId, placeId, floorId: selectedFloorId, connection: "network_node", nodeId, label: name }],
      verticalConnectors: [...topology.verticalConnectors, { id: connectorId, kind, name, placeId, stopNodeIds: [nodeId], segmentIds: [] }],
    })));
    setSelection({ kind: "place", id: placeId });
    setTool("select");
  };

  const addBuildingConnectionAt = (position: Point2D, explicitNodeId?: NodeId) => {
    if (!selectedFloorId || !activeFloor) return;
    const unitPerPixel = viewBox.width / Math.max(svgRef.current?.clientWidth ?? 1, 1);
    const nearest = explicitNodeId ?? floorNodes
      .map((node) => ({ node, d: distance(node.position, position) }))
      .filter(({ d }) => d < 18 * unitPerPixel)
      .sort((a, b) => a.d - b.d)[0]?.node.id;
    const nodeId = nearest ?? addNode(position, "building_portal");
    if (!nodeId) return;
    const targetNode = nodeMap.get(nodeId) ?? { id: nodeId, floorId: selectedFloorId, kind: "building_portal" as const, position };

    if (!pendingBuildingNodeId) {
      if (targetNode.kind !== "building_portal") patchNode(targetNode.id, { kind: "building_portal" });
      setPendingBuildingNodeId(nodeId);
      setNotice("First portal set. Choose the other building and floor, then click its portal point.");
      return;
    }
    if (pendingBuildingNodeId === nodeId) return;
    const sourceNode = nodeMap.get(pendingBuildingNodeId);
    const sourceFloor = sourceNode && project.topology.floors.find(({ id }) => id === sourceNode.floorId);
    const targetFloor = project.topology.floors.find(({ id }) => id === targetNode.floorId);
    if (!sourceNode || !sourceFloor || !targetFloor) {
      setPendingBuildingNodeId(null);
      setNotice("The first portal is no longer available. Start the connector again.");
      return;
    }
    if (sourceFloor.buildingId === targetFloor.buildingId) {
      setNotice("Building connectors must end in a different building. Switch buildings, then choose the other portal.");
      return;
    }
    const sourceBuilding = project.topology.buildings.find(({ id }) => id === sourceFloor.buildingId);
    const targetBuilding = project.topology.buildings.find(({ id }) => id === targetFloor.buildingId);
    if (!sourceBuilding || !targetBuilding) return;
    const segmentId = asSegmentId(uid("building-link"));
    const segment: PathSegment = {
      id: segmentId,
      kind: "building_connection",
      fromNodeId: sourceNode.id,
      toNodeId: targetNode.id,
      label: `${sourceBuilding.name} ↔ ${targetBuilding.name}`,
    };
    const edges: DirectedEdge[] = [
      { id: asEdgeId(uid("edge")), segmentId, fromNodeId: sourceNode.id, toNodeId: targetNode.id, cost: 60, instruction: `Follow the connector hallway into ${targetBuilding.name}.` },
      { id: asEdgeId(uid("edge")), segmentId, fromNodeId: targetNode.id, toNodeId: sourceNode.id, cost: 60, instruction: `Follow the connector hallway into ${sourceBuilding.name}.` },
    ];
    setProject((current) => updateTopology(current, (topology) => ({
      ...topology,
      nodes: topology.nodes.map((node) => node.id === sourceNode.id || node.id === targetNode.id ? { ...node, kind: "building_portal" } : node),
      segments: [...topology.segments, segment],
      edges: [...topology.edges, ...edges],
    })));
    setPendingBuildingNodeId(null);
    setSelection({ kind: "segment", id: segmentId });
    setTool("select");
    setNotice(`${sourceBuilding.name} and ${targetBuilding.name} are now connected in both directions.`);
  };

  const toggleRoomLink = (node: RoutingNode) => {
    if (selection?.kind !== "place") return;
    const point = project.topology.accessPoints.find((item) => item.placeId === selection.id && item.floorId === selectedFloorId && item.connection === "terminal");
    if (!point || point.connection !== "terminal") return;
    const existing = project.topology.terminalAccessLinks.find((link) => link.accessPointId === point.id && link.nodeId === node.id);
    setProject((current) => updateTopology(current, (topology) => ({
      ...topology,
      terminalAccessLinks: existing
        ? topology.terminalAccessLinks.filter(({ id }) => id !== existing.id)
        : [...topology.terminalAccessLinks, {
          id: asAccessLinkId(uid("terminal-link")), accessPointId: point.id, nodeId: node.id,
          cost: round(distance(point.position, node.position)), directionToPlace: directionBetween(node.position, point.position),
          toPlaceInstruction: `Continue ${directionBetween(node.position, point.position)} to the destination.`,
          fromPlaceInstruction: `Head ${directionBetween(point.position, node.position)} into the hallway.`,
        }],
    })));
  };

  const deleteSelection = (target: Selection) => {
    if (!target) return;
    setProject((current) => updateTopology(current, (topology) => {
      if (target.kind === "segment") return {
        ...topology,
        segments: topology.segments.filter(({ id }) => id !== target.id),
        edges: topology.edges.filter(({ segmentId }) => segmentId !== target.id),
      };
      if (target.kind === "place") {
        const pointIds = new Set(topology.accessPoints.filter(({ placeId }) => placeId === target.id).map(({ id }) => id));
        const nodeIds = new Set(topology.accessPoints.flatMap((point) => point.placeId === target.id && point.connection === "network_node" ? [point.nodeId] : []));
        const segmentIds = new Set(topology.segments.filter((segment) => nodeIds.has(segment.fromNodeId) || nodeIds.has(segment.toNodeId)).map(({ id }) => id));
        return {
          ...topology,
          places: topology.places.filter(({ id }) => id !== target.id),
          accessPoints: topology.accessPoints.filter(({ placeId }) => placeId !== target.id),
          terminalAccessLinks: topology.terminalAccessLinks.filter(({ accessPointId }) => !pointIds.has(accessPointId)),
          nodes: topology.nodes.filter(({ id }) => !nodeIds.has(id)),
          segments: topology.segments.filter(({ id }) => !segmentIds.has(id)),
          edges: topology.edges.filter(({ segmentId }) => !segmentIds.has(segmentId)),
          verticalConnectors: topology.verticalConnectors.filter(({ placeId }) => placeId !== target.id),
        };
      }
      const segmentIds = new Set(topology.segments.filter((segment) => segment.fromNodeId === target.id || segment.toNodeId === target.id).map(({ id }) => id));
      return {
        ...topology,
        nodes: topology.nodes.filter(({ id }) => id !== target.id),
        segments: topology.segments.filter(({ id }) => !segmentIds.has(id)),
        edges: topology.edges.filter(({ segmentId }) => !segmentIds.has(segmentId)),
        terminalAccessLinks: topology.terminalAccessLinks.filter(({ nodeId }) => nodeId !== target.id),
      };
    }));
    setSelection(null);
  };

  const onCanvasPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (tool === "pan") {
      event.currentTarget.setPointerCapture(event.pointerId);
      setPanStart({ clientX: event.clientX, clientY: event.clientY, viewBox });
      return;
    }
    const point = svgPoint(event);
    if (tool === "node") {
      const id = addNode(point);
      if (id) setSelection({ kind: "node", id });
    } else if (tool === "hallway") addHallwayAt(point);
    else if (tool === "building") addBuildingConnectionAt(point);
    else if (tool === "room") addRoom(point);
    else if (tool === "stairs" || tool === "elevator") addVerticalStop(point, tool);
    else if (tool === "select") setSelection(null);
  };

  const onCanvasPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const point = svgPoint(event);
    setCursorPoint(point);
    if (panStart) {
      const svg = svgRef.current;
      if (!svg) return;
      const scaleX = panStart.viewBox.width / svg.clientWidth;
      const scaleY = panStart.viewBox.height / svg.clientHeight;
      setViewBox({ ...panStart.viewBox, x: panStart.viewBox.x - (event.clientX - panStart.clientX) * scaleX, y: panStart.viewBox.y - (event.clientY - panStart.clientY) * scaleY });
    }
  };

  const onWheel = (event: ReactWheelEvent<SVGSVGElement>) => {
    const factor = event.deltaY > 0 ? 1.12 : 0.89;
    const point = svgPoint(event as unknown as ReactPointerEvent<SVGSVGElement>);
    const width = Math.min(Math.max(viewBox.width * factor, (activeAsset?.width ?? 1000) * 0.08), (activeAsset?.width ?? 1000) * 3);
    const height = width * viewBox.height / viewBox.width;
    const rx = (point.x - viewBox.x) / viewBox.width;
    const ry = (point.y - viewBox.y) / viewBox.height;
    setViewBox({ x: point.x - rx * width, y: point.y - ry * height, width, height });
  };

  const selectTool = (next: Tool) => {
    setTool(next);
    if (next !== "hallway") setPendingHallNodeId(null);
    if (next !== "building") setPendingBuildingNodeId(null);
  };

  const switchMode = (next: AppMode) => {
    setMode(next);
    setSelection(null);
    setPendingHallNodeId(null);
    setPendingBuildingNodeId(null);
    setTool(next === "navigate" ? "pan" : "select");
  };

  const planRoute = () => {
    if (!routeFrom || !routeTo) {
      setRoute(null);
      setRouteError("Choose a starting point and destination.");
      return;
    }
    try {
      const result = findIndoorRoute(project.topology, routeFrom, routeTo, { stepFree });
      setRoute(result);
      setRouteError(null);
      setSelectedFloorId(result.floorIds[0] ?? null);
    } catch (error) {
      setRoute(null);
      setRouteError(error instanceof RouteNotFoundError ? error.message : "The route could not be calculated.");
    }
  };

  const beginHallwayFromNode = (nodeId: NodeId) => {
    setPendingHallNodeId(nodeId);
    setTool("hallway");
    setSelection(null);
  };

  const patchNode = (id: string, patch: Partial<RoutingNode>) => setProject((current) => updateTopology(current, (topology) => ({
    ...topology, nodes: topology.nodes.map((node) => node.id === id ? { ...node, ...patch } : node),
  })));
  const patchPlace = (id: string, patch: Partial<Place>) => setProject((current) => updateTopology(current, (topology) => ({
    ...topology, places: topology.places.map((place) => place.id === id ? { ...place, ...patch } as Place : place),
  })));
  const patchSegment = (id: string, patch: Partial<PathSegment>) => setProject((current) => updateTopology(current, (topology) => ({
    ...topology, segments: topology.segments.map((segment) => segment.id === id ? { ...segment, ...patch } : segment),
  })));
  const patchEdge = (id: string, patch: Partial<DirectedEdge>) => setProject((current) => updateTopology(current, (topology) => ({
    ...topology, edges: topology.edges.map((edge) => edge.id === id ? { ...edge, ...patch } : edge),
  })));

  const fitPlan = () => {
    if (activeAsset) setViewBox({ x: 0, y: 0, width: activeAsset.width, height: activeAsset.height });
  };

  const selectBuilding = (buildingId: BuildingId) => {
    setSelectedBuildingId(buildingId);
    setSelectedFloorId(project.topology.floors.find((floor) => floor.buildingId === buildingId)?.id ?? null);
    setSelection(null);
    setPendingHallNodeId(null);
  };

  const selectFloor = (floorId: FloorId | null) => {
    if (!floorId) {
      setSelectedFloorId(null);
      return;
    }
    const floor = project.topology.floors.find(({ id }) => id === floorId);
    setSelectedFloorId(floorId);
    if (floor) setSelectedBuildingId(floor.buildingId);
  };

  const addBuilding = () => {
    const id = asBuildingId(uid("building"));
    const name = `Building ${project.topology.buildings.length + 1}`;
    setProject((current) => updateTopology(current, (topology) => ({ ...topology, buildings: [...topology.buildings, { id, name }] })));
    setSelectedBuildingId(id);
    setSelectedFloorId(null);
    setSelection(null);
    setNotice(`${name} added. Rename it in the inspector, then import its floorplans.`);
  };

  const confirmDeletion = () => {
    if (!deleteRequest) return;
    if (deleteRequest.kind === "floor") {
      const floor = project.topology.floors.find(({ id }) => id === deleteRequest.id);
      if (!floor) {
        setDeleteRequest(null);
        return;
      }
      const nextFloor = project.topology.floors.find((item) => item.buildingId === floor.buildingId && item.id !== floor.id);
      setProject((current) => removeFloorFromProject(current, floor.id));
      setSelectedBuildingId(floor.buildingId);
      setSelectedFloorId(nextFloor?.id ?? null);
      setNotice(`${floor.name ?? `Floor ${floor.level}`} and its annotations were removed.`);
    } else {
      if (project.topology.buildings.length <= 1) {
        setNotice("A project must keep at least one building.");
        setDeleteRequest(null);
        return;
      }
      const building = project.topology.buildings.find(({ id }) => id === deleteRequest.id);
      if (!building) {
        setDeleteRequest(null);
        return;
      }
      const nextBuilding = project.topology.buildings.find(({ id }) => id !== building.id);
      setProject((current) => removeBuildingFromProject(current, building.id));
      setSelectedBuildingId(nextBuilding?.id ?? null);
      setSelectedFloorId(project.topology.floors.find((floor) => floor.buildingId === nextBuilding?.id)?.id ?? null);
      setNotice(`${building.name} and all of its floorplans were removed.`);
    }
    setSelection(null);
    setPendingHallNodeId(null);
    setPendingBuildingNodeId(null);
    setRoute(null);
    setRouteFrom(null);
    setRouteTo(null);
    setRouteError(null);
    setTool("select");
    setDeleteRequest(null);
  };

  if (!ready) return <div className="loading-screen"><span className="contour-mark">C</span><p>Opening the field desk…</p></div>;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="contour-mark">C</span>
          <div><strong>Contour</strong><small>indoor topology desk</small></div>
        </div>
        <input
          className="project-name"
          aria-label="Project name"
          value={project.topology.name}
          onChange={(event) => setProject((current) => updateTopology(current, (topology) => ({ ...topology, name: event.target.value })))}
        />
        <div className="top-actions">
          <div className="mode-switch" aria-label="Application mode">
            <button className={mode === "annotate" ? "active" : ""} onClick={() => switchMode("annotate")}>Annotate</button>
            <button className={mode === "navigate" ? "active" : ""} onClick={() => switchMode("navigate")}>Navigate</button>
          </div>
          <span className={`save-state ${saveState}`}>{saveState === "saving" ? "Saving…" : saveState === "error" ? "Save failed" : "Saved locally"}</span>
          <button className="quiet-button" onClick={() => projectInputRef.current?.click()}>Open project</button>
          <button className="primary-button" onClick={() => downloadProject(project)} disabled={!project.topology.floors.length}>Export</button>
        </div>
      </header>

      <section className="workspace">
        <aside className="floor-rail">
          <div className="rail-heading"><span>Building index</span><b>{project.topology.buildings.length.toString().padStart(2, "0")}</b></div>
          <div className="building-picker">
            <label>
              <span>Active building</span>
              <select data-testid="building-picker" value={activeBuildingId ?? ""} onChange={(event) => selectBuilding(asBuildingId(event.target.value))}>
                {project.topology.buildings.map((building) => <option key={building.id} value={building.id}>{building.name}</option>)}
              </select>
            </label>
            <button onClick={addBuilding} aria-label="Add another building" title="Add another building">＋</button>
          </div>
          <button className="import-card" onClick={() => floorInputRef.current?.click()} disabled={importing}>
            <span className="import-plus">＋</span>
            <strong>{importing ? "Rendering plans…" : "Import floorplans"}</strong>
            <small>Into {activeBuilding?.name ?? "selected building"}</small>
          </button>
          <nav className="floor-list" aria-label="Imported floors">
            <div className="floor-list-heading"><span>Floorplans</span><b>{buildingFloors.length.toString().padStart(2, "0")}</b></div>
            {buildingFloors.map((floor, index) => {
              const nodes = project.topology.nodes.filter(({ floorId }) => floorId === floor.id).length;
              const destinations = project.topology.accessPoints.filter(({ floorId }) => floorId === floor.id).length;
              return (
                <button key={floor.id} className={`floor-row ${floor.id === selectedFloorId ? "active" : ""}`} onClick={() => selectFloor(floor.id)}>
                  <span className="floor-number">{String(index + 1).padStart(2, "0")}</span>
                  <span><strong>{floor.name ?? `Floor ${floor.level}`}</strong><small>{nodes} nodes · {destinations} places</small></span>
                  <i aria-hidden="true" />
                </button>
              );
            })}
            {!buildingFloors.length && <p className="no-floorplans">No floorplans yet.<br />Import the first sheet for this building.</p>}
          </nav>
          <div className="rail-footer">
            <span className={issues.length ? "issue-dot warn" : "issue-dot"} />
            {issues.length ? `${issues.length} data issue${issues.length === 1 ? "" : "s"}` : "Topology checks clean"}
          </div>
        </aside>

        <section className="editor-stage">
          {activeAsset && activeFloor ? (
            <>
              <div className="stage-heading">
                <div><span className="eyebrow">{activeBuilding?.name ?? "Active building"} / active sheet</span><h1>{activeFloor.name ?? `Floor ${activeFloor.level}`}</h1></div>
                <div className="sheet-meta"><span>{activeAsset.sourceFileName}</span>{activeAsset.sourcePage && <b>PAGE {activeAsset.sourcePage}</b>}</div>
              </div>
              <div className="canvas-frame">
                <svg
                  ref={svgRef}
                  className={`map-canvas tool-${tool} ${panStart ? "is-panning" : ""}`}
                  viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
                  onPointerDown={onCanvasPointerDown}
                  onPointerMove={onCanvasPointerMove}
                  onPointerUp={() => setPanStart(null)}
                  onPointerCancel={() => setPanStart(null)}
                  onPointerLeave={() => { setCursorPoint(null); setPanStart(null); }}
                  onWheel={onWheel}
                  data-testid="map-canvas"
                >
                  <image href={activeAsset.renderedImageDataUrl} width={activeAsset.width} height={activeAsset.height} className="plan-image" />
                  <g className="annotation-layer">
                    {floorSegments.map((segment) => {
                      const from = nodeMap.get(segment.fromNodeId);
                      const to = nodeMap.get(segment.toNodeId);
                      if (!from || !to) return null;
                      const isRoute = routeSegmentIds.has(segment.id);
                      return <line key={segment.id} data-entity="segment" className={`segment ${selection?.kind === "segment" && selection.id === segment.id ? "selected" : ""} ${mode === "navigate" && isRoute ? "route-active" : ""} ${mode === "navigate" && route && !isRoute ? "route-muted" : ""}`} x1={from.position.x} y1={from.position.y} x2={to.position.x} y2={to.position.y} onPointerDown={(event) => { event.stopPropagation(); if (mode === "annotate") { setSelection({ kind: "segment", id: segment.id }); if (tool !== "select") setTool("select"); } }} />;
                    })}
                    {project.topology.terminalAccessLinks.map((link) => {
                      const point = floorAccessPoints.find((item) => item.id === link.accessPointId);
                      const node = nodeMap.get(link.nodeId);
                      if (!point || point.connection !== "terminal" || !node) return null;
                      const isRoute = routeAccessLinkIds.has(link.id);
                      return <line key={link.id} className={`terminal-link ${mode === "navigate" && isRoute ? "route-active" : ""} ${mode === "navigate" && route && !isRoute ? "route-muted" : ""}`} x1={point.position.x} y1={point.position.y} x2={node.position.x} y2={node.position.y} />;
                    })}
                    {pendingHallNodeId && cursorPoint && nodeMap.get(pendingHallNodeId) && <line className="draft-segment" x1={nodeMap.get(pendingHallNodeId)!.position.x} y1={nodeMap.get(pendingHallNodeId)!.position.y} x2={cursorPoint.x} y2={cursorPoint.y} />}
                    {floorNodes.map((node) => {
                      const selected = selection?.kind === "node" && selection.id === node.id;
                      const linked = selection?.kind === "place" && project.topology.terminalAccessLinks.some((link) => link.nodeId === node.id && project.topology.accessPoints.some((point) => point.placeId === selection.id && point.id === link.accessPointId));
                      return (
                        <g key={node.id} data-entity="node" className={`node-mark kind-${node.kind} ${selected ? "selected" : ""} ${linked ? "linked" : ""} ${pendingBuildingNodeId === node.id ? "pending-building" : ""} ${mode === "navigate" && routeNodeIds.has(node.id) ? "route-active" : ""}`} transform={`translate(${node.position.x} ${node.position.y})`} onPointerDown={(event) => { event.stopPropagation(); if (mode === "navigate") return; if (tool === "hallway") addHallwayAt(node.position, node.id); else if (tool === "building") addBuildingConnectionAt(node.position, node.id); else if (tool === "access") toggleRoomLink(node); else { setSelection({ kind: "node", id: node.id }); setTool("select"); } }}>
                          <circle r="10" /><circle r="3" />
                        </g>
                      );
                    })}
                    {floorBuildingConnections.map((segment) => {
                      const from = nodeMap.get(segment.fromNodeId);
                      const to = nodeMap.get(segment.toNodeId);
                      if (!from || !to) return null;
                      const local = from.floorId === selectedFloorId ? from : to;
                      const remote = local.id === from.id ? to : from;
                      const remoteFloor = project.topology.floors.find(({ id }) => id === remote.floorId);
                      const remoteBuilding = project.topology.buildings.find(({ id }) => id === remoteFloor?.buildingId);
                      const selected = selection?.kind === "segment" && selection.id === segment.id;
                      const isRoute = routeSegmentIds.has(segment.id);
                      return <g key={segment.id} data-entity="building-connection" className={`building-connection-mark ${selected ? "selected" : ""} ${mode === "navigate" && isRoute ? "route-active" : ""} ${mode === "navigate" && route && !isRoute ? "route-muted" : ""}`} transform={`translate(${local.position.x} ${local.position.y})`} onPointerDown={(event) => { event.stopPropagation(); if (mode === "annotate") { if (tool === "hallway") addHallwayAt(local.position, local.id); else if (tool === "building") addBuildingConnectionAt(local.position, local.id); else if (tool === "access") toggleRoomLink(local); else { setSelection({ kind: "segment", id: segment.id }); setTool("select"); } } }}>
                        <circle r="19" />
                        <path d="M -9 -3 H 7 M 3 -8 L 9 -3 L 3 2 M 9 5 H -7 M -3 0 L -9 5 L -3 10" />
                        <text x="27" y="-2">TO {remoteBuilding?.name ?? "OTHER BUILDING"}</text>
                        <text className="portal-floor-label" x="27" y="12">FLOOR {remoteFloor?.level ?? "?"}</text>
                      </g>;
                    })}
                    {floorAccessPoints.map((point) => {
                      const place = project.topology.places.find(({ id }) => id === point.placeId);
                      const position = point.connection === "terminal" ? point.position : nodeMap.get(point.nodeId)?.position;
                      if (!place || !position) return null;
                      const selected = selection?.kind === "place" && selection.id === place.id;
                      return (
                        <g key={point.id} data-entity="place" className={`place-mark kind-${place.kind} ${selected ? "selected" : ""} ${mode === "navigate" && routePlaceIds.has(place.id) ? "route-active" : ""} ${mode === "navigate" && route && !routePlaceIds.has(place.id) ? "route-muted" : ""}`} transform={`translate(${position.x} ${position.y})`} onPointerDown={(event) => { event.stopPropagation(); if (mode === "navigate") return; if (tool === "hallway" && point.connection === "network_node") addHallwayAt(position, point.nodeId); else { setSelection({ kind: "place", id: place.id }); setTool(place.kind === "room" ? "access" : "select"); } }}>
                          <circle r="15" /><IconMark kind={place.kind} />
                          <text className="place-label" x="21" y="5">{place.kind === "room" ? place.roomNumber : place.name}</text>
                        </g>
                      );
                    })}
                  </g>
                </svg>
                {mode === "annotate" && <div className="canvas-tools" role="toolbar" aria-label="Map tools">
                  {(Object.keys(TOOL_INFO) as Tool[]).map((key) => <button key={key} className={tool === key ? "active" : ""} onClick={() => selectTool(key)} title={TOOL_INFO[key].hint}><span>{TOOL_INFO[key].icon}</span>{TOOL_INFO[key].label}</button>)}
                </div>}
                <div className="zoom-tools"><button onClick={() => setViewBox((box) => ({ ...box, width: box.width * .82, height: box.height * .82 }))} aria-label="Zoom in">＋</button><button onClick={fitPlan} aria-label="Fit plan">⌗</button><button onClick={() => setViewBox((box) => ({ ...box, width: box.width * 1.2, height: box.height * 1.2 }))} aria-label="Zoom out">−</button></div>
                <div className="tool-hint"><b>{mode === "navigate" ? "Route view" : TOOL_INFO[tool].label}</b><span>{mode === "navigate" ? (route ? `Showing ${route.floorIds.length} sheet${route.floorIds.length === 1 ? "" : "s"}` : "Choose two destinations in the navigator") : pendingBuildingNodeId ? "First portal set. Switch buildings and click the other portal. Esc cancels." : pendingHallNodeId ? "Click another node or empty point. Esc cancels." : TOOL_INFO[tool].hint}</span></div>
              </div>
            </>
          ) : (
            <EmptyStage onImport={() => floorInputRef.current?.click()} importing={importing} />
          )}
        </section>

        {mode === "annotate" ? <Inspector
          project={project}
          buildingId={activeBuildingId}
          floorId={selectedFloorId}
          selection={selection}
          tool={tool}
          issues={issues}
          patchNode={patchNode}
          patchPlace={patchPlace}
          patchSegment={patchSegment}
          patchEdge={patchEdge}
          beginHallwayFromNode={beginHallwayFromNode}
          deleteSelection={() => deleteSelection(selection)}
          removeFloor={(id) => setDeleteRequest({ kind: "floor", id })}
          removeBuilding={(id) => setDeleteRequest({ kind: "building", id })}
          setProject={setProject}
        /> : <NavigationPanel
          project={project}
          activeFloorId={selectedFloorId}
          route={route}
          fromPlaceId={routeFrom}
          toPlaceId={routeTo}
          error={routeError}
          stepFree={stepFree}
          setFromPlaceId={setRouteFrom}
          setToPlaceId={setRouteTo}
          setStepFree={setStepFree}
          setActiveFloorId={selectFloor}
          onPlanRoute={planRoute}
          onClear={() => { setRoute(null); setRouteError(null); }}
        />}
      </section>

      {notice && <button className="toast" onClick={() => setNotice(null)}>{notice}<span>×</span></button>}
      {deleteRequest && <DeleteDialog project={project} request={deleteRequest} onCancel={() => setDeleteRequest(null)} onConfirm={confirmDeletion} />}
      <input ref={floorInputRef} hidden type="file" accept="image/png,image/jpeg,image/webp,application/pdf,.pdf" multiple onChange={importFloorPlans} />
      <input ref={projectInputRef} hidden type="file" accept="application/json,.json" onChange={importProject} />
    </main>
  );
}

function EmptyStage({ onImport, importing }: { onImport: () => void; importing: boolean }) {
  return (
    <div className="empty-stage">
      <div className="empty-grid" aria-hidden="true"><i /><i /><i /><i /><i /></div>
      <div className="empty-copy"><span className="sheet-stamp">NEW SURVEY</span><h1>Turn floorplans into a navigable graph.</h1><p>Import up to ten—or a hundred—floorplan images. Multi-page PDFs become separate editable floors automatically.</p><button className="primary-button large" onClick={onImport} disabled={importing}>{importing ? "Rendering…" : "Choose floorplans"}</button><small>PDF, PNG, JPG, or WebP · select multiple files</small></div>
      <div className="empty-legend"><span><b className="legend-node" />Routing node</span><span><b className="legend-path" />Directed hallway</span><span><b className="legend-room" />Terminal destination</span></div>
    </div>
  );
}

interface NavigationPanelProps {
  project: MapAuthoringProject;
  activeFloorId: FloorId | null;
  route: IndoorRoute | null;
  fromPlaceId: PlaceId | null;
  toPlaceId: PlaceId | null;
  error: string | null;
  stepFree: boolean;
  setFromPlaceId: (id: PlaceId | null) => void;
  setToPlaceId: (id: PlaceId | null) => void;
  setStepFree: (value: boolean) => void;
  setActiveFloorId: (id: FloorId | null) => void;
  onPlanRoute: () => void;
  onClear: () => void;
}

function NavigationPanel({ project, activeFloorId, route, fromPlaceId, toPlaceId, error, stepFree, setFromPlaceId, setToPlaceId, setStepFree, setActiveFloorId, onPlanRoute, onClear }: NavigationPanelProps) {
  const topology = project.topology;
  const buildingName = (buildingId: BuildingId) => topology.buildings.find(({ id }) => id === buildingId)?.name ?? buildingId;
  const floor = (floorId: FloorId) => topology.floors.find(({ id }) => id === floorId);
  const floorName = (floorId: FloorId) => {
    const item = floor(floorId);
    return item ? `${buildingName(item.buildingId)} · Floor ${item.level}` : floorId;
  };
  const accessByPlace = new Map<PlaceId, typeof topology.accessPoints>();
  topology.places.forEach((place) => accessByPlace.set(place.id, topology.accessPoints.filter(({ placeId }) => placeId === place.id)));
  const options = topology.places
    .filter((place) => (accessByPlace.get(place.id)?.length ?? 0) > 0)
    .map((place) => {
      const floorLabels = [...new Set((accessByPlace.get(place.id) ?? []).map((point) => topology.floors.find(({ id }) => id === point.floorId)?.level ?? "?"))];
      return { place, floorLabel: floorLabels.join("/"), buildingLabel: buildingName(place.buildingId) };
    })
    .sort((a, b) => a.buildingLabel.localeCompare(b.buildingLabel) || a.floorLabel.localeCompare(b.floorLabel, undefined, { numeric: true }) || a.place.name.localeCompare(b.place.name, undefined, { numeric: true }));
  const connectorIssues = auditVerticalConnectors(topology);
  const routeSteps = route ? [
    ...(route.originInstruction ? [{ key: "origin", instruction: route.originInstruction, kind: "depart", floorId: route.floorIds[0] }] : []),
    ...route.traversals.map((step, index) => ({ key: `step-${index}`, instruction: step.instruction, kind: step.kind, floorId: step.destinationFloorId ?? step.floorId })),
    ...(route.arrivalInstruction ? [{ key: "arrival", instruction: route.arrivalInstruction, kind: "arrive", floorId: route.floorIds.at(-1) }] : []),
  ] : [];

  return (
    <aside className="inspector navigator-panel">
      <div className="inspector-heading"><span>Navigator</span><b>{route ? "ROUTE READY" : "PLAN"}</b></div>
      <div className="navigator-body">
        <div className="nav-intro"><span>WAYFINDING / BETA</span><h2>Where are you headed?</h2><p>Terminal room links are used only at departure and arrival. The route itself stays on the hallway graph.</p></div>
        <Field label="Starting point"><select data-testid="route-from" value={fromPlaceId ?? ""} onChange={(event) => { setFromPlaceId(event.target.value ? asPlaceId(event.target.value) : null); onClear(); }}><option value="">Choose a place…</option>{options.map(({ place, floorLabel, buildingLabel }) => <option key={place.id} value={place.id}>{buildingLabel} · F{floorLabel} · {place.name}</option>)}</select></Field>
        <button className="swap-route" aria-label="Swap start and destination" onClick={() => { const previousFrom = fromPlaceId; setFromPlaceId(toPlaceId); setToPlaceId(previousFrom); onClear(); }}>⇅ <span>swap</span></button>
        <Field label="Destination"><select data-testid="route-to" value={toPlaceId ?? ""} onChange={(event) => { setToPlaceId(event.target.value ? asPlaceId(event.target.value) : null); onClear(); }}><option value="">Choose a place…</option>{options.map(({ place, floorLabel, buildingLabel }) => <option key={place.id} value={place.id}>{buildingLabel} · F{floorLabel} · {place.name}</option>)}</select></Field>
        <label className={`accessibility-toggle ${stepFree ? "active" : ""}`}>
          <input data-testid="step-free-toggle" type="checkbox" checked={stepFree} onChange={(event) => { setStepFree(event.target.checked); onClear(); }} />
          <span className="toggle-track" aria-hidden="true"><i /></span>
          <span className="toggle-copy"><b>Step-free route</b><small>Avoid stairs and marked barriers</small></span>
          <span className="accessibility-mark" aria-hidden="true">♿</span>
        </label>
        <button className="plan-route-button" onClick={onPlanRoute}>Find route <span>→</span></button>

        {error && <div className="route-error"><b>No route found</b><p>{error}</p>{connectorIssues.length > 0 && <span>{connectorIssues.length} vertical connector issue{connectorIssues.length === 1 ? "" : "s"} must be resolved in Annotate mode.</span>}</div>}

        {route && <>
          {stepFree && <div className="route-preference-note"><span>♿</span><div><b>Step-free route</b><small>Stairs and marked barriers were excluded.</small></div></div>}
          <div className="route-summary"><div><span>Relative cost</span><b>{Math.round(route.totalCost)}</b></div><div><span>Sheets</span><b>{route.floorIds.length}</b></div></div>
          <div className="route-floor-tabs">{route.floorIds.map((floorId) => <button key={floorId} className={floorId === activeFloorId ? "active" : ""} onClick={() => setActiveFloorId(floorId)}>{floorName(floorId)}</button>)}</div>
          <section className="route-steps"><h3>Directions</h3>{routeSteps.map((step, index) => <button key={step.key} className={`route-step kind-${step.kind}`} onClick={() => step.floorId && setActiveFloorId(step.floorId)}><span className="step-number">{String(index + 1).padStart(2, "0")}</span><span className="step-copy"><b>{step.kind === "stairs" ? "Stairs" : step.kind === "elevator" ? "Elevator" : step.kind === "building_connection" ? "Building connector" : step.kind === "arrive" ? "Arrive" : step.kind === "depart" ? "Depart" : step.floorId ? floorName(step.floorId) : "Continue"}</b><small>{step.instruction}</small></span></button>)}</section>
        </>}

        {!route && !error && <div className="route-empty"><span>↗</span><p>Select two destinations to calculate the shortest directed path.</p><small>{connectorIssues.length ? `${connectorIssues.length} connector issues detected` : "Cross-floor connectors are ready"}</small></div>}
      </div>
    </aside>
  );
}

interface InspectorProps {
  project: MapAuthoringProject;
  buildingId: BuildingId | null;
  floorId: FloorId | null;
  selection: Selection;
  tool: Tool;
  issues: readonly { message: string; path: string }[];
  patchNode: (id: string, patch: Partial<RoutingNode>) => void;
  patchPlace: (id: string, patch: Partial<Place>) => void;
  patchSegment: (id: string, patch: Partial<PathSegment>) => void;
  patchEdge: (id: string, patch: Partial<DirectedEdge>) => void;
  beginHallwayFromNode: (nodeId: NodeId) => void;
  deleteSelection: () => void;
  removeFloor: (id: FloorId) => void;
  removeBuilding: (id: BuildingId) => void;
  setProject: React.Dispatch<React.SetStateAction<MapAuthoringProject>>;
}

function Inspector({ project, buildingId, floorId, selection, tool, issues, patchNode, patchPlace, patchSegment, patchEdge, beginHallwayFromNode, deleteSelection, removeFloor, removeBuilding, setProject }: InspectorProps) {
  const topology = project.topology;
  const building = topology.buildings.find(({ id }) => id === buildingId);
  const floor = topology.floors.find(({ id }) => id === floorId);
  const node = selection?.kind === "node" ? topology.nodes.find(({ id }) => id === selection.id) : undefined;
  const segment = selection?.kind === "segment" ? topology.segments.find(({ id }) => id === selection.id) : undefined;
  const place = selection?.kind === "place" ? topology.places.find(({ id }) => id === selection.id) : undefined;
  const edges = segment ? topology.edges.filter(({ segmentId }) => segmentId === segment.id) : [];
  const activePortalNode = segment?.kind === "building_connection"
    ? [segment.fromNodeId, segment.toNodeId]
      .map((nodeId) => topology.nodes.find(({ id }) => id === nodeId))
      .find((item) => item?.floorId === floorId)
    : undefined;
  const points = place ? topology.accessPoints.filter(({ placeId }) => placeId === place.id) : [];
  const links = topology.terminalAccessLinks.filter((link) => points.some(({ id }) => id === link.accessPointId));
  const networkNodeIds = new Set(points.flatMap((point) => point.connection === "network_node" ? [point.nodeId] : []));
  const connector = place ? topology.verticalConnectors.find((item) => item.stopNodeIds.some((nodeId) => networkNodeIds.has(nodeId))) : undefined;

  const renameBuilding = (name: string) => {
    if (!buildingId) return;
    setProject((current) => updateTopology(current, (map) => ({ ...map, buildings: map.buildings.map((item) => item.id === buildingId ? { ...item, name } : item) })));
  };
  const renameFloor = (field: "name" | "level", value: string) => {
    if (!floorId) return;
    setProject((current) => updateTopology(current, (map) => ({ ...map, floors: map.floors.map((item) => item.id === floorId ? { ...item, [field]: value } : item) })));
  };
  const moveFloorToBuilding = (nextBuildingId: BuildingId) => {
    if (!floorId) return;
    setProject((current) => updateTopology(current, (map) => {
      const placeIds = new Set(map.accessPoints.filter((point) => point.floorId === floorId).map((point) => point.placeId));
      return {
        ...map,
        floors: map.floors.map((item) => item.id === floorId ? { ...item, buildingId: nextBuildingId } : item),
        places: map.places.map((item) => placeIds.has(item.id) ? { ...item, buildingId: nextBuildingId } : item),
      };
    }));
  };

  return (
    <aside className="inspector">
      <div className="inspector-heading"><span>Inspector</span><b>{selection ? selection.kind.toUpperCase() : "SHEET"}</b></div>
      {node && <div className="inspector-body">
        <Field label="Label"><input value={node.label ?? ""} onChange={(event) => patchNode(node.id, { label: event.target.value })} /></Field>
        <Field label="Node type"><select value={node.kind} onChange={(event) => patchNode(node.id, { kind: event.target.value as NodeKind })}>{NODE_KINDS.map((kind) => <option key={kind}>{kind}</option>)}</select></Field>
        <div className="coordinate-pair"><Field label="X"><output>{node.position.x}</output></Field><Field label="Y"><output>{node.position.y}</output></Field></div>
        <Field label="Arrival cue"><textarea value={node.arrivalCue ?? ""} onChange={(event) => patchNode(node.id, { arrivalCue: event.target.value })} placeholder="What confirms arrival here?" /></Field>
        {node.kind === "building_portal" && <button className="connector-action" onClick={() => beginHallwayFromNode(node.id)}>Start hallway from this portal <span>→</span></button>}
        <DangerButton onClick={deleteSelection}>Delete node and attached paths</DangerButton>
      </div>}
      {segment && <div className="inspector-body">
        {segment.kind === "building_connection" && activePortalNode && <div className="connection-panel portal-connection-panel"><h3>Building portal</h3><div className="connector-rule"><b>One marker, two jobs</b><span>This portal is already a routing node. Join it directly to the local hallway—do not place a second node on top of it.</span></div><button className="connector-action" data-testid="portal-hallway-action" onClick={() => beginHallwayFromNode(activePortalNode.id)}>Start hallway from this portal <span>→</span></button></div>}
        <Field label="Path label"><input value={segment.label ?? ""} onChange={(event) => patchSegment(segment.id, { label: event.target.value })} /></Field>
        <Field label="Segment type"><select value={segment.kind} onChange={(event) => patchSegment(segment.id, { kind: event.target.value as SegmentKind })}>{SEGMENT_KINDS.map((kind) => <option key={kind}>{kind}</option>)}</select></Field>
        <section className="edge-editor"><h3>Directed travel</h3>{edges.map((edge, index) => <div className="edge-card" key={edge.id}><span className="edge-index">{index === 0 ? "A → B" : "B → A"}</span><Field label="Direction"><select value={edge.direction ?? "east"} onChange={(event) => patchEdge(edge.id, { direction: event.target.value as CardinalDirection })}>{DIRECTIONS.map((direction) => <option key={direction}>{direction}</option>)}</select></Field><Field label="Instruction"><textarea value={edge.instruction} onChange={(event) => patchEdge(edge.id, { instruction: event.target.value })} /></Field><Field label="Cost"><input type="number" min="0.1" step="0.1" value={edge.cost} onChange={(event) => patchEdge(edge.id, { cost: Number(event.target.value) })} /></Field></div>)}</section>
        <DangerButton onClick={deleteSelection}>Delete path and both directions</DangerButton>
      </div>}
      {place && <div className="inspector-body">
        <div className={`place-chip ${place.kind}`}>{place.kind}</div>
        <Field label="Destination name"><input value={place.name} onChange={(event) => patchPlace(place.id, { name: event.target.value })} autoFocus={place.kind === "room"} /></Field>
        {place.kind === "room" && <Field label="Room number"><input value={place.roomNumber} onChange={(event) => patchPlace(place.id, { roomNumber: event.target.value, name: event.target.value })} /></Field>}
        <Field label="Search aliases"><input value={(place.aliases ?? []).join(", ")} onChange={(event) => patchPlace(place.id, { aliases: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) })} placeholder="WEH 4707, 4707" /></Field>
        {place.kind === "room" && <div className="connection-panel"><h3>Terminal access</h3><p>{tool === "access" ? "Click hallway nodes to add or remove endpoint links." : "Choose Room link, then click reachable hallway nodes."}</p><strong>{links.length} endpoint{links.length === 1 ? "" : "s"} connected</strong>{links.map((link) => <details className="connection-detail" key={link.id}><summary><span>{topology.nodes.find(({ id }) => id === link.nodeId)?.label ?? link.nodeId}</span><b>{Math.round(link.cost)}</b></summary><Field label="Direction to room"><select value={link.directionToPlace ?? "east"} onChange={(event) => setProject((current) => updateTopology(current, (map) => ({ ...map, terminalAccessLinks: map.terminalAccessLinks.map((item) => item.id === link.id ? { ...item, directionToPlace: event.target.value as CardinalDirection } : item) })))}>{DIRECTIONS.map((direction) => <option key={direction}>{direction}</option>)}</select></Field><Field label="Arrival instruction"><textarea value={link.toPlaceInstruction} onChange={(event) => setProject((current) => updateTopology(current, (map) => ({ ...map, terminalAccessLinks: map.terminalAccessLinks.map((item) => item.id === link.id ? { ...item, toPlaceInstruction: event.target.value } : item) })))} /></Field><Field label="Cost"><input type="number" min="0.1" step="0.1" value={link.cost} onChange={(event) => setProject((current) => updateTopology(current, (map) => ({ ...map, terminalAccessLinks: map.terminalAccessLinks.map((item) => item.id === link.id ? { ...item, cost: Number(event.target.value) } : item) })))} /></Field><button className="unlink-button" onClick={() => setProject((current) => updateTopology(current, (map) => ({ ...map, terminalAccessLinks: map.terminalAccessLinks.filter(({ id }) => id !== link.id) })))}>Remove link</button></details>)}</div>}
        {connector && <ConnectorEditor connectorId={connector.id} project={project} activeFloorId={floorId} setProject={setProject} beginHallwayFromNode={beginHallwayFromNode} />}
        <DangerButton onClick={deleteSelection}>Delete destination</DangerButton>
      </div>}
      {!selection && floor && <div className="inspector-body">
        <Field label="Building"><select value={floor.buildingId} onChange={(event) => moveFloorToBuilding(asBuildingId(event.target.value))}>{topology.buildings.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
        <Field label="Building name"><input value={building?.name ?? ""} onChange={(event) => renameBuilding(event.target.value)} /></Field>
        <Field label="Floor name"><input value={floor.name ?? ""} onChange={(event) => renameFloor("name", event.target.value)} /></Field>
        <Field label="Level"><input value={floor.level} onChange={(event) => renameFloor("level", event.target.value)} /></Field>
        <div className="sheet-stats"><div><b>{topology.nodes.filter((item) => item.floorId === floorId).length}</b><span>nodes</span></div><div><b>{topology.segments.filter((item) => topology.nodes.find((nodeItem) => nodeItem.id === item.fromNodeId)?.floorId === floorId).length}</b><span>paths</span></div><div><b>{topology.accessPoints.filter((item) => item.floorId === floorId).length}</b><span>places</span></div></div>
        <section className="quick-guide"><h3>Fast annotation pass</h3><ol><li>Place or chain hallway nodes.</li><li>Add rooms at their doors.</li><li>Link each room to reachable endpoints.</li><li>Add and group vertical connectors.</li><li>Use Connector to join portal points in different buildings.</li></ol></section>
        <section className="removal-zone"><h3>Remove data</h3><p>These actions also remove attached annotations and routes.</p><button onClick={() => removeFloor(floor.id)}>Remove this floorplan</button><button onClick={() => building && removeBuilding(building.id)} disabled={!building || topology.buildings.length <= 1}>Remove {building?.name ?? "building"}</button>{topology.buildings.length <= 1 && <small>The project must keep one building.</small>}</section>
      </div>}
      {!floor && building && <div className="inspector-body building-empty-inspector"><span className="place-chip building-chip">building</span><Field label="Building name"><input value={building.name} onChange={(event) => renameBuilding(event.target.value)} autoFocus /></Field><p>Import one or more floorplans into this building, then place a connector portal where its hallway enters.</p><section className="removal-zone"><h3>Remove data</h3><button onClick={() => removeBuilding(building.id)} disabled={topology.buildings.length <= 1}>Remove {building.name}</button>{topology.buildings.length <= 1 && <small>The project must keep one building.</small>}</section></div>}
      {!floor && !building && <div className="inspector-empty"><span>⌖</span><p>Import a plan to begin a new survey.</p></div>}
      {issues.length > 0 && <details className="validation-panel"><summary>{issues.length} validation issue{issues.length === 1 ? "" : "s"}</summary>{issues.slice(0, 8).map((issue, index) => <p key={`${issue.path}-${index}`}><b>{issue.path}</b>{issue.message}</p>)}</details>}
    </aside>
  );
}

function DeleteDialog({ project, request, onCancel, onConfirm }: { project: MapAuthoringProject; request: DeleteRequest; onCancel: () => void; onConfirm: () => void }) {
  const topology = project.topology;
  const floorIds = new Set<FloorId>(request.kind === "floor"
    ? [request.id]
    : topology.floors.filter((floor) => floor.buildingId === request.id).map(({ id }) => id));
  const nodeIds = new Set(topology.nodes.filter((node) => floorIds.has(node.floorId)).map(({ id }) => id));
  const segmentCount = topology.segments.filter((segment) => nodeIds.has(segment.fromNodeId) || nodeIds.has(segment.toNodeId)).length;
  const placeCount = new Set(topology.accessPoints.filter((point) => floorIds.has(point.floorId)).map(({ placeId }) => placeId)).size;
  const floor = request.kind === "floor" ? topology.floors.find(({ id }) => id === request.id) : undefined;
  const building = request.kind === "building" ? topology.buildings.find(({ id }) => id === request.id) : topology.buildings.find(({ id }) => id === floor?.buildingId);
  const title = request.kind === "floor" ? `Remove ${floor?.name ?? "this floorplan"}?` : `Remove ${building?.name ?? "this building"}?`;
  const description = request.kind === "floor"
    ? "The floorplan and everything annotated on its sheet will be removed. Connections into other floors or buildings will also be cleaned up."
    : "Every floorplan in this building and all attached annotations will be removed. Connector hallways into neighboring buildings will also be cleaned up.";

  return <div className="dialog-backdrop" role="presentation" onPointerDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
    <section className="delete-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-dialog-title" aria-describedby="delete-dialog-description">
      <span className="dialog-stamp">DELETION SCOPE</span>
      <h2 id="delete-dialog-title">{title}</h2>
      <p id="delete-dialog-description">{description}</p>
      <div className="delete-summary"><div><b>{floorIds.size}</b><span>floorplan{floorIds.size === 1 ? "" : "s"}</span></div><div><b>{nodeIds.size}</b><span>nodes</span></div><div><b>{segmentCount}</b><span>paths</span></div><div><b>{placeCount}</b><span>places</span></div></div>
      <div className="dialog-actions"><button className="cancel-delete" onClick={onCancel}>Keep it</button><button className="confirm-delete" data-testid="confirm-delete" onClick={onConfirm}>Remove permanently</button></div>
    </section>
  </div>;
}

function ConnectorEditor({ connectorId, project, activeFloorId, setProject, beginHallwayFromNode }: { connectorId: string; project: MapAuthoringProject; activeFloorId: FloorId | null; setProject: React.Dispatch<React.SetStateAction<MapAuthoringProject>>; beginHallwayFromNode: (nodeId: NodeId) => void }) {
  const connector = project.topology.verticalConnectors.find(({ id }) => id === connectorId);
  if (!connector) return null;
  const floors = connector.stopNodeIds.map((id) => project.topology.nodes.find((node) => node.id === id)?.floorId).filter(Boolean);
  const connectorBuildingId = project.topology.floors.find(({ id }) => id === floors[0])?.buildingId;
  const peers = project.topology.verticalConnectors.filter((item) => {
    if (item.kind !== connector.kind) return false;
    const peerFloorId = project.topology.nodes.find(({ id }) => id === item.stopNodeIds[0])?.floorId;
    return project.topology.floors.find(({ id }) => id === peerFloorId)?.buildingId === connectorBuildingId;
  });
  const activeStopId = connector.stopNodeIds.find((nodeId) => project.topology.nodes.find((node) => node.id === nodeId)?.floorId === activeFloorId);
  const regroup = (targetId: string) => {
    if (targetId === connector.id) return;
    setProject((current) => updateTopology(current, (topology) => ({
      ...topology,
      verticalConnectors: topology.verticalConnectors
        .filter(({ id }) => id !== connector.id)
        .map((item) => item.id === targetId
          ? { ...item, stopNodeIds: [...new Set([...item.stopNodeIds, ...connector.stopNodeIds])], segmentIds: [...new Set([...item.segmentIds, ...connector.segmentIds])] }
          : item),
    })));
  };
  return <div className="connection-panel"><h3>Vertical connector</h3><div className="connector-rule"><b>One marker, two jobs</b><span>This stop is already a routing node. Join it directly to the hallway—do not place a second node on top of it.</span></div>{activeStopId && <button className="connector-action" onClick={() => beginHallwayFromNode(activeStopId)}>Start hallway from this stop <span>→</span></button>}<Field label="Group name"><input value={connector.name} onChange={(event) => setProject((current) => updateTopology(current, (topology) => ({ ...topology, verticalConnectors: topology.verticalConnectors.map((item) => item.id === connector.id ? { ...item, name: event.target.value } : item) })))} /></Field>{peers.length > 1 && <Field label="Merge into group"><select value={connector.id} onChange={(event) => regroup(event.target.value)}>{peers.map((item) => <option key={item.id} value={item.id}>{item.name}{item.id === connector.id ? " (current)" : ""}</option>)}</select></Field>}<p>{floors.length} floor stop{floors.length === 1 ? "" : "s"}. Place the matching landing on each floor, then merge those stops into one named group.</p></div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}
function DangerButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return <button className="danger-button" onClick={onClick}>{children}</button>;
}
