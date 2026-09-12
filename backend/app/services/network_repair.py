"""Close the gaps in a traced passage network, using the wall grid.

Whatever identifies corridors -- the space-type report's colours, or a model
classifying spaces -- comes back incomplete on real floors: a link between
two wings is missed, and the network arrives as several pieces that cannot
reach each other, so routing across the building fails even though each
piece looks fine.

The missing links are recoverable without asking anything else, because the
walls already say where walking is possible. For each pair of disconnected
pieces this searches the wall grid for a real route between their nearest
ends, preferring cells already known to be circulation, and adopts the route
it finds as passage. A link is only accepted if the grid actually walks it,
so nothing is ever invented through a wall.

This runs once at upload, where taking a few seconds to be right is the
correct trade; routing afterwards just reads the finished network.
"""

import heapq
import math

from app.models.graph import FloorDraft
from app.services import corridor_heuristic, navgrid
from app.services.geometry import WallIndex

Point = tuple[float, float]

JOIN_TOLERANCE = 1.0  # pt -- endpoints this close already count as joined
MAX_BRIDGE_LENGTH = 420.0  # pt -- beyond this a "link" is a route through the building, not a gap
DETOUR_LIMIT = 3.0  # a bridge may wander this much further than the direct line
OFF_CORRIDOR_PENALTY = 6.0  # prefer known circulation, without forbidding the rest
MAX_BRIDGES = 40


def _key(point: Point) -> tuple[int, int]:
    return (round(point[0] / JOIN_TOLERANCE), round(point[1] / JOIN_TOLERANCE))


def _components(lines: list[list[Point]]) -> list[set[int]]:
    """Group line indices that share endpoints into connected pieces."""
    parent: dict[int, int] = {}

    def find(i: int) -> int:
        parent.setdefault(i, i)
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    owner: dict[tuple[int, int], int] = {}
    for index, line in enumerate(lines):
        find(index)
        for point in line:
            k = _key(point)
            if k in owner:
                union(index, owner[k])
            else:
                owner[k] = index

    groups: dict[int, set[int]] = {}
    for index in range(len(lines)):
        groups.setdefault(find(index), set()).add(index)
    return list(groups.values())


def _cross_group_gaps(
    lines: list[list[Point]], groups: list[set[int]]
) -> list[tuple[float, Point, Point]]:
    """Closest point pair between every two pieces, via a spatial hash.

    Comparing all points of all pieces against each other is quadratic and
    this runs repeatedly, so points are bucketed and only neighbouring
    buckets are compared -- which is where a doorway-width gap can be.
    """
    owner: dict[int, int] = {}
    for group_id, group in enumerate(groups):
        for index in group:
            owner[index] = group_id

    bucket_size = max(20.0, MAX_BRIDGE_LENGTH / 6)
    buckets: dict[tuple[int, int], list[tuple[Point, int]]] = {}
    for index, line in enumerate(lines):
        for point in line:
            key = (int(point[0] / bucket_size), int(point[1] / bucket_size))
            buckets.setdefault(key, []).append((point, owner[index]))

    best: dict[tuple[int, int], tuple[float, Point, Point]] = {}
    for (bx, by), entries in buckets.items():
        neighbours: list[tuple[Point, int]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbours.extend(buckets.get((bx + dx, by + dy), ()))
        for point, group_id in entries:
            for other, other_group in neighbours:
                if other_group == group_id:
                    continue
                distance = math.dist(point, other)
                if distance > MAX_BRIDGE_LENGTH:
                    continue
                pair = (min(group_id, other_group), max(group_id, other_group))
                if pair not in best or distance < best[pair][0]:
                    best[pair] = (
                        (distance, point, other)
                        if group_id < other_group
                        else (distance, other, point)
                    )
    return sorted(best.values())


def prune(centerlines: list[list[Point]], draft: FloorDraft) -> list[list[Point]]:
    """Drop the pieces of a traced line that clip an obstacle or leave the
    building, splitting the line where that happens.

    The mask a centerline is thinned from is coarser than the geometry check
    applied later, so a spine can end up grazing a table. Letting those
    through means they are silently rejected further down, and each rejection
    takes a link out of the network -- ten of them split one floor's passages
    into six unreachable pieces. Cutting them here instead lets the bridging
    below see the real gaps and walk proper routes around them.
    """
    barriers = WallIndex(corridor_heuristic.wall_segments(draft.raw_geometry))
    exterior = corridor_heuristic.exterior_space_polygon(draft.raw_geometry)

    def usable(a: Point, b: Point) -> bool:
        if exterior is not None and not (
            corridor_heuristic._point_in_polygon(a, exterior)
            and corridor_heuristic._point_in_polygon(b, exterior)
        ):
            return False
        return not barriers.crosses_wall(a, b)

    kept: list[list[Point]] = []
    for line in centerlines:
        run: list[Point] = []
        for a, b in zip(line, line[1:]):
            if usable(a, b):
                if not run:
                    run = [a]
                run.append(b)
            else:
                if len(run) >= 2:
                    kept.append(run)
                run = []
        if len(run) >= 2:
            kept.append(run)
    return kept


def bridge(
    centerlines: list[list[Point]],
    draft: FloorDraft,
    is_circulation=None,
) -> tuple[list[list[Point]], int]:
    """Add grid-walked links until the network is one piece (or no more fit).

    Returns the extended centerlines and how many links were added.
    """
    if len(centerlines) < 2:
        return centerlines, 0

    doors = corridor_heuristic._cluster_door_points(draft.raw_geometry)
    walls = WallIndex(
        corridor_heuristic._cut_door_openings(
            corridor_heuristic.wall_segments(draft.raw_geometry), doors
        )
    )
    exterior = corridor_heuristic.exterior_space_polygon(draft.raw_geometry)

    def allowed(point: Point) -> bool:
        return exterior is None or corridor_heuristic._point_in_polygon(point, exterior)

    def cost(point: Point) -> float:
        if is_circulation is None or is_circulation(point):
            return 1.0
        return OFF_CORRIDOR_PENALTY

    lines = list(centerlines)
    added = 0
    for _ in range(MAX_BRIDGES):
        groups = _components(lines)
        if len(groups) < 2:
            break
        # Always attack the closest remaining gap first: it is the most
        # likely to be a real doorway-width link rather than a coincidence.
        candidates = _cross_group_gaps(lines, groups)
        if not candidates:
            break

        linked = False
        for gap, start, end in candidates:
            path = navgrid.find_simplified_path(
                start, end, walls, cost_multiplier=cost, allowed_point=allowed
            )
            if path is None:
                continue
            walked = navgrid.path_length(path)
            if walked > max(MAX_BRIDGE_LENGTH, gap * DETOUR_LIMIT):
                continue
            if any(walls.crosses_wall(a, b) for a, b in zip(path, path[1:])):
                continue
            lines.append(path)
            added += 1
            linked = True
            break
        if not linked:
            break
    return lines, added


def finalize(
    centerlines: list[list[Point]], draft: FloorDraft, is_circulation=None
) -> tuple[list[list[Point]], dict]:
    """Clean and complete a traced network before it is turned into a graph.

    Order matters: pruning first exposes the true gaps, so bridging then
    walks real routes around the obstacles rather than re-proposing the
    blocked lines that were just removed.
    """
    before = len(_components(centerlines))
    pruned = prune(centerlines, draft)
    bridged, added = bridge(pruned, draft, is_circulation)
    return bridged, {
        "components_before": before,
        "components_after": len(_components(bridged)),
        "segments_pruned": sum(len(l) - 1 for l in centerlines) - sum(len(l) - 1 for l in pruned),
        "links_added": added,
    }


def unreached_rooms(
    centerlines: list[list[Point]], draft: FloorDraft, limit: float = 120.0
) -> list[str]:
    """Rooms with no walkable route to any passage line, for reporting."""
    doors = corridor_heuristic._cluster_door_points(draft.raw_geometry)
    walls = WallIndex(
        corridor_heuristic._cut_door_openings(
            corridor_heuristic.structural_wall_segments(draft.raw_geometry), doors
        )
    )
    points = [p for line in centerlines for p in line]
    stranded: list[str] = []
    for node in draft.nodes:
        if node.type not in {"room", "stair", "elevator"}:
            continue
        origin = (node.x, node.y)
        nearest = sorted(points, key=lambda p: math.dist(origin, p))[:6]
        if not any(
            navgrid.find_path(origin, target, walls) is not None
            for target in nearest
            if math.dist(origin, target) <= limit
        ):
            stranded.append(node.id)
    return stranded
