from typing import Literal, Optional

from pydantic import BaseModel

NodeType = Literal[
    "room", "corridor", "door", "stair", "elevator", "entrance", "outdoor"
]
EdgeType = Literal["hallway", "door", "stairs", "elevator", "outdoor_path"]


class Node(BaseModel):
    id: str
    building: str
    floor: int
    x: float
    y: float
    type: NodeType
    label: Optional[str] = None
    room_type: Optional[str] = None
    department: Optional[str] = None


class Edge(BaseModel):
    id: str
    from_node: str
    to_node: str
    weight: float
    type: EdgeType
    points: list[tuple[float, float]] = []


class PassageLine(BaseModel):
    id: str
    points: list[tuple[float, float]]


class DoorPassageAttachment(BaseModel):
    door_node_id: str
    pathway_point: tuple[float, float]
    points: list[tuple[float, float]]


class FloorGraph(BaseModel):
    building: str
    floor: int
    nodes: list[Node] = []
    edges: list[Edge] = []
    auto_generated: bool = False
    routing_source: Literal[
        "legacy", "anchors_only", "gemini_pathways", "space_type_centerlines"
    ] = "legacy"
    page_width: float = 1224.0
    page_height: float = 792.0
    routing_warnings: list[str] = []
    unconnected_room_ids: list[str] = []
    passageways: list[PassageLine] = []
    door_attachments: list[DoorPassageAttachment] = []


class WallSegment(BaseModel):
    type: Literal["line", "curve"]
    points: list[tuple[float, float]]
    layer: Optional[str] = None  # CAD layer name (e.g. "ARCH|A-WALL"), when the PDF preserves it


class FloorDraft(BaseModel):
    """Output of the deterministic extraction pipeline: room nodes plus raw
    vector geometry, before a human has drawn any corridor/door edges."""

    building: str
    floor: int
    nodes: list[Node] = []
    raw_geometry: list[WallSegment] = []
    # Closed per-room boundaries from the CAD "A-AREA" layer, when the PDF
    # carries them. Kept as whole polygons rather than loose segments because
    # the grouping is the useful part: it answers which room a point is in,
    # and therefore which room a door actually belongs to.
    room_polygons: list[list[tuple[float, float]]] = []
    # Node id -> the doorways of that space, found from where its outline
    # opens onto walkable floor (see services/space_doors.py). Attributed to
    # the space by construction, so a door is never claimed by the room on
    # the other side of the wall.
    space_doors: dict[str, list[tuple[float, float]]] = {}
    raster_path: Optional[str] = None
