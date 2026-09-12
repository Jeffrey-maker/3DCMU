"""Deterministic PDF vector extraction — no AI, no vision model.

Ported from the build spec's Step 2b, which was validated against the real
Wean Hall Level 1 base PDF (52/52 rooms, 3,831 line segments, 95 curves).
Outputs are wrapped into the shared Pydantic models (app.models.graph) at the
boundary so the rest of the app has exactly one schema to agree on.
"""

from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF

from app.models.graph import FloorDraft, Node, WallSegment


@dataclass
class TextLabel:
    text: str
    x: float
    y: float


def extract_text_labels(pdf_path: str, page_number: int = 0) -> list[TextLabel]:
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_number]
        labels = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = span["bbox"]
                    labels.append(
                        TextLabel(text=text, x=(x0 + x1) / 2, y=(y0 + y1) / 2)
                    )
        return labels
    finally:
        doc.close()


def extract_vector_geometry(pdf_path: str, page_number: int = 0) -> list[WallSegment]:
    """Raw wall/door line segments — candidate geometry for the corridor
    graph editor, and preserved for future polygon/3D reconstruction.

    When the source PDF preserves its original CAD layers (as PDF Optional
    Content Groups -- true for this CMU ESIM export), each segment's layer
    name is captured too (e.g. "ARCH|A-WALL", "ARCH|A-DOOR"). This is a much
    more reliable way to tell a real structural wall apart from furniture,
    fixtures, or annotation lines than any geometric heuristic -- consumers
    that care about that distinction (corridor_heuristic, navgrid) use it
    when present, falling back to treating every line as a possible wall
    when a PDF doesn't have layer info.
    """
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_number]
        segments: list[WallSegment] = []
        for d in page.get_drawings():
            layer = d.get("layer")
            for item in d["items"]:
                kind = item[0]
                if kind == "l":
                    p1, p2 = item[1], item[2]
                    segments.append(
                        WallSegment(
                            type="line", points=[(p1.x, p1.y), (p2.x, p2.y)], layer=layer
                        )
                    )
                elif kind == "c":  # bezier curve — often a door swing arc
                    segments.append(
                        WallSegment(
                            type="curve", points=[(p.x, p.y) for p in item[1:]], layer=layer
                        )
                    )
        return segments
    finally:
        doc.close()


ROOM_POLYGON_LAYER = "A-AREA"


def extract_room_polygons(pdf_path: str, page_number: int = 0) -> list[list[tuple[float, float]]]:
    """Closed room boundaries from the CAD space/area layer.

    Verified on the real Wean Hall Level 4 plan: 164 of 175 room labels fall
    inside exactly one of these polygons and none falls inside two, which is
    what makes them usable as the authority on which room a door belongs to
    (the build spec's point-in-polygon fix). A drawing may hold several
    disjoint loops, so subpaths are split wherever the pen jumps instead of
    being chained into one nonsense outline.
    """
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_number]
        polygons: list[list[tuple[float, float]]] = []
        for drawing in page.get_drawings():
            if drawing.get("layer") != ROOM_POLYGON_LAYER:
                continue
            edges = [
                ((item[1].x, item[1].y), (item[2].x, item[2].y))
                for item in drawing["items"]
                if item[0] == "l"
            ]
            polygons.extend(split_subpaths(edges))
        return polygons
    finally:
        doc.close()


def split_subpaths(
    edges: list[tuple[tuple[float, float], tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    """Group consecutive edges into separate outlines, breaking wherever the
    pen jumps. One drawing can hold several disjoint loops; chaining them all
    into a single ring would produce an outline enclosing neither room."""
    polygons: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for start, end in edges:
        if current and (
            abs(current[-1][0] - start[0]) > 0.01 or abs(current[-1][1] - start[1]) > 0.01
        ):
            if len(current) >= 4:
                polygons.append(current)
            current = [start, end]
        else:
            if not current:
                current.append(start)
            current.append(end)
    if len(current) >= 4:
        polygons.append(current)
    return polygons


def is_room_number(text: str) -> bool:
    """CMU room numbers: digits, optionally with a trailing letter, e.g. '1004', '1001A'."""
    stripped = text.strip()
    return len(stripped) >= 3 and stripped[:4].isdigit()


def build_room_nodes(base_pdf_path: str, building: str, floor: int) -> list[Node]:
    labels = extract_text_labels(base_pdf_path)
    return [
        Node(
            id=f"{building}-{floor}-{lbl.text}",
            building=building,
            floor=floor,
            x=round(lbl.x, 1),
            y=round(lbl.y, 1),
            type="room",
            label=lbl.text,
            room_type=None,
            department=None,
        )
        for lbl in labels
        if is_room_number(lbl.text)
    ]


LEADER_TICK_MAX_LENGTH = 30.0  # pt -- leader ticks observed on real data run ~17-19pt
LEADER_TICK_SEARCH_RADIUS = 20.0  # pt -- how close a tick's near end must be to the raw label
LEADER_LAYER = "A-SPAC-IDEN-LEDR"


def _leader_segments(raw_geometry: list[WallSegment]) -> list[tuple[int, WallSegment]]:
    """Return plausible diagonal leader ticks.

    CMU's exported PDFs identify these on a dedicated CAD layer. Prefer that
    source of truth when it exists; PDFs without layer metadata retain the
    geometry-only fallback used by the original extractor.
    """
    has_leader_layer = any(seg.layer == LEADER_LAYER for seg in raw_geometry)
    candidates: list[tuple[int, WallSegment]] = []
    for index, seg in enumerate(raw_geometry):
        if seg.type != "line" or len(seg.points) < 2:
            continue
        if has_leader_layer and seg.layer != LEADER_LAYER:
            continue
        p1, p2 = seg.points[0], seg.points[1]
        dx, dy = abs(p2[0] - p1[0]), abs(p2[1] - p1[1])
        length = (dx**2 + dy**2) ** 0.5
        if dx > 3 and dy > 3 and 10 <= length <= LEADER_TICK_MAX_LENGTH:
            candidates.append((index, seg))
    return candidates


def _leader_match(
    label_pos: tuple[float, float], segment: WallSegment
) -> Optional[tuple[float, tuple[float, float]]]:
    p1, p2 = segment.points[0], segment.points[1]
    d1 = ((p1[0] - label_pos[0]) ** 2 + (p1[1] - label_pos[1]) ** 2) ** 0.5
    d2 = ((p2[0] - label_pos[0]) ** 2 + (p2[1] - label_pos[1]) ** 2) ** 0.5
    near_d, far_point = (d1, p2) if d1 < d2 else (d2, p1)
    if near_d > LEADER_TICK_SEARCH_RADIUS:
        return None
    return near_d, far_point


def _leader_line_target(
    label_pos: tuple[float, float], raw_geometry: list[WallSegment]
) -> Optional[tuple[float, float]]:
    """CMU floor plans sometimes pull a small room's number label outside
    the room via a short diagonal leader line ending in a dot at the room's
    true location -- common for rooms too small to fit the label text
    inside (closets, storage, stairwells). Detected via geometry alone: a
    short (10-30pt), non-axis-aligned line segment near the label, distinct
    from the mostly-horizontal/vertical wall lines. If found, the label's
    true position is the FAR endpoint, not the label centroid itself.
    """
    best: Optional[tuple[float, tuple[float, float]]] = None
    for _index, seg in _leader_segments(raw_geometry):
        match = _leader_match(label_pos, seg)
        if match is not None and (best is None or match[0] < best[0]):
            best = match
    return best[1] if best else None


def apply_leader_line_corrections(
    room_nodes: list[Node], raw_geometry: list[WallSegment]
) -> list[Node]:
    """Confirmed against real Wean Hall Level 1 data: of 7 rooms flagged by
    this pattern, 6 were genuine leader-lined labels (correctly repositioned
    into their real room) and 1 was a coincidental nearby tick on an
    already-correctly-placed room -- harmless, since the "correction" is
    short enough to still land inside the same room."""
    segments = _leader_segments(raw_geometry)
    has_leader_layer = any(seg.layer == LEADER_LAYER for seg in raw_geometry)

    # A leader tick belongs to one room number. Greedy nearest matching stops
    # two nearby labels from both snapping onto the same black endpoint dot.
    if has_leader_layer:
        matches: list[tuple[float, int, int, tuple[float, float]]] = []
        for node_index, node in enumerate(room_nodes):
            for segment_index, seg in segments:
                match = _leader_match((node.x, node.y), seg)
                if match is not None:
                    matches.append((match[0], node_index, segment_index, match[1]))

        targets: dict[int, tuple[float, float]] = {}
        used_segments: set[int] = set()
        for _distance, node_index, segment_index, target in sorted(matches):
            if node_index in targets or segment_index in used_segments:
                continue
            targets[node_index] = target
            used_segments.add(segment_index)
    else:
        targets = {}
        for node_index, node in enumerate(room_nodes):
            target = _leader_line_target((node.x, node.y), raw_geometry)
            if target is not None:
                targets[node_index] = target

    return [
        node.model_copy(update={"x": round(targets[i][0], 1), "y": round(targets[i][1], 1)})
        if i in targets
        else node
        for i, node in enumerate(room_nodes)
    ]


def enrich_with_overlay(
    room_nodes: list[Node],
    overlay_pdf_path: str,
    field_name: str,
    match_radius: float = 20.0,
) -> list[Node]:
    """
    KNOWN LIMITATION (confirmed on real data): matching overlay text to rooms by
    nearest-centroid only succeeded for ~15% of rooms — multi-word labels like
    "Research/Nonclass Laboratory" sit off-center from the room number. Before
    relying on this, switch to point-in-polygon matching against each room's
    bounding rectangle (extractable from the vector geometry) instead of
    nearest-point-to-point distance. Until then, an unmatched field is left
    None (blank) rather than guessed — surfaced for a human to fill in via the
    graph editor rather than silently mismatched.
    """
    overlay_labels = extract_text_labels(overlay_pdf_path)
    candidate = [
        l
        for l in overlay_labels
        if len(l.text) > 3 and not l.text.replace(",", "").replace("sf", "").strip().isdigit()
    ]
    for node in room_nodes:
        nearest, best_d = None, None
        for l in candidate:
            d = (l.x - node.x) ** 2 + (l.y - node.y) ** 2
            if best_d is None or d < best_d:
                best_d, nearest = d, l
        if nearest and best_d is not None and best_d**0.5 <= match_radius:
            setattr(node, field_name, nearest.text)
    return room_nodes


SNAP_SEARCH_RADIUS = 60.0  # pt -- how far a leader line plausibly pulls a label


def snap_nodes_into_outlines(
    room_nodes: list[Node], polygons: list[list[tuple[float, float]]]
) -> list[Node]:
    """Pull a node that landed outside every room outline into its own room.

    Small spaces -- stairwells especially -- have their number set outside
    the room on a leader line, and the leader-line correction can still leave
    the node just outside the outline (or in the corridor). A node stranded
    there has no room to own a door, so it never connects. Each unplaced node
    takes the nearest outline that no other node already occupies, which is
    the room the leader was pointing at.
    """
    if not polygons:
        return room_nodes

    def centroid(polygon: list[tuple[float, float]]) -> tuple[float, float]:
        return (
            sum(p[0] for p in polygon) / len(polygon),
            sum(p[1] for p in polygon) / len(polygon),
        )

    from app.services.corridor_heuristic import _point_in_polygon

    taken: set[int] = set()
    stranded: list[Node] = []
    for node in room_nodes:
        index = next(
            (i for i, p in enumerate(polygons) if _point_in_polygon((node.x, node.y), p)), None
        )
        if index is None:
            stranded.append(node)
        else:
            taken.add(index)

    for node in stranded:
        candidates = [
            (((node.x - (c := centroid(p))[0]) ** 2 + (node.y - c[1]) ** 2) ** 0.5, i, c)
            for i, p in enumerate(polygons)
            if i not in taken
        ]
        if not candidates:
            continue
        distance, index, target = min(candidates)
        if distance <= SNAP_SEARCH_RADIUS:
            node.x, node.y = round(target[0], 1), round(target[1], 1)
            taken.add(index)
    return room_nodes


VERTICAL_MATCH_RADIUS = 40.0  # pt -- a category label sits within its own space
VERTICAL_CONFIRM_RADIUS = 40.0  # pt -- how close the drawn stair/lift must be
VERTICAL_LAYER_SUFFIXES = {"A-FLOR-STRS": "stair", "A-FLOR-EVTR": "elevator"}


def _has_layer_geometry_near(
    raw_geometry: list[WallSegment], layer_suffix: str, x: float, y: float, radius: float
) -> bool:
    radius_sq = radius**2
    for seg in raw_geometry:
        if seg.layer is None or seg.layer.split("|")[-1] != layer_suffix:
            continue
        if any((px - x) ** 2 + (py - y) ** 2 <= radius_sq for px, py in seg.points):
            return True
    return False


VERTICAL_CLUSTER_RADIUS = 45.0  # pt -- one stair's treads/rails spread about this far
MIN_VERTICAL_SEGMENTS = 6  # ignore a stray tread or arrow on the layer


def _layer_clusters(
    raw_geometry: list[WallSegment], layer_suffix: str
) -> list[tuple[float, float]]:
    points = [
        p
        for seg in raw_geometry
        if seg.layer and seg.layer.split("|")[-1] == layer_suffix
        for p in seg.points
    ]
    clusters: list[list[tuple[float, float]]] = []
    for point in points:
        for cluster in clusters:
            if (point[0] - cluster[0][0]) ** 2 + (point[1] - cluster[0][1]) ** 2 <= (
                VERTICAL_CLUSTER_RADIUS**2
            ):
                cluster.append(point)
                break
        else:
            clusters.append([point])
    return [
        (sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c))
        for c in clusters
        if len(c) >= MIN_VERTICAL_SEGMENTS
    ]


# Calibrated against floor 4, where the report names every stair and lift so
# the right answer is known. The lift layer is clean: 3 of 4 found with zero
# false positives at any threshold tried. The stair layer is not -- it also
# carries ramps and handrails, and at best managed 3 of 6 while inventing 7,
# which would put fake floor changes in the middle of ordinary rooms. So only
# lifts are taken from layers.
LAYER_FALLBACK_TYPES = {"elevator"}


def apply_vertical_circulation_from_layers(
    room_nodes: list[Node], raw_geometry: list[WallSegment]
) -> list[Node]:
    """Mark lift rooms from the base plan's own CAD layers.

    This is the primary source because it needs nothing but the base PDF and
    is language-independent. The space-type report cannot be relied on for
    it: floor 4's report prints "Stairway"/"Elevator" at each one, but floor
    5's prints neither -- it calls them "Shaft" -- so a text-driven rule
    found six stairs and four lifts on one floor and none at all on the next.
    """
    claimed: set[str] = set()
    for layer_suffix, node_type in VERTICAL_LAYER_SUFFIXES.items():
        if node_type not in LAYER_FALLBACK_TYPES:
            continue
        for x, y in _layer_clusters(raw_geometry, layer_suffix):
            candidates = [
                n
                for n in room_nodes
                if n.id not in claimed
                and n.type == "room"
                and (n.x - x) ** 2 + (n.y - y) ** 2 <= VERTICAL_CLUSTER_RADIUS**2
            ]
            if not candidates:
                continue
            nearest = min(candidates, key=lambda n: (n.x - x) ** 2 + (n.y - y) ** 2)
            nearest.type = node_type
            claimed.add(nearest.id)
    return room_nodes


def apply_vertical_circulation(
    room_nodes: list[Node],
    raw_geometry: list[WallSegment],
    base_pdf_path: str,
    type_pdf_path: str,
) -> list[Node]:
    """Retype the rooms that are stairwells or elevators.

    Routing needs these marked so a path can change floors through them. The
    space-type report names them in text at each space's own position, which
    (unlike its fill colors) is unambiguous -- see space_type_overlay.

    A label can still land nearer some unrelated room than its own, when the
    real stairwell is too small to carry an extracted room number (observed:
    a stair label claiming a study carrel 64pt away). So each match must be
    confirmed by the base plan's own stair/elevator CAD layer actually being
    drawn there; unconfirmed matches are left as ordinary rooms rather than
    inventing a floor change that doesn't exist.
    """
    from app.services import space_type_overlay

    found = space_type_overlay.vertical_circulation(base_pdf_path, type_pdf_path)
    if not found:
        return room_nodes
    claimed: set[str] = set()
    for node_type, positions in found.items():
        layer_suffix = next(k for k, v in VERTICAL_LAYER_SUFFIXES.items() if v == node_type)
        for x, y in positions:
            candidates = [
                node
                for node in room_nodes
                if node.id not in claimed
                and node.type == "room"
                and (node.x - x) ** 2 + (node.y - y) ** 2 <= VERTICAL_MATCH_RADIUS**2
            ]
            if not candidates:
                continue
            nearest = min(candidates, key=lambda n: (n.x - x) ** 2 + (n.y - y) ** 2)
            if not _has_layer_geometry_near(
                raw_geometry, layer_suffix, nearest.x, nearest.y, VERTICAL_CONFIRM_RADIUS
            ):
                continue
            nearest.type = node_type
            claimed.add(nearest.id)
    return room_nodes


def extract_floor_draft(
    base_pdf_path: str,
    building: str,
    floor: int,
    dept_pdf_path: Optional[str] = None,
    type_pdf_path: Optional[str] = None,
) -> FloorDraft:
    """Generic entry point — works for any uploaded floor, base file required, overlays optional."""
    room_nodes = build_room_nodes(base_pdf_path, building, floor)
    raw_geometry = extract_vector_geometry(base_pdf_path)
    room_nodes = apply_leader_line_corrections(room_nodes, raw_geometry)
    if dept_pdf_path:
        room_nodes = enrich_with_overlay(room_nodes, dept_pdf_path, "department")
    if type_pdf_path:
        room_nodes = enrich_with_overlay(room_nodes, type_pdf_path, "room_type")
        room_nodes = apply_vertical_circulation(
            room_nodes, raw_geometry, base_pdf_path, type_pdf_path
        )
    if not any(n.type in {"stair", "elevator"} for n in room_nodes):
        # The report named none of them -- floor 5's calls them "Shaft", not
        # "Stairway"/"Elevator" -- so fall back to the CAD layers, which is
        # lifts only for the accuracy reasons noted there.
        room_nodes = apply_vertical_circulation_from_layers(room_nodes, raw_geometry)
    polygons = extract_room_polygons(base_pdf_path)
    room_nodes = snap_nodes_into_outlines(room_nodes, polygons)
    draft = FloorDraft(
        building=building,
        floor=floor,
        nodes=room_nodes,
        raw_geometry=raw_geometry,
        room_polygons=polygons,
    )
    if type_pdf_path:
        # Imported here rather than at module scope: door detection reaches
        # back into this module through space_type_overlay.
        from app.services import space_doors

        draft.space_doors = space_doors.detect(draft, base_pdf_path, type_pdf_path)
    return draft
