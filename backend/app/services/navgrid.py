"""Grid-based (occupancy-map) pathfinding over raw wall geometry -- the
"chessboard" approach: the floor is treated as a fine grid of cells, two
adjacent cells are connected unless a wall segment sits between them, and
the true shortest walkable path is found via A* search over that grid.

This is a stronger guarantee against wall-crossing than validating a
handful of long straight-line candidates (as corridor_heuristic's
door-to-door MST selection does): every single step of the path is checked
against the walls, not just each candidate's two endpoints, so the
resulting path can never cut through a wall, and naturally bends around
corners the way a person actually walks.

Cost/accuracy tradeoff: cells are searched lazily (never materializing the
full grid), and the wall lookup is spatially bucketed (see
app.services.geometry.WallIndex), so a query over a ~1200x800pt floor at a
6pt cell size (the resolution used here) runs in well under a second.
"""

import heapq
import math
from typing import Callable, Optional

from app.services.geometry import Point, WallIndex

CELL_SIZE = 6.0  # pt -- fine enough to reliably pass through a real door gap
SIMPLIFY_TOLERANCE = 12.0  # pt -- lateral deviations under this are treated as navigationally insignificant
MAX_EXPANSIONS = 200_000  # safety bound so a genuinely unreachable point fails fast, not hangs

_NEIGHBOR_OFFSETS = [
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1), (0, 1),
    (1, -1), (1, 0), (1, 1),
]
_DIAGONAL = math.sqrt(2)

Cell = tuple[int, int]


def _snap(p: Point) -> Cell:
    return (round(p[0] / CELL_SIZE), round(p[1] / CELL_SIZE))


def _unsnap(cell: Cell) -> Point:
    return (cell[0] * CELL_SIZE, cell[1] * CELL_SIZE)


def _reachable_snap(p: Point, walls: WallIndex) -> Optional[Cell]:
    """Choose a nearby grid cell on the same side of every barrier as `p`.

    Blind rounding can place a door point and its snapped grid cell on
    opposite sides of a thin wall. A* would then validate the snapped path
    but omit that first wall-crossing hop when restoring the exact endpoint.
    """
    center = _snap(p)
    candidates = [
        (center[0] + dx, center[1] + dy)
        for dx in range(-1, 2)
        for dy in range(-1, 2)
    ]
    candidates.sort(key=lambda cell: math.hypot(_unsnap(cell)[0] - p[0], _unsnap(cell)[1] - p[1]))
    for cell in candidates:
        if not walls.crosses_wall(p, _unsnap(cell)):
            return cell
    return None


def find_path(
    start: Point,
    end: Point,
    walls: WallIndex,
    cost_multiplier: Optional[Callable[[Point], float]] = None,
    allowed_point: Optional[Callable[[Point], bool]] = None,
) -> Optional[list[Point]]:
    """A* over an implicit grid. Returns an ordered list of waypoints from
    start to end (inclusive), or None if no walkable path exists within the
    search bound."""
    start_cell, end_cell = _reachable_snap(start, walls), _reachable_snap(end, walls)
    if start_cell is None or end_cell is None:
        return None
    if start_cell == end_cell and not walls.crosses_wall(start, end):
        return [start, end]

    def heuristic(cell: Cell) -> float:
        return math.hypot(cell[0] - end_cell[0], cell[1] - end_cell[1]) * CELL_SIZE

    open_heap: list[tuple[float, float, Cell]] = [(heuristic(start_cell), 0.0, start_cell)]
    best_g: dict[Cell, float] = {start_cell: 0.0}
    came_from: dict[Cell, Cell] = {}
    visited: set[Cell] = set()
    expansions = 0

    while open_heap:
        _, g, cell = heapq.heappop(open_heap)
        if cell in visited:
            continue
        visited.add(cell)
        expansions += 1

        if cell == end_cell:
            path_cells = [cell]
            while cell in came_from:
                cell = came_from[cell]
                path_cells.append(cell)
            path_cells.reverse()
            points = [start] + [_unsnap(c) for c in path_cells] + [end]
            deduplicated = [points[0]]
            for point in points[1:]:
                if point != deduplicated[-1]:
                    deduplicated.append(point)
            return deduplicated

        if expansions > MAX_EXPANSIONS:
            return None

        p_cell = _unsnap(cell)
        for dx, dy in _NEIGHBOR_OFFSETS:
            neighbor = (cell[0] + dx, cell[1] + dy)
            if neighbor in visited:
                continue
            p_neighbor = _unsnap(neighbor)
            if allowed_point is not None and not allowed_point(p_neighbor):
                continue
            if walls.crosses_wall(p_cell, p_neighbor):
                continue
            multiplier = cost_multiplier(p_neighbor) if cost_multiplier else 1.0
            step_cost = (
                CELL_SIZE
                * (_DIAGONAL if dx != 0 and dy != 0 else 1.0)
                * max(1.0, multiplier)
            )
            tentative_g = g + step_cost
            if tentative_g < best_g.get(neighbor, float("inf")):
                best_g[neighbor] = tentative_g
                came_from[neighbor] = cell
                heapq.heappush(open_heap, (tentative_g + heuristic(neighbor), tentative_g, neighbor))

    return None


def _perpendicular_distance(point: Point, line_start: Point, line_end: Point) -> float:
    if line_start == line_end:
        return math.hypot(point[0] - line_start[0], point[1] - line_start[1])
    x0, y0 = point
    x1, y1 = line_start
    x2, y2 = line_end
    numerator = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1)
    denominator = math.hypot(y2 - y1, x2 - x1)
    return numerator / denominator


def simplify_indices(
    points: list[Point], walls: WallIndex, tolerance: float = SIMPLIFY_TOLERANCE
) -> list[int]:
    """Ramer-Douglas-Peucker simplification, constrained to never cross a
    wall: a raw grid path is a sequence of many small hops (often 50-150+
    for a real corridor walk) that can legitimately need to jog a few points
    sideways at every doorway it passes -- e.g. to clear the thin dividing
    wall between one office and the next at the corridor's edge -- even
    though a person walking that corridor would never perceive that as a
    real turn. A pure wall-crossing check (collapse anything with clear
    line of sight) can't remove those jogs, because the straight line
    between two distant doors along the same corridor typically DOES clip
    those intervening dividers. RDP fixes this the standard way: a point is
    only kept if it deviates from the straight line between its neighbors
    by more than `tolerance` -- small lateral jogs collapse away, real
    corners (which deviate by much more) survive. The wall-crossing check
    still gates every collapse, so simplification can never cut through a
    wall regardless of how small the deviation looks.

    Returns the surviving INDICES into the original list, not the points
    themselves, so a caller can carry per-point metadata (e.g. which graph
    Node each point came from) through simplification -- this matters when
    simplifying an already-resolved multi-edge route end to end, not just a
    single edge's own raw path: only a global pass sees that a run of
    several short edges in a row (e.g. connecting each door along one
    corridor to the next) was actually one straight walk.
    """
    if len(points) <= 2:
        return list(range(len(points)))

    def rdp(lo: int, hi: int) -> set[int]:
        if hi <= lo + 1:
            return {lo, hi}
        max_dist, max_idx = -1.0, lo + 1
        for i in range(lo + 1, hi):
            d = _perpendicular_distance(points[i], points[lo], points[hi])
            if d > max_dist:
                max_dist, max_idx = d, i
        if max_dist <= tolerance and not walls.crosses_wall(points[lo], points[hi]):
            return {lo, hi}
        return rdp(lo, max_idx) | rdp(max_idx, hi)

    return sorted(rdp(0, len(points) - 1))


def simplify_path(points: list[Point], walls: WallIndex) -> list[Point]:
    return [points[i] for i in simplify_indices(points, walls)]


def find_simplified_path(
    start: Point,
    end: Point,
    walls: WallIndex,
    cost_multiplier: Optional[Callable[[Point], float]] = None,
    allowed_point: Optional[Callable[[Point], bool]] = None,
) -> Optional[list[Point]]:
    raw = find_path(start, end, walls, cost_multiplier=cost_multiplier, allowed_point=allowed_point)
    if raw is None:
        return None
    return simplify_path(raw, walls)


def path_length(points: list[Point]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))
