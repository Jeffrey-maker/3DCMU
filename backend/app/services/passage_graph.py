"""Build room -> door -> projected passage -> door -> room routes.

Gemini supplies only public passage polylines. Geometry validates every segment,
splits crossings/T junctions and door projections into shared graph vertices,
and attaches each room/door once. No door-to-door spanning tree is used.
"""

import math
import heapq
from dataclasses import dataclass

from app.models.graph import (
    DoorPassageAttachment,
    Edge,
    FloorDraft,
    FloorGraph,
    Node,
    PassageLine,
)
from app.services import corridor_heuristic as heuristic, navgrid
from app.services.gemini_vision import PassageSuggestions
from app.services.geometry import Point, WallIndex, distance

Segment = tuple[Point, Point]
MIN_CONFIDENCE = 0.5
JUNCTION_TOLERANCE = 3.0  # PDF points, only joined with clear line of sight
LINE_REPAIR_MARGIN = 12.0
MAX_DOOR_ATTACHMENT = 80.0
DOOR_ON_BOUNDARY = 14.0  # pt -- how close a door sits to the wall it pierces
# Must stay below the spacing of real neighbouring doors (measured at ~14pt
# for adjacent offices on this floor), or two rooms' doors collapse into one.
DOOR_MERGE_RADIUS = 8.0  # pt


class PassageGraphError(ValueError):
    pass


def project(point: Point, a: Point, b: Point) -> tuple[float, Point]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq < 1e-12 else max(0.0, min(1.0, (
        (point[0] - a[0]) * dx + (point[1] - a[1]) * dy
    ) / length_sq))
    return t, (a[0] + t * dx, a[1] + t * dy)


def _intersection(a: Point, b: Point, c: Point, d: Point) -> Point | None:
    ab = (b[0] - a[0], b[1] - a[1])
    cd = (d[0] - c[0], d[1] - c[1])
    cross = ab[0] * cd[1] - ab[1] * cd[0]
    if abs(cross) < 1e-9:
        return None  # Collinear overlap is split by the endpoint projections.
    ac = (c[0] - a[0], c[1] - a[1])
    t = (ac[0] * cd[1] - ac[1] * cd[0]) / cross
    u = (ac[0] * ab[1] - ac[1] * ab[0]) / cross
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return (a[0] + t * ab[0], a[1] + t * ab[1])
    return None


def _distance_to_boundary(point: Point, polygon: list[Point]) -> float:
    return min(
        distance(point, project(point, a, b)[1])
        for a, b in zip(polygon, polygon[1:] + polygon[:1])
    )


def _door_ownership(
    rooms: list[Node], doors: list[Node], polygons: list[list[Point]]
) -> tuple[dict[str, int], dict[str, set[int]]]:
    """Work out which room each door actually opens into.

    Nearest-door-by-distance is wrong surprisingly often: a door belonging to
    the room across the wall can easily be the closest one to a given room's
    label, which is how a single door ended up claimed by three different
    rooms. A door pierces the boundary of the room it serves, so a door is
    owned by the room polygons whose edge it sits on -- geometry the CAD file
    already states, rather than a guess from proximity.
    """
    room_polygon: dict[str, int] = {}
    for room in rooms:
        for index, polygon in enumerate(polygons):
            if heuristic._point_in_polygon((room.x, room.y), polygon):
                room_polygon[room.id] = index
                break

    door_polygons: dict[str, set[int]] = {}
    for door in doors:
        point = (door.x, door.y)
        bordering = {
            index
            for index, polygon in enumerate(polygons)
            if _distance_to_boundary(point, polygon) <= DOOR_ON_BOUNDARY
        }
        if bordering:
            door_polygons[door.id] = bordering
    return room_polygon, door_polygons


@dataclass
class BuildResult:
    graph: FloorGraph
    report: dict


def build_passage_graph(
    draft: FloorDraft,
    existing: FloorGraph,
    suggestions: dict,
    page_width: float,
    page_height: float,
    coordinate_system: dict | None = None,
    routing_source: str = "gemini_pathways",
) -> BuildResult:
    review = PassageSuggestions.model_validate(suggestions)
    # Check passage lines against actual barriers. Never carve wide circles at
    # every door: that could erase a dividing wall between neighboring rooms.
    barrier_segments = heuristic.wall_segments(draft.raw_geometry)
    structural_segments = heuristic.structural_wall_segments(draft.raw_geometry)
    barriers = WallIndex(barrier_segments)
    exterior = heuristic.exterior_space_polygon(draft.raw_geometry)
    warnings = list(review.warnings)

    def inside(p: Point) -> bool:
        return (
            0 <= p[0] <= page_width and 0 <= p[1] <= page_height
            and (exterior is None or heuristic._point_in_polygon(p, exterior))
        )

    def segment_inside(a: Point, b: Point) -> bool:
        steps = max(1, math.ceil(distance(a, b) / 2.0))
        return all(inside((a[0] + (b[0] - a[0]) * t / steps,
                           a[1] + (b[1] - a[1]) * t / steps)) for t in range(steps + 1))

    def safe_path(a: Point, b: Point, walls: WallIndex, limit: float, margin: float) -> list[Point] | None:
        if distance(a, b) > limit or not inside(a) or not inside(b):
            return None
        if not walls.crosses_wall(a, b) and segment_inside(a, b):
            return [a, b]
        # A narrow local search can bend around a column or jamb, but cannot
        # fabricate a replacement corridor across a different wing or outdoors.
        path = navgrid.find_simplified_path(
            a, b, walls,
            allowed_point=lambda p: inside(p) and distance(p, project(p, a, b)[1]) <= margin,
        )
        if path is None or navgrid.path_length(path) > limit:
            return None
        if any(walls.crosses_wall(p, q) or not segment_inside(p, q) for p, q in zip(path, path[1:])):
            return None
        return path

    bounds = (coordinate_system or {}).get("pdf_bounds", [0, 0, page_width, page_height])
    left, top, right, bottom = (float(value) for value in bounds)

    def from_image(p) -> Point:
        return (
            left + p.x_normalized * (right - left) / 1000,
            top + p.y_normalized * (bottom - top) / 1000,
        )

    segments: list[Segment] = []
    rejected = repaired = 0
    for pathway in review.pathways:
        if pathway.confidence < MIN_CONFIDENCE:
            rejected += max(1, len(pathway.points) - 1)
            continue
        points = [from_image(p) for p in pathway.points]
        for a, b in zip(points, points[1:]):
            if distance(a, b) < 0.01:
                continue
            path = safe_path(a, b, barriers, distance(a, b) * 1.5 + 12, LINE_REPAIR_MARGIN)
            if path is None:
                rejected += 1
                continue
            repaired += int(len(path) > 2)
            segments.extend(zip(path, path[1:]))
    if not segments:
        raise PassageGraphError("Gemini returned no usable passage lines. Adjust the lines or trace again.")

    # Connect near-miss T/end junctions only with short, barrier-checked links.
    # Adding a link retains the original geometry instead of moving a point.
    joins: list[Segment] = []
    for a, b in list(segments):
        for p in (a, b):
            for c, d in segments:
                _, q = project(p, c, d)
                gap = distance(p, q)
                if 1e-6 < gap <= JUNCTION_TOLERANCE and not barriers.crosses_wall(p, q) and segment_inside(p, q):
                    joins.append((p, q))
    segments.extend(joins)
    # Remove exact duplicates before the O(n^2) intersection/splitting pass.
    segments = list(dict.fromkeys(tuple(sorted((a, b))) for a, b in segments))
    cuts: list[list[Point]] = [[a, b] for a, b in segments]
    for i, (a, b) in enumerate(segments):
        for j in range(i + 1, len(segments)):
            c, d = segments[j]
            intersection = _intersection(a, b, c, d)
            if intersection is not None:
                cuts[i].append(intersection)
                cuts[j].append(intersection)
            # Handles exact T junctions and overlapping collinear lines.
            for p in (a, b):
                if distance(p, project(p, c, d)[1]) < 1e-6:
                    cuts[j].append(p)
            for p in (c, d):
                if distance(p, project(p, a, b)[1]) < 1e-6:
                    cuts[i].append(p)

    anchors = [n.model_copy() for n in existing.nodes if n.type != "corridor"]
    if not anchors:
        anchors = [n.model_copy() for n in draft.nodes]
    doors = [n for n in anchors if n.type == "door"]
    door_owner: dict[str, set[str]] = {}
    if not doors:
        arcs = heuristic._cluster_door_points(draft.raw_geometry)

        def place(point: Point, room_id: str | None, prefix: str) -> None:
            """Add a door, or fold this position into the one already there.

            A drawn swing arc and the opening detected in the same wall are
            the same physical door a few points apart, so adding both leaves
            a visible cluster of duplicate nodes on every room. Merging
            within less than the spacing of genuinely separate neighbouring
            doors keeps one node per doorway while still letting two rooms
            that share a wall each keep their own.
            """
            nearest = min(doors, key=lambda d: distance((d.x, d.y), point), default=None)
            if nearest is not None and distance((nearest.x, nearest.y), point) <= DOOR_MERGE_RADIUS:
                if room_id is not None:
                    door_owner.setdefault(nearest.id, set()).add(room_id)
                return
            node = Node(
                id=f"{draft.building}-{draft.floor}-{prefix}-{len(doors)}",
                building=draft.building, floor=draft.floor, type="door",
                x=point[0], y=point[1],
            )
            doors.append(node)
            if room_id is not None:
                door_owner.setdefault(node.id, set()).add(room_id)

        # Detected doorways go in first: unlike the arcs they carry the
        # identity of the space they serve, and they exist for spaces the
        # arcs miss completely (an elevator shaft has no swing to detect).
        for room_id, doorways in draft.space_doors.items():
            for point in doorways:
                place(point, room_id, "doorway")
        for point in arcs:
            place(point, None, "door")
        anchors.extend(doors)
    else:
        for room_id, doorways in draft.space_doors.items():
            for point in doorways:
                nearest = min(doors, key=lambda d: distance((d.x, d.y), point), default=None)
                if nearest is not None and distance((nearest.x, nearest.y), point) <= DOOR_MERGE_RADIUS:
                    door_owner.setdefault(nearest.id, set()).add(room_id)
    # Each room's search may only pass through ITS OWN candidate door's
    # opening, never a different door's. A wall index that cut every door
    # open at once (the previous approach) let a room "leak" a straight
    # line through a neighboring room's doorway -- e.g. a small room could
    # attach to a door two rooms away because that door's own opening,
    # carved for a different room entirely, happened to sit on the line of
    # sight. One index per door, built once and reused across every room
    # that considers that door, closes the leak while staying cheap.
    per_door_walls = {
        door.id: WallIndex(heuristic._cut_door_openings(structural_segments, [(door.x, door.y)]))
        for door in doors
    }

    # Each door gets exactly one link to the nearest reachable point ON a line.
    # Projection cuts the line in the middle, so it is a real Dijkstra junction.
    door_paths: dict[str, list[Point]] = {}
    for door in doors:
        p = (door.x, door.y)
        door_barriers = WallIndex(heuristic._cut_door_openings(barrier_segments, [p]))
        candidates = sorted((distance(p, q), i, q) for i, (a, b) in enumerate(segments)
                            for q in [project(p, a, b)[1]])
        best: tuple[float, int, list[Point]] | None = None
        for direct, i, q in candidates:
            if direct > MAX_DOOR_ATTACHMENT or (best is not None and direct >= best[0]):
                break
            path = safe_path(
                p, q, door_barriers, min(MAX_DOOR_ATTACHMENT, direct * 2.5 + 12), 18
            )
            if path is not None:
                length = navgrid.path_length(path)
                if best is None or length < best[0]:
                    best = (length, i, path)
        if best is not None:
            _, i, path = best
            cuts[i].append(path[-1])
            door_paths[door.id] = path

    nodes = list(anchors)
    edges: list[Edge] = []
    vertices: dict[tuple[float, float], Node] = {}
    edge_keys: set[tuple[str, str]] = set()

    def vertex(p: Point, shared: bool = True) -> Node:
        key = (round(p[0], 6), round(p[1], 6))
        if shared and key in vertices:
            return vertices[key]
        node = Node(id=f"{draft.building}-{draft.floor}-passage-{len(nodes)}",
                    building=draft.building, floor=draft.floor, x=p[0], y=p[1], type="corridor")
        nodes.append(node)
        if shared:
            vertices[key] = node
        return node

    def edge(a: Node, b: Node, kind: str) -> None:
        key = tuple(sorted((a.id, b.id)))
        if a.id == b.id or key in edge_keys:
            return
        edge_keys.add(key)
        edges.append(Edge(id=f"passage-edge-{len(edges)}", from_node=a.id, to_node=b.id,
                          weight=distance((a.x, a.y), (b.x, b.y)), type=kind))

    def chain(start: Node, end: Node, path: list[Point], kind: str) -> None:
        previous = start
        for p in path[1:-1]:
            # A room/door connector must not become a public passage shortcut.
            current = vertex(p, shared=False)
            edge(previous, current, kind)
            previous = current
        edge(previous, end, kind)

    for (a, b), points in zip(segments, cuts):
        ordered = sorted(points, key=lambda p: project(p, a, b)[0])
        for p, q in zip(ordered, ordered[1:]):
            if distance(p, q) > 1e-6:
                edge(vertex(p), vertex(q), "hallway")
    for door in doors:
        if door.id in door_paths:
            path = door_paths[door.id]
            chain(door, vertex(path[-1]), path, "door")

    connected_rooms: list[str] = []
    unconnected_rooms: list[str] = []
    room_connections: list[tuple[Node, Node, list[Point]]] = []
    usable_doors = [door for door in doors if door.id in door_paths]
    room_anchors = [n for n in anchors if n.type not in {"door", "outdoor"}]
    room_polygon, door_polygons = _door_ownership(room_anchors, usable_doors, draft.room_polygons)
    for room in room_anchors:
        p = (room.x, room.y)
        # Restrict to doors on this room's own boundary BEFORE ranking by
        # distance: a room's real door is not always among its eight nearest,
        # so filtering after the cut would discard it. Falls back to plain
        # proximity where the CAD file has no polygon for the room (or none
        # of its own doors reached the passage network), so floors without an
        # A-AREA layer keep working exactly as before.
        # Strongest signal first: a doorway detected on this space's own
        # outline belongs to it by construction. Only fall back to inferring
        # ownership from polygon boundaries, and finally to bare proximity,
        # for spaces that detection couldn't resolve.
        own = [d for d in usable_doors if room.id in door_owner.get(d.id, ())]
        if not own:
            own_polygon = room_polygon.get(room.id)
            own = (
                [d for d in usable_doors if own_polygon in door_polygons.get(d.id, ())]
                if own_polygon is not None
                else []
            )
        pool = own or usable_doors
        candidates = sorted(pool, key=lambda d: distance(p, (d.x, d.y)))[:8]
        best = None
        for door in candidates:
            q = (door.x, door.y)
            direct = distance(p, q)
            if best is not None and direct >= best[0]:
                break
            path = safe_path(p, q, per_door_walls[door.id], min(100, direct * 2.2 + 6), 18)
            if path is not None:
                length = navgrid.path_length(path)
                if best is None or length < best[0]:
                    best = (length, door, path)
        if best is not None:
            _, door, path = best
            chain(room, door, path, "door")
            room_connections.append((room, door, path))
            if room.type == "room":
                connected_rooms.append(room.id)
        elif room.type == "room":
            unconnected_rooms.append(room.id)

    # Count independent public components; never join them through a room.
    public_ids = {n.id for n in vertices.values()}
    adjacency = {node_id: set() for node_id in public_ids}
    for e in edges:
        if e.type == "hallway":
            adjacency[e.from_node].add(e.to_node)
            adjacency[e.to_node].add(e.from_node)
    remaining = set(public_ids)
    components = 0
    while remaining:
        components += 1
        pending = [remaining.pop()]
        while pending:
            for neighbor in adjacency[pending.pop()]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)

    if rejected:
        warnings.append(f"Omitted {rejected} passage segments with low confidence or blocked/outside geometry.")
    if unconnected_rooms:
        labels = [n.label or n.id for n in anchors if n.id in unconnected_rooms]
        warnings.append("No verified door-to-passage connection for: " + ", ".join(labels) + ".")
    if components > 1:
        warnings.append(f"The passage lines form {components} separate networks; routes between them are unavailable.")
    by_id = {node.id: node for node in nodes}
    passageways = [
        PassageLine(
            id=edge.id,
            points=[
                (by_id[edge.from_node].x, by_id[edge.from_node].y),
                (by_id[edge.to_node].x, by_id[edge.to_node].y),
            ],
        )
        for edge in edges
        if edge.type == "hallway"
    ]
    attachments = [
        DoorPassageAttachment(
            door_node_id=door.id,
            pathway_point=path[-1],
            points=path,
        )
        for door in doors
        if (path := door_paths.get(door.id)) is not None
    ]
    # Persist only room and door anchors. Passage vertices are reconstructed
    # in memory during routing, so there are no orange nodes in stored data.
    room_edges = [
        Edge(
            id=f"room-door-{room.id}-{door.id}",
            from_node=room.id,
            to_node=door.id,
            weight=navgrid.path_length(path),
            type="door",
            points=path,
        )
        for room, door, path in room_connections
    ]
    graph = FloorGraph(
        building=draft.building, floor=draft.floor, nodes=anchors, edges=room_edges,
        auto_generated=True, routing_source=routing_source, page_width=page_width,
        page_height=page_height, routing_warnings=warnings, unconnected_room_ids=unconnected_rooms,
        passageways=passageways, door_attachments=attachments,
    )
    return BuildResult(graph, {
        "pathway_count": len(review.pathways), "passage_segment_count": sum(e.type == "hallway" for e in edges),
        "connected_room_count": len(connected_rooms), "unconnected_room_count": len(unconnected_rooms),
        "connected_door_count": len(door_paths), "rejected_segment_count": rejected,
        "repaired_segment_count": repaired, "network_count": components, "warnings": warnings,
    })


# Graphs that store passages as polylines rather than as persisted corridor
# nodes, and so need those vertices rebuilt in memory before Dijkstra runs.
PASSAGE_ROUTING_SOURCES = {
    "gemini_pathways",
    "space_type_centerlines",
    "classified_spaces",
}


def materialize_for_routing(graph: FloorGraph) -> FloorGraph:
    """Recreate invisible passage vertices for Dijkstra without persisting them."""
    if graph.routing_source not in PASSAGE_ROUTING_SOURCES:
        return graph
    nodes = [node.model_copy() for node in graph.nodes]
    edges: list[Edge] = []
    shared: dict[tuple[float, float], Node] = {}
    counter = 0

    def point_node(point: Point, *, reuse: bool) -> Node:
        nonlocal counter
        key = (round(point[0], 6), round(point[1], 6))
        if reuse and key in shared:
            return shared[key]
        counter += 1
        node = Node(
            id=f"{graph.building}-{graph.floor}-route-point-{counter}",
            building=graph.building, floor=graph.floor,
            x=point[0], y=point[1], type="corridor",
        )
        nodes.append(node)
        if reuse:
            shared[key] = node
        return node

    def add_chain(start: Node, end: Node, points: list[Point], kind: str, reuse_middle: bool) -> None:
        previous = start
        for point in points[1:-1]:
            current = point_node(point, reuse=reuse_middle)
            edges.append(Edge(
                id=f"route-edge-{len(edges)}", from_node=previous.id, to_node=current.id,
                weight=distance((previous.x, previous.y), point), type=kind,
            ))
            previous = current
        edges.append(Edge(
            id=f"route-edge-{len(edges)}", from_node=previous.id, to_node=end.id,
            weight=distance((previous.x, previous.y), (end.x, end.y)), type=kind,
        ))

    for line in graph.passageways:
        for a, b in zip(line.points, line.points[1:]):
            add_chain(point_node(a, reuse=True), point_node(b, reuse=True), [a, b], "hallway", True)
    anchors = {node.id: node for node in nodes if node.type != "corridor"}
    for attachment in graph.door_attachments:
        door = anchors.get(attachment.door_node_id)
        if door is None:
            continue
        endpoint = point_node(attachment.pathway_point, reuse=True)
        add_chain(door, endpoint, attachment.points, "door", False)
    for stored_edge in graph.edges:
        start, end = anchors.get(stored_edge.from_node), anchors.get(stored_edge.to_node)
        if start is None or end is None:
            continue
        points = stored_edge.points or [(start.x, start.y), (end.x, end.y)]
        add_chain(start, end, points, stored_edge.type, False)
    return graph.model_copy(update={"nodes": nodes, "edges": edges})


def strip_legacy_connections(draft: FloorDraft, graph: FloorGraph) -> FloorGraph:
    """Remove persisted orange nodes and door-to-door hallway connections.

    Existing room-to-door chains are collapsed into polylines stored on one
    room/door edge. This preserves the room's selected doorway without keeping
    any corridor node or passage guess from the legacy generator.
    """
    anchors = [node.model_copy() for node in graph.nodes if node.type != "corridor"]
    anchor_by_id = {node.id: node for node in anchors}
    rooms = [node for node in anchors if node.type == "room"]
    door_ids = {node.id for node in anchors if node.type == "door"}
    by_id = {node.id: node for node in graph.nodes}
    adjacency: dict[str, list[tuple[str, Edge]]] = {}
    for edge in graph.edges:
        if edge.type != "door":
            continue
        adjacency.setdefault(edge.from_node, []).append((edge.to_node, edge))
        adjacency.setdefault(edge.to_node, []).append((edge.from_node, edge))

    collapsed: list[Edge] = []
    unconnected: list[str] = []
    for room in rooms:
        queue = [(0.0, room.id)]
        previous: dict[str, str] = {}
        best = {room.id: 0.0}
        found: str | None = None
        while queue:
            cost, node_id = heapq.heappop(queue)
            if cost != best.get(node_id):
                continue
            if node_id in door_ids:
                found = node_id
                break
            for neighbor, edge in adjacency.get(node_id, []):
                candidate = cost + edge.weight
                if candidate < best.get(neighbor, float("inf")):
                    best[neighbor] = candidate
                    previous[neighbor] = node_id
                    heapq.heappush(queue, (candidate, neighbor))
        if found is None:
            unconnected.append(room.id)
            continue
        ids = [found]
        while ids[-1] != room.id:
            ids.append(previous[ids[-1]])
        ids.reverse()
        points = [(by_id[node_id].x, by_id[node_id].y) for node_id in ids]
        collapsed.append(Edge(
            id=f"room-door-{room.id}-{found}", from_node=room.id, to_node=found,
            weight=navgrid.path_length(points), type="door", points=points,
        ))
    warnings = []
    if unconnected:
        labels = [anchor_by_id[node_id].label or node_id for node_id in unconnected]
        warnings.append("No detected room door for: " + ", ".join(labels) + ".")
    return FloorGraph(
        building=graph.building, floor=graph.floor, nodes=anchors, edges=collapsed,
        auto_generated=True, routing_source="anchors_only",
        page_width=graph.page_width, page_height=graph.page_height,
        routing_warnings=warnings, unconnected_room_ids=unconnected,
    )
