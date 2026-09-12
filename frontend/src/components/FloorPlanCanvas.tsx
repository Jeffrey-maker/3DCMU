import { useState } from "react";
import type { PointerEvent as ReactPointerEvent, KeyboardEvent as ReactKeyboardEvent } from "react";
import type {
  DoorPassageAttachment,
  EdgeType,
  GraphEdge,
  GraphNode,
  NodeType,
  PassageLine,
} from "../types/graph";

const NODE_TYPES: NodeType[] = ["room", "corridor", "door", "stair", "elevator", "entrance", "outdoor"];
const EDGE_TYPES: EdgeType[] = ["hallway", "door", "stairs", "elevator", "outdoor_path"];

const NODE_COLORS: Record<NodeType, string> = {
  room: "#2563eb",
  corridor: "#f59e0b",
  door: "#10b981",
  stair: "#8b5cf6",
  elevator: "#7c3aed",
  entrance: "#ef4444",
  outdoor: "#6b7280",
};

type EditMode = "select" | "add-node" | "connect";

function makeNodeId(building: string, floor: number, type: NodeType): string {
  return `${building}-${floor}-${type}-${crypto.randomUUID().slice(0, 8)}`;
}

function makeEdgeId(): string {
  return `edge-${crypto.randomUUID().slice(0, 8)}`;
}

interface FloorPlanCanvasProps {
  rasterUrl: string;
  viewBoxWidth?: number;
  viewBoxHeight?: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  editable: boolean;
  showNodes?: boolean;
  showCorridorNodes?: boolean;
  passageLines?: boolean;
  hideHallwayEdges?: boolean;
  passageways?: PassageLine[];
  doorAttachments?: DoorPassageAttachment[];
  highlightedPoints?: { x: number; y: number }[];
  highlightedPath?: string[];
  currentStepNodeId?: string | null;
  onNodesChange?: (nodes: GraphNode[]) => void;
  onEdgesChange?: (edges: GraphEdge[]) => void;
}

export function FloorPlanCanvas({
  rasterUrl,
  viewBoxWidth = 1224,
  viewBoxHeight = 792,
  nodes,
  edges,
  editable,
  showNodes = true,
  showCorridorNodes = true,
  passageLines = false,
  hideHallwayEdges = false,
  passageways = [],
  doorAttachments = [],
  highlightedPoints,
  highlightedPath,
  currentStepNodeId,
  onNodesChange,
  onEdgesChange,
}: FloorPlanCanvasProps) {
  const [mode, setMode] = useState<EditMode>("select");
  const [newNodeType, setNewNodeType] = useState<NodeType>("door");
  const [newEdgeType, setNewEdgeType] = useState<EdgeType>("door");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [connectFromId, setConnectFromId] = useState<string | null>(null);
  const [draggingNodeId, setDraggingNodeId] = useState<string | null>(null);
  const [svgEl, setSvgEl] = useState<SVGSVGElement | null>(null);

  const nodesById = new Map(nodes.map((n) => [n.id, n]));

  function toSvgPoint(clientX: number, clientY: number): { x: number; y: number } {
    if (!svgEl) return { x: 0, y: 0 };
    const ctm = svgEl.getScreenCTM();
    if (!ctm) return { x: 0, y: 0 };
    const pt = svgEl.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const p = pt.matrixTransform(ctm.inverse());
    return { x: Math.round(p.x * 10) / 10, y: Math.round(p.y * 10) / 10 };
  }

  function handleCanvasClick(evt: ReactPointerEvent<SVGSVGElement>) {
    if (mode !== "add-node" || !onNodesChange) return;
    const { x, y } = toSvgPoint(evt.clientX, evt.clientY);
    const building = nodes[0]?.building ?? "";
    const floor = nodes[0]?.floor ?? 1;
    const newNode: GraphNode = {
      id: makeNodeId(building, floor, newNodeType),
      building,
      floor,
      x,
      y,
      type: newNodeType,
      label: null,
      room_type: null,
      department: null,
    };
    onNodesChange([...nodes, newNode]);
    setSelectedNodeId(newNode.id);
  }

  function handleNodePointerDown(nodeId: string, evt: ReactPointerEvent<SVGCircleElement>) {
    evt.stopPropagation();
    if (mode === "connect") {
      if (connectFromId === null) {
        setConnectFromId(nodeId);
      } else if (connectFromId !== nodeId && onEdgesChange) {
        const from = nodesById.get(connectFromId);
        const to = nodesById.get(nodeId);
        // Public circulation is represented by passage polylines. A direct
        // door-to-door edge would recreate the wall-crossing network that the
        // passage model intentionally replaced.
        if (from?.type === "door" && to?.type === "door") {
          setConnectFromId(null);
          return;
        }
        const newEdge: GraphEdge = {
          id: makeEdgeId(),
          from_node: connectFromId,
          to_node: nodeId,
          weight: 0,
          type: newEdgeType,
        };
        onEdgesChange([...edges, newEdge]);
        setConnectFromId(null);
      }
      return;
    }
    setSelectedNodeId(nodeId);
    setSelectedEdgeId(null);
    setDraggingNodeId(nodeId);
  }

  function handlePointerMove(evt: ReactPointerEvent<SVGSVGElement>) {
    if (draggingNodeId === null || !onNodesChange) return;
    const { x, y } = toSvgPoint(evt.clientX, evt.clientY);
    onNodesChange(nodes.map((n) => (n.id === draggingNodeId ? { ...n, x, y } : n)));
  }

  function deleteSelected() {
    if (selectedNodeId && onNodesChange && onEdgesChange) {
      onNodesChange(nodes.filter((n) => n.id !== selectedNodeId));
      onEdgesChange(edges.filter((e) => e.from_node !== selectedNodeId && e.to_node !== selectedNodeId));
      setSelectedNodeId(null);
    } else if (selectedEdgeId && onEdgesChange) {
      onEdgesChange(edges.filter((e) => e.id !== selectedEdgeId));
      setSelectedEdgeId(null);
    }
  }

  function handleKeyDown(evt: ReactKeyboardEvent<SVGSVGElement>) {
    if (evt.key === "Delete" || evt.key === "Backspace") {
      evt.preventDefault();
      deleteSelected();
    }
  }

  function updateSelectedNode(patch: Partial<GraphNode>) {
    if (!selectedNodeId || !onNodesChange) return;
    onNodesChange(nodes.map((n) => (n.id === selectedNodeId ? { ...n, ...patch } : n)));
  }

  const selectedNode = selectedNodeId ? nodesById.get(selectedNodeId) : null;
  const highlightedSet = new Set(highlightedPath ?? []);
  const pathPoints = (highlightedPoints ?? (highlightedPath ?? [])
    .map((id) => nodesById.get(id))
    .filter((n): n is GraphNode => n !== undefined))
    .map((n) => `${n.x},${n.y}`)
    .join(" ");

  return (
    <div className="floor-plan-canvas">
      {editable && (
        <div className="canvas-toolbar">
          <button
            type="button"
            className={mode === "select" ? "active" : ""}
            onClick={() => {
              setMode("select");
              setConnectFromId(null);
            }}
          >
            Select / Move
          </button>
          <button
            type="button"
            className={mode === "add-node" ? "active" : ""}
            onClick={() => {
              setMode("add-node");
              setConnectFromId(null);
            }}
          >
            Add Node
          </button>
          {mode === "add-node" && (
            <select value={newNodeType} onChange={(e) => setNewNodeType(e.target.value as NodeType)}>
              {NODE_TYPES.filter((t) => showCorridorNodes || t !== "corridor").map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className={mode === "connect" ? "active" : ""}
            onClick={() => {
              setMode("connect");
              setConnectFromId(null);
            }}
          >
            Draw Edge{connectFromId ? ` (from ${connectFromId} → click target)` : ""}
          </button>
          {mode === "connect" && (
            <select value={newEdgeType} onChange={(e) => setNewEdgeType(e.target.value as EdgeType)}>
              {EDGE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          )}
          <button type="button" onClick={deleteSelected} disabled={!selectedNodeId && !selectedEdgeId}>
            Delete selected
          </button>
        </div>
      )}

      <svg
        ref={setSvgEl}
        viewBox={`0 0 ${viewBoxWidth} ${viewBoxHeight}`}
        className="floor-plan-svg"
        tabIndex={editable ? 0 : -1}
        onClick={editable ? handleCanvasClick : undefined}
        onPointerMove={editable ? handlePointerMove : undefined}
        onPointerUp={editable ? () => setDraggingNodeId(null) : undefined}
        onKeyDown={editable ? handleKeyDown : undefined}
      >
        <image href={rasterUrl} x={0} y={0} width={viewBoxWidth} height={viewBoxHeight} />

        {passageways.map((line) => (
          <polyline
            key={line.id}
            points={line.points.map(([x, y]) => `${x},${y}`).join(" ")}
            fill="none"
            stroke="#d97706"
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ))}
        {doorAttachments.map((attachment) => (
          <polyline
            key={`attachment-${attachment.door_node_id}`}
            points={attachment.points.map(([x, y]) => `${x},${y}`).join(" ")}
            fill="none"
            stroke="#94a3b8"
            strokeWidth={1.5}
          />
        ))}

        {edges.filter((edge) => !hideHallwayEdges || edge.type !== "hallway").map((edge) => {
          const from = nodesById.get(edge.from_node);
          const to = nodesById.get(edge.to_node);
          if (!from || !to) return null;
          const edgePoints = edge.points ?? [];
          const points = edgePoints.length >= 2
            ? edgePoints.map(([x, y]) => `${x},${y}`).join(" ")
            : `${from.x},${from.y} ${to.x},${to.y}`;
          return (
            <polyline
              key={edge.id}
              points={points}
              fill="none"
              stroke={selectedEdgeId === edge.id ? "#facc15" : passageLines && edge.type === "hallway" ? "#d97706" : "#94a3b8"}
              strokeWidth={selectedEdgeId === edge.id ? 3 : passageLines && edge.type === "hallway" ? 2.5 : 1.5}
              onPointerDown={
                editable
                  ? (e) => {
                      e.stopPropagation();
                      if (mode === "select") {
                        setSelectedEdgeId(edge.id);
                        setSelectedNodeId(null);
                      }
                    }
                  : undefined
              }
            />
          );
        })}

        {pathPoints && (
          <polyline
            points={pathPoints}
            fill="none"
            stroke="#dc2626"
            strokeWidth={4}
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity={0.85}
          />
        )}

        {showNodes && nodes.filter((node) => showCorridorNodes || node.type !== "corridor").map((node) => (
          <circle
            key={node.id}
            cx={node.x}
            cy={node.y}
            r={node.id === currentStepNodeId ? 9 : highlightedSet.has(node.id) ? 7 : 5}
            fill={NODE_COLORS[node.type]}
            stroke={selectedNodeId === node.id || connectFromId === node.id ? "#111827" : "white"}
            strokeWidth={selectedNodeId === node.id || connectFromId === node.id ? 2.5 : 1}
            onPointerDown={editable ? (e) => handleNodePointerDown(node.id, e) : undefined}
          />
        ))}
      </svg>

      {editable && selectedNode && (
        <div className="node-properties">
          <h4>{selectedNode.label ?? selectedNode.id}</h4>
          <label>
            Type
            <select
              value={selectedNode.type}
              onChange={(e) => updateSelectedNode({ type: e.target.value as NodeType })}
            >
              {NODE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label>
            Label
            <input
              value={selectedNode.label ?? ""}
              onChange={(e) => updateSelectedNode({ label: e.target.value || null })}
            />
          </label>
          <label>
            Room type
            <input
              value={selectedNode.room_type ?? ""}
              onChange={(e) => updateSelectedNode({ room_type: e.target.value || null })}
            />
          </label>
          <label>
            Department
            <input
              value={selectedNode.department ?? ""}
              onChange={(e) => updateSelectedNode({ department: e.target.value || null })}
            />
          </label>
        </div>
      )}
    </div>
  );
}
