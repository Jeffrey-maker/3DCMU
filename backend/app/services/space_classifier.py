"""Ask a vision model which numbered spaces are circulation -- nothing else.

Used only for floors with no space-type report. Tracing passage polylines by
eye failed badly on this data: asked to draw the network, the model silently
omitted whole wings of a large floor, and there was no way to tell a missing
wing from a floor that genuinely had none. Classification is a far smaller
question -- a list of room numbers in, a category per number out -- and its
answer is checkable, because the spaces it calls circulation must actually
form a connected region on the plan.

So the model only answers "which of these are corridors, stairs, lifts".
Everything after that -- the walkable mask, the skeleton, the centerlines,
the doors -- stays deterministic, and a wrong answer costs one mislabelled
space rather than a mistraced floor.
"""

import base64
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.models.graph import FloorDraft
from app.services import corridor_heuristic, gemini_vision

CATEGORIES = ("corridor", "stairway", "elevator", "room")
MIN_CORRIDOR_SPACES = 1
# The circulation of one floor is walkable end to end, so its outlines should
# form few connected clumps. Many disconnected ones means the model labelled
# scattered rooms rather than the corridor system, and the answer is refused.
MAX_CORRIDOR_COMPONENTS = 4
ADJACENCY_TOLERANCE = 6.0  # pt -- outlines this close are treated as touching
MAX_BRIDGE_PASSES = 3


class SpaceCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    category: str = Field(pattern="^(corridor|stairway|elevator|room)$")


class SpaceCategories(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spaces: list[SpaceCategory] = Field(max_length=2000)


def _schema() -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "spaces": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "label": {"type": "STRING"},
                        "category": {"type": "STRING"},
                    },
                    "required": ["label", "category"],
                },
            }
        },
        "required": ["spaces"],
    }


def _prompt(draft: FloorDraft, labels: list[str]) -> str:
    return (
        "This is an architectural floor plan "
        f"({draft.building}, floor {draft.floor}). Every numbered space on it "
        "is listed below. Classify EACH one into exactly one category:\n"
        "  corridor - public circulation: corridors, lobbies, vestibules, and "
        "open areas people walk through to reach other spaces\n"
        "  stairway - a stairwell\n"
        "  elevator - a lift car or lift shaft\n"
        "  room     - anything else (offices, labs, classrooms, toilets, "
        "storage, mechanical)\n\n"
        "Judge each space by what is drawn at its number on the plan. A "
        "corridor is long and thin, has many doors opening onto it, and joins "
        "up with the other corridors into one network that reaches the whole "
        "floor -- if the spaces you call corridors do not join up, you have "
        "missed some. Do not mark an ordinary room as a corridor merely "
        "because it is large. Return every label exactly once, using the "
        "label text verbatim.\n\nLabels: " + ", ".join(labels)
    )


def _components(polygons: list[list[tuple[float, float]]]) -> int:
    """How many separate clumps these outlines form, by bounding-box touch."""
    boxes = [
        (
            min(p[0] for p in poly), min(p[1] for p in poly),
            max(p[0] for p in poly), max(p[1] for p in poly),
        )
        for poly in polygons
    ]
    parent = list(range(len(boxes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if (
                a[0] - ADJACENCY_TOLERANCE <= b[2]
                and b[0] - ADJACENCY_TOLERANCE <= a[2]
                and a[1] - ADJACENCY_TOLERANCE <= b[3]
                and b[1] - ADJACENCY_TOLERANCE <= a[3]
            ):
                parent[find(i)] = find(j)
    return len({find(i) for i in range(len(boxes))})


def corridor_polygons(
    draft: FloorDraft, categories: dict[str, str]
) -> list[list[tuple[float, float]]]:
    """The outlines of every space classified as circulation, verified.

    Returns an empty list when the answer doesn't hold up geometrically, so
    the caller falls back rather than tracing a network through whatever the
    model happened to name.
    """
    polygons = draft.room_polygons
    chosen: set[int] = set()
    for node in draft.nodes:
        if node.label is None or categories.get(node.label) != "corridor":
            continue
        containing = [
            i for i, p in enumerate(polygons)
            if corridor_heuristic._point_in_polygon((node.x, node.y), p)
        ]
        if containing:
            chosen.add(min(containing, key=lambda i: _area(polygons[i])))
    if len(chosen) < MIN_CORRIDOR_SPACES:
        return []
    chosen = _bridge(chosen, polygons)
    if _components([polygons[i] for i in chosen]) > MAX_CORRIDOR_COMPONENTS:
        return []
    return [polygons[i] for i in chosen]


def _touches(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    ax0, ay0 = min(p[0] for p in a), min(p[1] for p in a)
    ax1, ay1 = max(p[0] for p in a), max(p[1] for p in a)
    bx0, by0 = min(p[0] for p in b), min(p[1] for p in b)
    bx1, by1 = max(p[0] for p in b), max(p[1] for p in b)
    return (
        ax0 - ADJACENCY_TOLERANCE <= bx1 and bx0 - ADJACENCY_TOLERANCE <= ax1
        and ay0 - ADJACENCY_TOLERANCE <= by1 and by0 - ADJACENCY_TOLERANCE <= ay1
    )


def _bridge(chosen: set[int], polygons: list[list[tuple[float, float]]]) -> set[int]:
    """Pull in the small connector spaces the model skipped.

    Classification reliably names the big per-wing corridors but tends to
    miss the vestibules and link spaces joining one wing to the next, which
    leaves the traced network in disconnected pieces. Any unclassified
    outline that touches two otherwise-separate pieces can only be such a
    link, so adding it is a deterministic repair rather than another guess.
    """
    for _ in range(MAX_BRIDGE_PASSES):
        groups: list[set[int]] = []
        for index in chosen:
            merged = [g for g in groups if any(_touches(polygons[index], polygons[j]) for j in g)]
            fresh = {index}
            for g in merged:
                fresh |= g
                groups.remove(g)
            groups.append(fresh)
        if len(groups) <= 1:
            break
        added = False
        for candidate in range(len(polygons)):
            if candidate in chosen:
                continue
            reached = sum(
                1 for g in groups if any(_touches(polygons[candidate], polygons[j]) for j in g)
            )
            if reached >= 2:
                chosen.add(candidate)
                added = True
        if not added:
            break
    return chosen


def _area(polygon: list[tuple[float, float]]) -> float:
    return abs(
        sum(
            polygon[i][0] * polygon[(i + 1) % len(polygon)][1]
            - polygon[(i + 1) % len(polygon)][0] * polygon[i][1]
            for i in range(len(polygon))
        )
    ) / 2


async def classify(raster_png: bytes, draft: FloorDraft) -> dict[str, str] | None:
    """Category per room label, or None when the model can't be reached."""
    if not settings.enable_gemini_vision or not settings.gemini_api_key:
        return None
    labels = sorted({n.label for n in draft.nodes if n.label})
    if not labels:
        return None

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    body: dict[str, Any] = {
        "contents": [{"parts": [
            {"text": _prompt(draft, labels)},
            {"inline_data": {
                "mime_type": "image/png",
                "data": base64.b64encode(raster_png).decode("ascii"),
            }},
        ]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _schema(),
            "thinkingConfig": {"thinkingLevel": settings.gemini_thinking_level},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await gemini_vision._post_with_retries(
                client, endpoint,
                headers={"x-goog-api-key": settings.gemini_api_key}, json=body,
            )
        candidate = response.json()["candidates"][0]
        if candidate.get("finishReason") != "STOP":
            return None
        text = "".join(
            part["text"] for part in candidate["content"]["parts"]
            if isinstance(part.get("text"), str) and not part.get("thought")
        )
        parsed = SpaceCategories.model_validate_json(text)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return None
    known = set(labels)
    return {s.label: s.category for s in parsed.spaces if s.label in known}
