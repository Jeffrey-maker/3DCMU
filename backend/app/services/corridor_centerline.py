"""Compute public-corridor centerlines geometrically, with no vision model.

Once the space-type report is registered onto the base plan (see
space_type_overlay), "where is the corridor" stops being a perception
problem: the report fills every Public Corridor space with one known color,
so the corridor region can be read off directly and its centerline derived
by morphology. That removes the failure mode a vision model kept hitting on
this data -- silently omitting whole wings of a large, irregular floor --
and makes the result identical on every run.

Pipeline: render the report -> keep pixels of the corridor color -> close
small holes punched by room labels and furniture lines drawn over the fill
-> Zhang-Suen thinning to a one-pixel skeleton -> trace the skeleton into
polylines -> drop spurs, simplify, and map into base-plan coordinates.
"""

import math
from collections import defaultdict

import fitz

from app.config import settings
from app.models.graph import FloorDraft
from app.services import corridor_heuristic, space_type_overlay
from app.services.space_type_overlay import Transform

Point = tuple[float, float]
Cell = tuple[int, int]

CORRIDOR_LABEL = "Public Corridor"
CELL_PT = 1.5  # pt per mask pixel -- a ~17pt corridor is still ~11px wide
COLOR_TOLERANCE = 18  # per 0-255 channel, absorbs antialiasing at fill edges
CLOSE_RADIUS = 4  # px -- spans the room-number text printed over the fill
MIN_SKELETON_RUN = 6  # px -- shorter traces are thinning spurs, not passages
# A dead-end branch has to be substantially longer to be believed. Thinning a
# large open space (a study hall filled from its own outline) throws off a
# spur at every irregularity in a many-sided boundary; those are artifacts of
# the shape, not places anyone walks. A run between two junctions is kept at
# any length, because that is load-bearing connectivity.
MIN_DEAD_END_RUN = 24  # px
OPEN_SPACE_SMOOTHING = 8  # px -- opening radius applied to walkable room outlines
# Keep a walkable spine well clear of furniture. Hugging it too closely is
# counterproductive: the mask is coarser than the geometry check that runs
# later, so a spine drawn right along a table gets rejected downstream and
# takes the network's connectivity with it. Widening the clearance both
# simplifies the traced shape and leaves margin for that recheck to pass.
OBSTACLE_CLEARANCE = 3  # px
SIMPLIFY_TOLERANCE_PT = 2.0

# P2..P9, clockwise from north, per the Zhang-Suen formulation.
_NEIGHBORS = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]


def _corridor_mask(type_pdf_path: str, color: tuple[float, float, float]) -> set[Cell]:
    dpi = int(round(72.0 / CELL_PT))
    doc = fitz.open(type_pdf_path)
    try:
        pixmap = doc[0].get_pixmap(dpi=dpi)
    finally:
        doc.close()
    red, green, blue = (round(channel * 255) for channel in color)
    data, n, stride = pixmap.samples, pixmap.n, pixmap.stride
    mask: set[Cell] = set()
    for y in range(pixmap.height):
        row = y * stride
        for x in range(pixmap.width):
            offset = row + x * n
            if (
                abs(data[offset] - red) <= COLOR_TOLERANCE
                and abs(data[offset + 1] - green) <= COLOR_TOLERANCE
                and abs(data[offset + 2] - blue) <= COLOR_TOLERANCE
            ):
                mask.add((x, y))
    return mask


def _polygon_cells(polygon: list[Point], transform: Transform) -> set[Cell]:
    """Fill one base-plan room polygon into mask cells.

    Some spaces are walked through even though the inventory classifies them
    as rooms rather than circulation (a large open study hall, say). Those
    can't be picked out by fill color -- this export gives Open Stack Study
    the same green as Stairway and Janitor Room -- so they are located by
    their printed name and filled from their own CAD outline instead.
    """
    local = [transform.invert(p) for p in polygon]
    xs = [p[0] / CELL_PT for p in local]
    ys = [p[1] / CELL_PT for p in local]
    cells: set[Cell] = set()
    for y in range(int(min(ys)), int(max(ys)) + 1):
        # Scanline crossings at this row's center, then fill between pairs.
        center = y + 0.5
        crossings = []
        for (x0, y0), (x1, y1) in zip(
            zip(xs, ys), list(zip(xs, ys))[1:] + [list(zip(xs, ys))[0]]
        ):
            if (y0 > center) != (y1 > center):
                crossings.append(x0 + (center - y0) * (x1 - x0) / (y1 - y0))
        crossings.sort()
        for start, end in zip(crossings[::2], crossings[1::2]):
            for x in range(int(start), int(end) + 1):
                cells.add((x, y))
    return cells


def _barrier_cells(draft: FloorDraft, transform: Transform) -> set[Cell]:
    """Furniture, columns and walls as mask cells, in the report's own space.

    A corridor is mostly empty, so obstacles there barely matter. A large
    open room is not: its floor is covered in tables and stacks, and a spine
    drawn straight down the middle would cross them and be thrown out by the
    geometry check later. Removing the obstacles from the walkable area first
    makes the thinning thread between them instead.
    """
    cells: set[Cell] = set()
    for start, end in corridor_heuristic.wall_segments(draft.raw_geometry):
        a = transform.invert(start)
        b = transform.invert(end)
        length = math.hypot(b[0] - a[0], b[1] - a[1]) / CELL_PT
        for step in range(int(length * 2) + 2):
            t = min(1.0, step / max(1.0, length * 2))
            cells.add(
                (
                    int((a[0] + (b[0] - a[0]) * t) / CELL_PT),
                    int((a[1] + (b[1] - a[1]) * t) / CELL_PT),
                )
            )
    return cells


def walkable_room_cells(
    type_pdf_path: str,
    labels: list[str],
    room_polygons: list[list[Point]],
    transform: Transform,
    draft: FloorDraft | None = None,
) -> set[Cell]:
    from app.services import corridor_heuristic as heuristic

    def polygon_area(polygon: list[Point]) -> float:
        return abs(
            sum(
                polygon[i][0] * polygon[(i + 1) % len(polygon)][1]
                - polygon[(i + 1) % len(polygon)][0] * polygon[i][1]
                for i in range(len(polygon))
            )
        ) / 2

    cells: set[Cell] = set()
    for label in labels:
        for local in space_type_overlay.labelled_positions(type_pdf_path, label):
            point = transform.apply(local)
            containing = [p for p in room_polygons if heuristic._point_in_polygon(point, p)]
            if not containing:
                continue
            # A label can fall inside both a room and an enclosing outline;
            # the smallest match is the space the label actually names.
            cells |= _polygon_cells(min(containing, key=polygon_area), transform)
    if not cells:
        return cells
    # Thinning follows every wobble in an outline, and these rooms have very
    # detailed ones (a real study hall outline ran to 218 vertices), which
    # turns a single open space into a dense mesh of hundreds of branches.
    # Opening the shape first pulls the boundary smooth so what survives is
    # the one spine somebody would actually walk.
    cells = _dilate(_erode(cells, OPEN_SPACE_SMOOTHING), OPEN_SPACE_SMOOTHING)
    if draft is not None:
        cells -= _dilate(_barrier_cells(draft, transform), OBSTACLE_CLEARANCE)
    return cells


def _disc(radius: int) -> list[Cell]:
    return [
        (dx, dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if dx * dx + dy * dy <= radius * radius
    ]


def _dilate(mask: set[Cell], radius: int) -> set[Cell]:
    offsets = _disc(radius)
    grown: set[Cell] = set()
    for x, y in mask:
        for dx, dy in offsets:
            grown.add((x + dx, y + dy))
    return grown


def _erode(mask: set[Cell], radius: int) -> set[Cell]:
    offsets = _disc(radius)
    return {p for p in mask if all((p[0] + dx, p[1] + dy) in mask for dx, dy in offsets)}


def _close(mask: set[Cell], radius: int) -> set[Cell]:
    """Dilate then erode, repairing holes where text/linework covered the fill."""
    return _erode(_dilate(mask, radius), radius)


def walkable_mask(
    type_pdf_path: str,
    draft: FloorDraft,
    transform: Transform,
    corridor: set[Cell] | None = None,
) -> set[Cell]:
    """Every cell a person can stand in: public corridor plus any space
    configured as walked-through. Shared with door detection, which needs the
    same notion of "outside this room is somewhere you can walk"."""
    if corridor is None:
        color = corridor_color(type_pdf_path)
        if color is None:
            return set()
        corridor = _corridor_mask(type_pdf_path, color)
    mask = set(corridor)
    if settings.walkable_space_labels and draft.room_polygons:
        mask |= walkable_room_cells(
            type_pdf_path,
            settings.walkable_space_labels,
            draft.room_polygons,
            transform,
            draft,
        )
    return _close(mask, CLOSE_RADIUS)


def _components_touching(mask: set[Cell], seeds: set[Cell]) -> set[Cell]:
    """The parts of `mask` reachable from any seed cell, 4-connected."""
    frontier = [cell for cell in seeds if cell in mask]
    reached = set(frontier)
    while frontier:
        x, y = frontier.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbor = (x + dx, y + dy)
            if neighbor in mask and neighbor not in reached:
                reached.add(neighbor)
                frontier.append(neighbor)
    return reached


def _thin(mask: set[Cell]) -> set[Cell]:
    """Zhang-Suen thinning. Iterating the foreground set (rather than the
    whole raster) keeps this cheap: only corridor pixels are ever visited."""
    skeleton = set(mask)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            doomed = []
            for x, y in skeleton:
                neighbors = [
                    1 if (x + dx, y + dy) in skeleton else 0 for dx, dy in _NEIGHBORS
                ]
                filled = sum(neighbors)
                if filled < 2 or filled > 6:
                    continue
                transitions = sum(
                    1
                    for i in range(8)
                    if neighbors[i] == 0 and neighbors[(i + 1) % 8] == 1
                )
                if transitions != 1:
                    continue
                north, east, south, west = neighbors[0], neighbors[2], neighbors[4], neighbors[6]
                if step == 0:
                    if north * east * south or east * south * west:
                        continue
                else:
                    if north * east * west or north * south * west:
                        continue
                doomed.append((x, y))
            if doomed:
                skeleton.difference_update(doomed)
                changed = True
    return skeleton


def _trace(skeleton: set[Cell]) -> list[list[Cell]]:
    """Split the skeleton into runs between endpoints and junctions.

    Short runs are dropped only when they dead-end (a thinning spur). A short
    run joining two junctions is a real link -- pruning those by length alone
    silently cuts the passage network into disconnected islands.
    """
    degree = {
        p: sum(1 for dx, dy in _NEIGHBORS if (p[0] + dx, p[1] + dy) in skeleton)
        for p in skeleton
    }
    branch_points = {p for p, d in degree.items() if d != 2}
    walked: set[tuple[Cell, Cell]] = set()
    runs: list[list[Cell]] = []

    def walk(start: Cell, second: Cell) -> list[Cell]:
        run = [start, second]
        previous, current = start, second
        while degree.get(current, 0) == 2:
            following = next(
                (
                    q
                    for dx, dy in _NEIGHBORS
                    if (q := (current[0] + dx, current[1] + dy)) in skeleton and q != previous
                ),
                None,
            )
            if following is None:
                break
            run.append(following)
            previous, current = current, following
        return run

    for point in branch_points:
        for dx, dy in _NEIGHBORS:
            neighbor = (point[0] + dx, point[1] + dy)
            if neighbor not in skeleton or (point, neighbor) in walked:
                continue
            run = walk(point, neighbor)
            for a, b in zip(run, run[1:]):
                walked.add((a, b))
                walked.add((b, a))
            runs.append(run)

    # Rings with no junction at all are never reached above; follow them once.
    remaining = skeleton - {p for run in runs for p in run}
    while remaining:
        current = next(iter(remaining))
        remaining.discard(current)
        ring = [current]
        while True:
            following = next(
                (
                    q
                    for dx, dy in _NEIGHBORS
                    if (q := (current[0] + dx, current[1] + dy)) in remaining
                ),
                None,
            )
            if following is None:
                break
            ring.append(following)
            remaining.discard(following)
            current = following
        if len(ring) > MIN_SKELETON_RUN:
            runs.append(ring)

    kept = []
    for run in runs:
        joins_two_junctions = degree.get(run[0], 0) >= 3 and degree.get(run[-1], 0) >= 3
        minimum = MIN_SKELETON_RUN if joins_two_junctions else MIN_DEAD_END_RUN
        if joins_two_junctions or len(run) >= minimum:
            kept.append(run)
    return kept


def _simplify(points: list[Point], tolerance: float) -> list[Point]:
    if len(points) < 3:
        return points

    def deviation(p: Point, a: Point, b: Point) -> float:
        if a == b:
            return math.hypot(p[0] - a[0], p[1] - a[1])
        return abs(
            (b[1] - a[1]) * p[0] - (b[0] - a[0]) * p[1] + b[0] * a[1] - b[1] * a[0]
        ) / math.hypot(b[1] - a[1], b[0] - a[0])

    worst, index = 0.0, 0
    for i in range(1, len(points) - 1):
        d = deviation(points[i], points[0], points[-1])
        if d > worst:
            worst, index = d, i
    if worst > tolerance:
        return _simplify(points[: index + 1], tolerance)[:-1] + _simplify(points[index:], tolerance)
    return [points[0], points[-1]]


def corridor_color(type_pdf_path: str) -> tuple[float, float, float] | None:
    """The Public Corridor fill, but only when no other category on the page
    shares it -- this export reuses colors across categories (see
    space_type_overlay), and tracing a color that also means "Lounge" would
    route people through private rooms."""
    doc = fitz.open(type_pdf_path)
    try:
        page = doc[0]
        drawings = [
            d
            for d in page.get_drawings()
            if d.get("layer") == space_type_overlay.CATEGORY_LAYER and d.get("fill")
        ]
        labels = space_type_overlay._category_label_candidates(page) | {CORRIDOR_LABEL}
        colors = {
            label: color
            for label in labels
            if (color := space_type_overlay._dominant_fill_for_label(page, label, drawings))
            is not None
        }
        owners: dict[tuple[float, float, float], set[str]] = defaultdict(set)
        for label, color in colors.items():
            owners[color].add(label)
        color = colors.get(CORRIDOR_LABEL)
        if color is None or len(owners[color]) != 1:
            return None
        return color
    finally:
        doc.close()


def compute(base_pdf_path: str, type_pdf_path: str, draft: FloorDraft) -> list[list[Point]] | None:
    """Corridor centerlines in base-plan coordinates, or None when the two
    PDFs can't be registered or the corridor color isn't unambiguous."""
    transform = space_type_overlay.fit_transform(base_pdf_path, type_pdf_path)
    color = corridor_color(type_pdf_path)
    if transform is None or color is None:
        return None

    corridor = _corridor_mask(type_pdf_path, color)
    if not corridor:
        return None
    mask = walkable_mask(type_pdf_path, draft, transform, corridor)
    # Subtracting furniture can strand pockets of an open room behind the
    # tables that surround them. Keep only what is actually reachable from
    # the public corridor system: an isolated pocket is not somewhere a route
    # can start or finish, and tracing it would split the network into pieces
    # that cannot reach each other.
    mask = _components_touching(mask, corridor)
    return _skeletonize(mask, transform, draft)


def _skeletonize(
    mask: set[Cell], transform: Transform, draft: FloorDraft
) -> list[list[Point]] | None:
    """Thin a walkable mask down to centerlines in base-plan coordinates."""
    if not mask:
        return None
    runs = _trace(_thin(mask))
    exterior = corridor_heuristic.exterior_space_polygon(draft.raw_geometry)
    centerlines: list[list[Point]] = []
    for run in runs:
        points = [transform.apply((x * CELL_PT, y * CELL_PT)) for x, y in run]
        # The report prints its own legend swatch in the corridor colour;
        # anything outside the building outline is that, not a passage.
        if exterior is not None:
            inside = sum(1 for p in points if corridor_heuristic._point_in_polygon(p, exterior))
            if inside < len(points) * 0.5:
                continue
        simplified = _simplify(points, SIMPLIFY_TOLERANCE_PT)
        if len(simplified) >= 2:
            centerlines.append(simplified)
    return centerlines or None


IDENTITY = Transform(1.0, 0.0, 1.0, 0.0)


def compute_from_polygons(
    draft: FloorDraft, corridor_polygons: list[list[Point]]
) -> list[list[Point]] | None:
    """Centerlines for a floor with no space-type report, where the corridor
    outlines were identified some other way (see space_classifier).

    Everything after the question "which outlines are circulation" is the
    same deterministic morphology the report-driven path uses, so a wrong
    answer costs one mislabelled space rather than a mistraced floor.
    """
    if not corridor_polygons:
        return None
    mask: set[Cell] = set()
    for polygon in corridor_polygons:
        mask |= _polygon_cells(polygon, IDENTITY)
    if not mask:
        return None
    mask -= _dilate(_barrier_cells(draft, IDENTITY), OBSTACLE_CLEARANCE)
    mask = _close(mask, CLOSE_RADIUS)
    return _skeletonize(mask, IDENTITY, draft)


def as_suggestions(centerlines: list[list[Point]], page_width: float, page_height: float) -> dict:
    """Shape the result like a vision response so the existing geometry
    validator, junction splitter and door attachment consume it unchanged."""
    return {
        "summary": (
            f"{len(centerlines)} public-corridor centerlines derived geometrically "
            "from the space-type report."
        ),
        "pathways": [
            {
                "id": f"corridor-{index}",
                "points": [
                    {
                        "x_normalized": max(0.0, min(1000.0, p[0] / page_width * 1000)),
                        "y_normalized": max(0.0, min(1000.0, p[1] / page_height * 1000)),
                    }
                    for p in line
                ],
                "confidence": 1.0,
                "barrier_notes": "",
            }
            for index, line in enumerate(centerlines)
        ],
        "warnings": [],
    }
