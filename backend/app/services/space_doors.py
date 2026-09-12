"""Find each space's own doorway from where it opens onto walkable space.

The drawn door-swing arcs give precise positions but say nothing about which
space owns them, and plenty of spaces have no arc at all -- an elevator is
drawn as a shaft with no swing, so nearest-arc matching hands it a door
belonging to some neighbouring room several feet away.

This works the other way round, from the space-type report's own colours: a
doorway is where a space's outline borders somewhere walkable AND the wall
has a gap. Both facts come from data already loaded -- the room outline from
the CAD area layer, "walkable" from the registered corridor colour, and the
gap from the structural wall layer -- so the door is attributed to its space
by construction rather than inferred from proximity. Where a real arc was
drawn nearby the opening snaps onto it, keeping the draughtsman's exact
position while fixing the ownership.
"""

import math

from app.models.graph import FloorDraft
from app.services import corridor_centerline, corridor_heuristic, space_type_overlay
from app.services.geometry import WallIndex

Point = tuple[float, float]

INNER_PROBE = 7.0  # pt inside the outline, clear of the wall itself
OUTER_PROBE = 9.0  # pt outside, far enough to land in the corridor
SAMPLE_STEP = 1.0  # pt along the outline
CLUSTER_GAP = 6.0  # pt -- samples further apart than this are separate doorways
MIN_DOORWAY_WIDTH = 0.0  # pt -- even a single sample marks a real gap in the wall
MAX_DOORWAY_WIDTH = 60.0  # pt -- wider is an open threshold, not a single door
# Snap tightly. A generous radius pulls a space onto the arc of the door
# next door, which puts the ownership ambiguity straight back in.
ARC_SNAP_RADIUS = 5.0  # pt


def _polygon_area(polygon: list[Point]) -> float:
    return abs(
        sum(
            polygon[i][0] * polygon[(i + 1) % len(polygon)][1]
            - polygon[(i + 1) % len(polygon)][0] * polygon[i][1]
            for i in range(len(polygon))
        )
    ) / 2


def _outline_for(point: Point, polygons: list[list[Point]]) -> list[Point] | None:
    containing = [p for p in polygons if corridor_heuristic._point_in_polygon(point, p)]
    if not containing:
        return None
    # A point can sit inside both a room and an enclosing outline; the
    # smallest match is the space it actually belongs to.
    return min(containing, key=_polygon_area)


def _cluster(samples: list[Point]) -> list[list[Point]]:
    clusters: list[list[Point]] = []
    for point in samples:
        placed = False
        for cluster in clusters:
            if any(math.dist(point, other) <= CLUSTER_GAP for other in cluster):
                cluster.append(point)
                placed = True
                break
        if not placed:
            clusters.append([point])
    return clusters


def _openings(outline: list[Point], is_walkable, walls: WallIndex) -> list[Point]:
    """Midpoints of the gaps where this outline opens onto walkable space."""
    samples: list[Point] = []
    for a, b in zip(outline, outline[1:] + outline[:1]):
        length = math.dist(a, b)
        if length < 1e-6:
            continue
        normal = (-(b[1] - a[1]) / length, (b[0] - a[0]) / length)
        for step in range(int(length / SAMPLE_STEP) + 1):
            t = min(1.0, step * SAMPLE_STEP / length)
            edge = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            # The outline's winding is not guaranteed, so try both normals
            # and keep whichever points from inside the space to outside it.
            for sign in (1, -1):
                inner = (edge[0] - sign * INNER_PROBE * normal[0], edge[1] - sign * INNER_PROBE * normal[1])
                outer = (edge[0] + sign * OUTER_PROBE * normal[0], edge[1] + sign * OUTER_PROBE * normal[1])
                if (
                    corridor_heuristic._point_in_polygon(inner, outline)
                    and is_walkable(outer)
                    and not walls.crosses_wall(inner, outer)
                ):
                    samples.append(edge)
                    break

    doorways: list[Point] = []
    for cluster in _cluster(samples):
        width = max(
            (math.dist(p, q) for p in cluster for q in cluster), default=0.0
        )
        if width < MIN_DOORWAY_WIDTH or width > MAX_DOORWAY_WIDTH:
            continue
        doorways.append(
            (
                sum(p[0] for p in cluster) / len(cluster),
                sum(p[1] for p in cluster) / len(cluster),
            )
        )
    return doorways


def detect(draft: FloorDraft, base_pdf_path: str, type_pdf_path: str) -> dict[str, list[Point]]:
    """Map each room/stair/elevator node id to the doorways of its own space.

    Returns an empty mapping for floors this can't be computed for (no room
    outlines, or a space-type report that couldn't be registered), leaving
    the caller on its existing nearest-arc behaviour.
    """
    if not draft.room_polygons:
        return {}
    transform = space_type_overlay.fit_transform(base_pdf_path, type_pdf_path)
    if transform is None:
        return {}
    mask = corridor_centerline.walkable_mask(type_pdf_path, draft, transform)
    if not mask:
        return {}
    cell = corridor_centerline.CELL_PT

    def is_walkable(point: Point) -> bool:
        local = transform.invert(point)
        return (int(local[0] / cell), int(local[1] / cell)) in mask

    walls = WallIndex(corridor_heuristic.structural_wall_segments(draft.raw_geometry))
    arcs = corridor_heuristic._cluster_door_points(draft.raw_geometry)

    # Only outlines holding exactly one space are usable. A node whose own
    # small outline doesn't contain it falls through to whatever larger
    # outline encloses it -- a corridor, or a suite drawn around several
    # rooms -- and would then inherit every opening of that big shape. Two
    # rooms were coming back with identical doorway lists spread right
    # across the floor that way, so an outline shared by more than one space
    # is treated as naming none of them.
    occupants: dict[int, list[str]] = {}
    outline_index: dict[str, int] = {}
    for node in draft.nodes:
        if node.type not in {"room", "stair", "elevator"}:
            continue
        outline = _outline_for((node.x, node.y), draft.room_polygons)
        if outline is None:
            continue
        index = draft.room_polygons.index(outline)
        occupants.setdefault(index, []).append(node.id)
        outline_index[node.id] = index

    found: dict[str, list[Point]] = {}
    for node in draft.nodes:
        index = outline_index.get(node.id)
        if index is None or len(occupants[index]) != 1:
            continue
        if is_walkable((node.x, node.y)):
            # This space IS the circulation -- a corridor or lobby numbered
            # like a room. It needs no door to reach the passage network, and
            # treating its whole length as openings would scatter doors along
            # it (one corridor produced nine, spread over 371pt).
            continue
        outline = draft.room_polygons[index]
        doorways = _openings(outline, is_walkable, walls)
        if not doorways:
            continue
        # Prefer the draughtsman's own door position when one was drawn here.
        snapped = []
        for doorway in doorways:
            nearest = min(arcs, key=lambda a: math.dist(a, doorway), default=None)
            snapped.append(
                nearest if nearest is not None and math.dist(nearest, doorway) <= ARC_SNAP_RADIUS
                else doorway
            )
        found[node.id] = snapped
    return found
