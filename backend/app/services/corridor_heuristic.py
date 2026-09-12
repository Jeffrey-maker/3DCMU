"""Automatic best-effort corridor connectivity, so routing works immediately
after uploading just the base PDF -- no manual graph editing required.

Two complementary techniques, in order of how much they're trusted:

1. **CAD layers, when the PDF preserves them** (true for this CMU ESIM
   export, via PDF Optional Content Groups): "ARCH|A-WALL" identifies real
   structural walls and "ARCH|A-DOOR" identifies real doors, directly from
   the source data -- not a geometric guess. This is authoritative ground
   truth, dramatically more reliable than inferring "wall vs. furniture vs.
   annotation" from line length or position (the raw vector geometry
   contains ~3800 segments; only ~1000 of them are real walls, the rest is
   furniture, fixtures, hatching, and text annotation that happens to be
   drawn as vector lines too).
2. **A grid-based ("chessboard") pathfinder** (see navgrid.py) over those
   real walls: the floor is treated as a fine grid of cells, two adjacent
   cells connected unless a wall sits between them, and the true shortest
   walkable path between any two points is found via A*. This is a much
   stronger guarantee against wall-crossing than validating a handful of
   long straight-line candidates: every step of the path is checked, not
   just each candidate's two endpoints, so the result can never cut through
   a wall and naturally bends around corners.

Falls back gracefully when a PDF doesn't preserve CAD layers (treats every
line as a possible barrier and every curve as a possible door). It never
fabricates a straight connection when the grid cannot find a walkable path:
an unresolved room is surfaced for human review instead of drawing an edge
through a wall.

The overall shape:
1. Each room connects to its nearest grid-reachable detected door.
2. All doors are joined into one connected graph via a minimum spanning
   tree (MST) over Euclidean distance, preferring pairs whose straight line
   doesn't cross a wall (a cheap pre-filter -- see `_wall_aware_mst_pairs`).
3. Each selected connection (room-to-door and door-to-door) is materialized
   using the real grid-computed path between its endpoints, not a straight
   line -- see `_materialize_edge`.

Still a heuristic, not a guarantee: mis-detected doors or floor plans this
generous CAD-layer data isn't available for will fall back to earlier,
cruder approximations. The graph editor remains available to fix any
specific connection that looks wrong.
"""

from app.models.graph import Edge, FloorDraft, FloorGraph, Node, WallSegment
from app.services import navgrid
from app.services.geometry import Point, WallIndex, distance

DOOR_CLUSTER_RADIUS = 15.0  # pt -- merges multi-segment door-arc drawings into one door point
WALL_IGNORE_RADIUS = 20.0  # pt -- radius carved from wall geometry at a detected door

REAL_WALL_LAYER = "ARCH|A-WALL"
REAL_DOOR_LAYER = "ARCH|A-DOOR"
EXTERIOR_SPACE_LAYER = "A-SPAC-EXTR"
ROOM_DOOR_CANDIDATE_COUNT = 8
ROOM_DOOR_MAX_WALK_DISTANCE = 100.0
ROOM_DOOR_MAX_DETOUR_RATIO = 2.2
OUTSIDE_BUILDING_COST_MULTIPLIER = 25.0

# Public corridors and lobbies remain walkable, but their physical obstacles
# do not. Movable furniture is included for passage routing so a highlighted
# route bends around tables/desks instead of crossing them. Room-to-door
# attachment uses structural walls only so furniture inside a room cannot
# trap the room anchor.
PASSAGE_BARRIER_LAYER_SUFFIXES = {
    "A-WALL",
    "S-COLS",
    "A-FURN",
    "P-FIXT",
    "A-FLOR-MLWK",
    "A-EQPM",
    "I-EQPM-FIXD",
}


def _curve_centroid(segment: WallSegment) -> Point:
    xs = [p[0] for p in segment.points]
    ys = [p[1] for p in segment.points]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _has_layer_info(raw_geometry: list[WallSegment]) -> bool:
    """Whether this PDF preserves CAD layer names at all. Deciding this
    separately from "is the layer-filtered result non-empty" matters: a
    floor with real layer data but zero segments on ARCH|A-WALL (or
    ARCH|A-DOOR) should trust that -- not silently fall back to treating
    every line as a wall, which would defeat the whole point."""
    return any(seg.layer for seg in raw_geometry)


def _cluster_door_points(raw_geometry: list[WallSegment]) -> list[Point]:
    """Greedy single-pass clustering of door-arc curve centroids into
    distinct physical doors -- a single visual door swing is often drawn as
    more than one vector primitive (the arc plus a door-leaf line), which
    would otherwise be double-counted as separate doors. Prefers the real
    "ARCH|A-DOOR" CAD layer when present (excludes stair treads, plumbing
    fixtures, and other curves that share no layer distinction otherwise);
    falls back to every curve when the PDF doesn't preserve layer info."""
    if _has_layer_info(raw_geometry):
        curves = [seg for seg in raw_geometry if seg.type == "curve" and seg.layer == REAL_DOOR_LAYER]
    else:
        curves = [seg for seg in raw_geometry if seg.type == "curve"]

    clusters: list[list[Point]] = []
    for segment in curves:
        point = _curve_centroid(segment)
        for cluster in clusters:
            if distance(point, cluster[-1]) <= DOOR_CLUSTER_RADIUS:
                cluster.append(point)
                break
        else:
            clusters.append([point])
    return [
        (sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c)) for c in clusters
    ]


def structural_wall_segments(raw_geometry: list[WallSegment]) -> list[tuple[Point, Point]]:
    """Structural walls used to decide which door belongs to a room."""
    if _has_layer_info(raw_geometry):
        walls = [seg for seg in raw_geometry if seg.type == "line" and seg.layer == REAL_WALL_LAYER]
    else:
        walls = [seg for seg in raw_geometry if seg.type == "line"]
    return [(seg.points[0], seg.points[1]) for seg in walls if len(seg.points) >= 2]


def wall_segments(raw_geometry: list[WallSegment]) -> list[tuple[Point, Point]]:
    """Navigation barriers for hallways and public/open spaces.

    CAD annotation, room-number, area, and glazing layers are deliberately
    excluded. Structural walls, columns, furniture, plumbing fixtures,
    millwork, and fixed equipment are treated as physical obstacles.
    """
    if _has_layer_info(raw_geometry):
        barriers = [
            seg
            for seg in raw_geometry
            if seg.type == "line"
            and seg.layer is not None
            and seg.layer.split("|")[-1] in PASSAGE_BARRIER_LAYER_SUFFIXES
        ]
    else:
        barriers = [seg for seg in raw_geometry if seg.type == "line"]
    return [(seg.points[0], seg.points[1]) for seg in barriers if len(seg.points) >= 2]


def exterior_space_polygon(raw_geometry: list[WallSegment]) -> list[Point] | None:
    """Reconstruct the closed exterior-space CAD boundary as one polygon."""
    if not _has_layer_info(raw_geometry):
        return None
    boundaries = [
        seg
        for seg in raw_geometry
        if seg.type == "line" and seg.layer == EXTERIOR_SPACE_LAYER
    ]
    adjacency: dict[Point, list[Point]] = {}
    for seg in boundaries:
        if len(seg.points) < 2:
            continue
        a = (round(seg.points[0][0], 3), round(seg.points[0][1], 3))
        b = (round(seg.points[1][0], 3), round(seg.points[1][1], 3))
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    if not adjacency or any(len(neighbors) != 2 for neighbors in adjacency.values()):
        return None

    start = next(iter(adjacency))
    polygon = [start]
    previous: Point | None = None
    current = start
    while True:
        neighbors = adjacency[current]
        following = neighbors[0] if neighbors[0] != previous else neighbors[1]
        if following == start:
            break
        if following in polygon:
            return None
        polygon.append(following)
        previous, current = current, following
    return polygon if len(polygon) == len(adjacency) else None


def _point_in_polygon(point: Point, polygon: list[Point]) -> bool:
    x, y = point
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a[1] > y) == (b[1] > y):
            continue
        crossing_x = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
        if x < crossing_x:
            inside = not inside
    return inside


def _cut_door_openings(
    segments: list[tuple[Point, Point]], door_points: list[Point]
) -> list[tuple[Point, Point]]:
    """Remove a small interval from barrier lines at every detected door.

    CAD exports commonly draw the structural wall as a continuous line and
    draw the door swing on a separate layer. Without explicitly carving the
    opening, a correct room anchor appears sealed inside its room to the grid
    pathfinder. Splitting, rather than dropping, the wall keeps the remainder
    of long wall segments fully blocking.
    """
    opened: list[tuple[Point, Point]] = []
    radius_sq = WALL_IGNORE_RADIUS**2
    for start, end in segments:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_sq = dx**2 + dy**2
        if length_sq < 1e-9:
            continue
        length = length_sq**0.5
        intervals: list[tuple[float, float]] = []
        for door in door_points:
            t = ((door[0] - start[0]) * dx + (door[1] - start[1]) * dy) / length_sq
            if t < 0.0 or t > 1.0:
                continue
            closest = (start[0] + t * dx, start[1] + t * dy)
            perpendicular_sq = (door[0] - closest[0]) ** 2 + (door[1] - closest[1]) ** 2
            if perpendicular_sq >= radius_sq:
                continue
            half_t = (radius_sq - perpendicular_sq) ** 0.5 / length
            intervals.append((max(0.0, t - half_t), min(1.0, t + half_t)))

        if not intervals:
            opened.append((start, end))
            continue

        merged: list[list[float]] = []
        for lo, hi in sorted(intervals):
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])

        cursor = 0.0
        for lo, hi in merged:
            if (lo - cursor) * length > 0.5:
                opened.append(
                    (
                        (start[0] + cursor * dx, start[1] + cursor * dy),
                        (start[0] + lo * dx, start[1] + lo * dy),
                    )
                )
            cursor = max(cursor, hi)
        if (1.0 - cursor) * length > 0.5:
            opened.append(
                (
                    (start[0] + cursor * dx, start[1] + cursor * dy),
                    end,
                )
            )
    return opened


def _nearest_reachable_door(
    room: Node, doors: list[Node], structural_walls: WallIndex
) -> tuple[Node, list[Point]] | None:
    """Choose by actual walking distance, not distance through a wall.

    Limiting grid searches to the nearest Euclidean candidates keeps large
    floors tractable while still covering the doors plausibly belonging to
    this room.
    """
    candidates = sorted(
        doors,
        key=lambda door: distance((room.x, room.y), (door.x, door.y)),
    )[:ROOM_DOOR_CANDIDATE_COUNT]
    best: tuple[float, Node, list[Point]] | None = None
    for door in candidates:
        path = navgrid.find_simplified_path(
            (room.x, room.y), (door.x, door.y), structural_walls
        )
        if path is None:
            continue
        walk_distance = navgrid.path_length(path)
        if best is None or walk_distance < best[0]:
            best = (walk_distance, door, path)
    if best is None:
        return None
    direct_distance = distance((room.x, room.y), (best[1].x, best[1].y))
    if (
        best[0] > ROOM_DOOR_MAX_WALK_DISTANCE
        or best[0] > max(direct_distance, 1.0) * ROOM_DOOR_MAX_DETOUR_RATIO
    ):
        return None
    return best[1], best[2]


class _UnionFind:
    def __init__(self, ids: list[str]):
        self.parent = {i: i for i in ids}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[ra] = rb
        return True


def _mst_pairs(nodes: list[Node]) -> list[tuple[Node, Node]]:
    """Plain Prim's, O(V^2) -- used when there's no wall geometry to check
    against (see the no-doors-detected fallback below)."""
    if len(nodes) < 2:
        return []
    by_id = {n.id: n for n in nodes}
    in_tree = {nodes[0].id}
    remaining = {n.id for n in nodes[1:]}
    pairs: list[tuple[Node, Node]] = []
    while remaining:
        best: tuple[float, str, str] | None = None
        for a_id in in_tree:
            a = by_id[a_id]
            for b_id in remaining:
                b = by_id[b_id]
                d = distance((a.x, a.y), (b.x, b.y))
                if best is None or d < best[0]:
                    best = (d, a_id, b_id)
        assert best is not None
        _, a_id, b_id = best
        pairs.append((by_id[a_id], by_id[b_id]))
        in_tree.add(b_id)
        remaining.discard(b_id)
    return pairs


def _wall_aware_mst_pairs(nodes: list[Node], wall_index: WallIndex) -> list[tuple[Node, Node]]:
    """Kruskal's MST that strictly prefers pairs whose straight line doesn't
    cross a wall -- a cheap pre-filter to pick a sensible spanning
    structure. Falls back to a wall-crossing pair only when no
    wall-respecting pair exists to keep every node in one connected
    component. The actual path materialized for each selected pair (see
    `_materialize_edge`) is the real grid-computed route, not this straight
    line -- this step only decides WHICH doors should be connected."""
    if len(nodes) < 2:
        return []
    by_id = {n.id: n for n in nodes}
    ids = list(by_id.keys())

    candidates: list[tuple[float, bool, str, str]] = []
    for i in range(len(ids)):
        a = by_id[ids[i]]
        pa = (a.x, a.y)
        for j in range(i + 1, len(ids)):
            b = by_id[ids[j]]
            pb = (b.x, b.y)
            valid = not wall_index.crosses_wall(pa, pb, ignore_radius=WALL_IGNORE_RADIUS)
            candidates.append((distance(pa, pb), valid, ids[i], ids[j]))

    candidates.sort(key=lambda t: (not t[1], t[0]))

    uf = _UnionFind(ids)
    pairs: list[tuple[Node, Node]] = []
    remaining_components = len(ids)
    for _dist, _valid, a_id, b_id in candidates:
        if remaining_components == 1:
            break
        if uf.union(a_id, b_id):
            pairs.append((by_id[a_id], by_id[b_id]))
            remaining_components -= 1
    return pairs


def _materialize_edge(
    from_node: Node,
    to_node: Node,
    edge_type: str,
    wall_index: WallIndex,
    building: str,
    floor: int,
    waypoint_counter: list[int],
    known_path: list[Point] | None = None,
    cost_multiplier=None,
) -> tuple[list[Node], list[Edge]]:
    """The real payoff of the grid pathfinder: replace a direct edge with
    the true wall-respecting path between its endpoints, materialized as a
    chain of corridor waypoint nodes. Falls back to a direct edge if the
    grid search can't find a path (a real CAD quirk -- e.g. a double-line
    wall representation the grid reads as solid, or a mis-assigned nearest
    door) -- full connectivity is a hard requirement, and a straight edge
    beats leaving two rooms unreachable from each other."""
    start, end = (from_node.x, from_node.y), (to_node.x, to_node.y)
    path = known_path or navgrid.find_simplified_path(
        start, end, wall_index, cost_multiplier=cost_multiplier
    )

    if path is None:
        return [], []

    if len(path) <= 2:
        weight = distance(start, end)
        edge = Edge(
            id=f"auto-{edge_type}-{from_node.id}-{to_node.id}",
            from_node=from_node.id,
            to_node=to_node.id,
            weight=round(weight, 2),
            type=edge_type,
        )
        return [], [edge]

    new_nodes: list[Node] = []
    new_edges: list[Edge] = []
    prev_id, prev_point = from_node.id, start
    for point in path[1:-1]:
        waypoint_counter[0] += 1
        waypoint = Node(
            id=f"{building}-{floor}-wp-{waypoint_counter[0]}",
            building=building,
            floor=floor,
            x=round(point[0], 1),
            y=round(point[1], 1),
            type="corridor",
            label=None,
            room_type=None,
            department=None,
        )
        new_nodes.append(waypoint)
        new_edges.append(
            Edge(
                id=f"auto-{edge_type}-{prev_id}-{waypoint.id}",
                from_node=prev_id,
                to_node=waypoint.id,
                weight=round(distance(prev_point, point), 2),
                type=edge_type,
            )
        )
        prev_id, prev_point = waypoint.id, point

    new_edges.append(
        Edge(
            id=f"auto-{edge_type}-{prev_id}-{to_node.id}",
            from_node=prev_id,
            to_node=to_node.id,
            weight=round(distance(prev_point, end), 2),
            type=edge_type,
        )
    )
    return new_nodes, new_edges


def generate_auto_graph(draft: FloorDraft) -> FloorGraph:
    building, floor = draft.building, draft.floor
    room_nodes = list(draft.nodes)
    door_points = _cluster_door_points(draft.raw_geometry)
    barriers = _cut_door_openings(wall_segments(draft.raw_geometry), door_points)
    structural_walls = _cut_door_openings(
        structural_wall_segments(draft.raw_geometry), door_points
    )
    wall_index = WallIndex(barriers) if barriers else None
    structural_wall_index = WallIndex(structural_walls) if structural_walls else wall_index
    exterior_polygon = exterior_space_polygon(draft.raw_geometry)

    def passage_cost(point: Point) -> float:
        if exterior_polygon is None or _point_in_polygon(point, exterior_polygon):
            return 1.0
        return OUTSIDE_BUILDING_COST_MULTIPLIER

    if not door_points or len(room_nodes) < 2:
        # No doors detected (or nothing to connect) -- fall back to a direct
        # MST between room centroids so the floor is still one connected
        # graph, just without a distinct door-node layer.
        pairs = _mst_pairs(room_nodes)
        edges = [
            Edge(
                id=f"auto-hallway-{a.id}-{b.id}",
                from_node=a.id,
                to_node=b.id,
                weight=round(distance((a.x, a.y), (b.x, b.y)), 2),
                type="hallway",
            )
            for a, b in pairs
        ]
        return FloorGraph(
            building=building, floor=floor, nodes=room_nodes, edges=edges, auto_generated=True
        )

    door_nodes: dict[Point, Node] = {}
    for idx, point in enumerate(door_points):
        door_nodes[point] = Node(
            id=f"{building}-{floor}-door-{idx}",
            building=building,
            floor=floor,
            x=round(point[0], 1),
            y=round(point[1], 1),
            type="door",
            label=None,
            room_type=None,
            department=None,
        )

    doors = list(door_nodes.values())
    room_door_pairs: list[tuple[Node, Node, list[Point] | None]] = []
    for room in room_nodes:
        if structural_wall_index is not None:
            reachable = _nearest_reachable_door(room, doors, structural_wall_index)
            if reachable is None:
                continue
            door, path = reachable
            room_door_pairs.append((room, door, path))
        else:
            nearest = min(doors, key=lambda d: distance((room.x, room.y), (d.x, d.y)))
            room_door_pairs.append((room, nearest, None))

    hallway_pairs = (
        _wall_aware_mst_pairs(doors, wall_index) if wall_index is not None else _mst_pairs(doors)
    )

    all_nodes = list(room_nodes) + doors
    all_edges: list[Edge] = []
    waypoint_counter = [0]

    for room, door, room_path in room_door_pairs:
        if structural_wall_index is not None:
            new_nodes, new_edges = _materialize_edge(
                room,
                door,
                "door",
                structural_wall_index,
                building,
                floor,
                waypoint_counter,
                known_path=room_path,
            )
        else:
            new_nodes, new_edges = [], [
                Edge(
                    id=f"auto-door-{room.id}",
                    from_node=room.id,
                    to_node=door.id,
                    weight=round(distance((room.x, room.y), (door.x, door.y)), 2),
                    type="door",
                )
            ]
        all_nodes.extend(new_nodes)
        all_edges.extend(new_edges)

    if wall_index is not None:
        # Preserve every wall-safe grid path as authored. An earlier shared
        # "spine" optimization averaged nearby waypoints, but averaging can
        # move a valid point across a wall and turn two safe paths into one
        # unsafe straight segment. Global route simplification later removes
        # unnecessary visual turns while rechecking every proposed shortcut
        # against the same barrier index.
        for a, b in hallway_pairs:
            new_nodes, new_edges = _materialize_edge(
                a,
                b,
                "hallway",
                wall_index,
                building,
                floor,
                waypoint_counter,
                cost_multiplier=passage_cost,
            )
            all_nodes.extend(new_nodes)
            all_edges.extend(new_edges)
    else:
        for a, b in hallway_pairs:
            all_edges.append(
                Edge(
                    id=f"auto-hallway-{a.id}-{b.id}",
                    from_node=a.id,
                    to_node=b.id,
                    weight=round(distance((a.x, a.y), (b.x, b.y)), 2),
                    type="hallway",
                )
            )

    return FloorGraph(
        building=building,
        floor=floor,
        nodes=all_nodes,
        edges=all_edges,
        auto_generated=True,
    )
