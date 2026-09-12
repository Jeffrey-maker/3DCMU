"""Deterministically registers a facilities space-type report (colored by
category -- Public Corridor, Stairway, Elevator, etc.) onto the base floor
plan's own coordinate space, instead of handing Gemini two separately-scaled
images and hoping it cross-references them visually by eye.

Both PDFs are typically plotted from the same underlying CAD model at
slightly different page/print scales. Confirmed empirically on a real CMU
floor: matching room-number labels between the base plan and its space-type
report fit a pure scale+translation transform (no measurable rotation) to
within a few points of residual on a ~1200x800pt page. That precision is
enough to map the report's colored regions into the base plan's own frame
and draw them there directly, so the model sees one image with the
classification already embedded at the right place -- not two.
"""

from dataclasses import dataclass
from statistics import median

import fitz

from app.services.extraction import extract_text_labels, is_room_number

Point = tuple[float, float]
Color = tuple[float, float, float]

MIN_MATCHED_LABELS = 8
MAX_MEDIAN_RESIDUAL = 15.0  # pt -- above this, the fit isn't trustworthy enough to draw with
CATEGORY_LAYER = "A-SPAC-PATT"
FILL_MATCH_TOLERANCE = 0.02
# Hatch ticks are sparse, so a label's fill is sampled over an area, not one
# point. But a small classification (e.g. an elevator icon) can sit directly
# inside a much larger one (its serving corridor); searching too wide first
# lets the larger area's fill dominate purely by size. Try tight radii first
# and stop at the first one that finds anything, widening only when a
# category's own label truly has no nearby fill at all (observed for sparse
# stairway hatching).
LABEL_SEARCH_RADII = (2.0, 8.0, 20.0)

# Categories that matter for routing, keyed by the exact label text CMU's
# space-type export prints inside/near each classified area.
CATEGORY_LABELS = {
    "public_corridor": "Public Corridor",
    "stairway": "Stairway",
    "elevator": "Elevator",
}


@dataclass(frozen=True)
class Transform:
    scale_x: float
    offset_x: float
    scale_y: float
    offset_y: float

    def apply(self, p: Point) -> Point:
        return (self.scale_x * p[0] + self.offset_x, self.scale_y * p[1] + self.offset_y)

    def invert(self, p: Point) -> Point:
        """Base-plan coordinates back into the report's own space."""
        return (
            (p[0] - self.offset_x) / self.scale_x,
            (p[1] - self.offset_y) / self.scale_y,
        )


def labelled_positions(type_pdf_path: str, label_text: str) -> list[Point]:
    """Every place the report prints one category name, in its own space."""
    doc = fitz.open(type_pdf_path)
    try:
        found: list[Point] = []
        for block in doc[0].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["text"].strip() == label_text:
                        x0, y0, x1, y1 = span["bbox"]
                        found.append(((x0 + x1) / 2, (y0 + y1) / 2))
        return found
    finally:
        doc.close()


def _room_label_positions(pdf_path: str) -> dict[str, list[Point]]:
    positions: dict[str, list[Point]] = {}
    for label in extract_text_labels(pdf_path):
        if is_room_number(label.text):
            positions.setdefault(label.text, []).append((label.x, label.y))
    return positions


def _fit_axis(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares scale+offset for one axis: dst = scale*src + offset."""
    n = len(pairs)
    mean_s = sum(s for s, _ in pairs) / n
    mean_d = sum(d for _, d in pairs) / n
    denominator = sum((s - mean_s) ** 2 for s, _ in pairs)
    scale = sum((s - mean_s) * (d - mean_d) for s, d in pairs) / denominator if denominator > 1e-9 else 1.0
    offset = mean_d - scale * mean_s
    return scale, offset


def fit_transform(base_pdf_path: str, type_pdf_path: str) -> Transform | None:
    """Map type.pdf coordinates onto base.pdf coordinates using matching
    room-number labels as correspondence points. Only labels that appear
    exactly once in each PDF are used, so a duplicated number (e.g. a suite
    of "4201A"/"4201B") can never pair with the wrong instance. Returns None
    when there isn't enough overlap or the fit is too poor to trust --
    callers should fall back to treating the two PDFs as unrelated images."""
    base_positions = _room_label_positions(base_pdf_path)
    type_positions = _room_label_positions(type_pdf_path)
    common = [
        label
        for label in set(base_positions) & set(type_positions)
        if len(base_positions[label]) == 1 and len(type_positions[label]) == 1
    ]
    if len(common) < MIN_MATCHED_LABELS:
        return None

    pairs_x = [(type_positions[label][0][0], base_positions[label][0][0]) for label in common]
    pairs_y = [(type_positions[label][0][1], base_positions[label][0][1]) for label in common]
    scale_x, offset_x = _fit_axis(pairs_x)
    scale_y, offset_y = _fit_axis(pairs_y)
    transform = Transform(scale_x, offset_x, scale_y, offset_y)

    residuals = []
    for label in common:
        predicted = transform.apply(type_positions[label][0])
        actual = base_positions[label][0]
        residuals.append(((predicted[0] - actual[0]) ** 2 + (predicted[1] - actual[1]) ** 2) ** 0.5)
    if median(residuals) > MAX_MEDIAN_RESIDUAL:
        return None
    return transform


def _dominant_fill_for_label(page: fitz.Page, label_text: str, drawings: list[dict]) -> Color | None:
    """The space-type layer draws each category as many small hatch ticks,
    not one solid fill, so a single point-in-shape test often lands in a gap
    between ticks. Summing fill area near every occurrence of the category's
    own text label is robust to that sparseness -- but a small classification
    (an elevator icon) can sit directly inside a much larger one (its
    corridor), so radii are tried tight-first and widened only when nothing
    is found at all, rather than always pooling a wide area where the larger
    neighboring category would dominate by sheer size."""
    label_centers = [
        ((x0 + x1) / 2, (y0 + y1) / 2)
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span["text"].strip() == label_text
        for x0, y0, x1, y1 in [span["bbox"]]
    ]
    for radius in LABEL_SEARCH_RADII:
        totals: dict[Color, float] = {}
        for cx, cy in label_centers:
            for d in drawings:
                r = d["rect"]
                rcx, rcy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
                if (rcx - cx) ** 2 + (rcy - cy) ** 2 <= radius**2:
                    area = max(1e-6, r.x1 - r.x0) * max(1e-6, r.y1 - r.y0)
                    key = tuple(round(c, 3) for c in d["fill"])
                    totals[key] = totals.get(key, 0.0) + area
        if totals:
            return max(totals, key=totals.get)
    return None


def _category_label_candidates(page: fitz.Page) -> set[str]:
    """Every distinct space-type category name printed on the page, found
    generically rather than from a hardcoded list, so collisions against
    categories this app doesn't otherwise care about are still caught. Room
    numbers/areas and header/footer boilerplate (dates, titles, the "AREA"/
    "TOTAL:" legend column headers) all contain a digit; real category names
    (e.g. "Lounge", "Processing Room") never do, which is enough to tell them
    apart without hardcoding the FICM category list."""
    candidates: set[str] = set()
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = span["text"].strip()
                if len(t) > 3 and " - " not in t and not any(ch.isdigit() for ch in t):
                    candidates.add(t)
    return candidates


def _color_name(color: Color) -> str:
    r, g, b = color
    if r > 0.85 and g > 0.85 and b > 0.85:
        return "white/very light"
    if r > 0.85 and 0.55 < g < 0.85 and b < 0.65:
        return "tan/orange"
    if r > 0.8 and g < 0.3 and b > 0.5:
        return "magenta/pink"
    if g > 0.8 and r < 0.5 and b < 0.5:
        return "green"
    if r > 0.85 and g > 0.85 and b < 0.65:
        return "yellow"
    if b > 0.8 and r < 0.6:
        return "blue/cyan"
    if r > 0.8 and 0.4 < g < 0.75 and b < 0.75:
        return "salmon/pink"
    return f"rgb({r:.2f},{g:.2f},{b:.2f})"


# Space-type categories that become their own node type, because routing has
# to treat them as vertical connections to another floor rather than as
# ordinary rooms. Matched by the report's printed text, never by fill color:
# this export reuses one color across unrelated categories (Stairway shares
# its green with Open Stack Study and Janitor Room), so color would silently
# mistype private rooms as vertical circulation.
VERTICAL_LABELS = {"Stairway": "stair", "Elevator": "elevator"}


def vertical_circulation(
    base_pdf_path: str, type_pdf_path: str
) -> dict[str, list[Point]] | None:
    """Locate stair and elevator spaces, in base-plan coordinates."""
    transform = fit_transform(base_pdf_path, type_pdf_path)
    if transform is None:
        return None
    doc = fitz.open(type_pdf_path)
    try:
        page = doc[0]
        found: dict[str, list[Point]] = {node_type: [] for node_type in VERTICAL_LABELS.values()}
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    node_type = VERTICAL_LABELS.get(span["text"].strip())
                    if node_type is None:
                        continue
                    x0, y0, x1, y1 = span["bbox"]
                    found[node_type].append(transform.apply(((x0 + x1) / 2, (y0 + y1) / 2)))
        return found
    finally:
        doc.close()


@dataclass(frozen=True)
class OverlayResult:
    png_bytes: bytes
    matched_categories: dict[str, str]  # category key -> human color name actually drawn
    matched_shape_count: int


def build_overlay_pixmap(base_pdf_path: str, type_pdf_path: str, dpi: int) -> OverlayResult | None:
    """Draw the space-type report's Public Corridor / Stairway / Elevator
    areas directly onto a rendered copy of the base floor plan, precisely
    repositioned via `fit_transform`. Returns None if the two PDFs can't be
    reliably registered or no target category was found -- callers should
    fall back to sending the report as an unaligned second image instead."""
    transform = fit_transform(base_pdf_path, type_pdf_path)
    if transform is None:
        return None

    type_doc = fitz.open(type_pdf_path)
    try:
        type_page = type_doc[0]
        pattern_drawings = [
            d for d in type_page.get_drawings() if d.get("layer") == CATEGORY_LAYER and d.get("fill")
        ]

        # This export's palette is small enough that unrelated categories
        # can land on the exact same fill color, distinguished on paper only
        # by hatch pattern (observed on real data: Elevator/Processing Room/
        # Lounge all share one magenta; Stairway/Open Stack Study/Janitor
        # Room all share one green). Drawing a color that actually belongs to
        # a private room would be worse than not drawing it -- so any
        # category whose color collides with another real category found
        # anywhere on the page is dropped rather than guessed.
        all_labels = _category_label_candidates(type_page) | set(CATEGORY_LABELS.values())
        label_colors = {
            label: color
            for label in all_labels
            if (color := _dominant_fill_for_label(type_page, label, pattern_drawings)) is not None
        }
        color_owners: dict[Color, set[str]] = {}
        for label, color in label_colors.items():
            color_owners.setdefault(color, set()).add(label)

        category_fills: dict[str, Color] = {}
        for key, label_text in CATEGORY_LABELS.items():
            color = label_colors.get(label_text)
            if color is not None and len(color_owners[color]) == 1:
                category_fills[key] = color
        if not category_fills:
            return None

        target_colors = list(category_fills.values())
        matching = [
            d
            for d in pattern_drawings
            if any(
                all(abs(c - t) < FILL_MATCH_TOLERANCE for c, t in zip(d["fill"], target))
                for target in target_colors
            )
        ]
        if not matching:
            return None

        base_doc = fitz.open(base_pdf_path)
        try:
            base_page = base_doc[0]
            for d in matching:
                r = d["rect"]
                if r.is_empty or r.is_infinite:
                    continue
                p0 = transform.apply((r.x0, r.y0))
                p1 = transform.apply((r.x1, r.y1))
                rect = fitz.Rect(min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1]))
                if rect.width < 0.4 or rect.height < 0.4:
                    rect += (-0.3, -0.3, 0.3, 0.3)
                base_page.draw_rect(
                    rect, color=None, fill=tuple(d["fill"]), fill_opacity=0.65, overlay=True
                )
            pixmap = base_page.get_pixmap(dpi=dpi)
            return OverlayResult(
                png_bytes=pixmap.tobytes("png"),
                matched_categories={key: _color_name(fill) for key, fill in category_fills.items()},
                matched_shape_count=len(matching),
            )
        finally:
            base_doc.close()
    finally:
        type_doc.close()
