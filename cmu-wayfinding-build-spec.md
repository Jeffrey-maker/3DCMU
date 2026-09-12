# CMU Indoor + Outdoor Wayfinding App — Build Spec

## 0. What this is
A web app that gives room-to-room and building-to-building walking directions
across CMU campus, starting with Wean Hall Level 1 as the pilot floor. Output is
text directions now; a 2D graph visualization comes first, 3D rendering later.

Stack: **Python (FastAPI) backend**, **React frontend**, **IFM K2 Horizon API**
for the reasoning steps only (K2 Horizon is text-only — no image/vision input
is exposed, so it must never be asked to "read" a floor plan image).

---

## 1. Key architectural decision: don't ask an LLM to read the floor plan

The uploaded floor plan PDFs (`WEH-1-ESIM-Base/Dept/Type.pdf`) are **vector
drawings**, not scanned images — they contain real wall line segments, door
swing arcs, and room polygons as PDF vector objects, plus room-number and
room-type text as selectable text with exact coordinates. This means the
geometry can be extracted **deterministically** with a PDF parsing library —
no AI needed for this step, and no vision model required at all.

K2 Horizon's role is limited to two things it's actually good at as a
text-only reasoning model:
1. Suggesting corridor connectivity between extracted rooms/doors, given their
   coordinates and labels as structured text (not as an image).
2. Turning a computed path (list of nodes/edges) into natural-language
   step-by-step directions, optionally enriched with room-type context
   (e.g. "past the Robotics Institute lab").

Both are optional enhancements. The core app — parsing, graph storage,
pathfinding, and turn-by-turn generation — must work with **zero LLM calls**,
using plain Dijkstra/A* and geometry-based bearing calculations. If the K2 API
is unavailable or its suggestions look wrong, the app should still function.

---

## 2. Data model

```
Node {
  id: string
  building: string        # e.g. "WEH"
  floor: int               # e.g. 1
  x: float, y: float        # coordinates in the floor plan's local space
  type: "room" | "corridor" | "door" | "stair" | "elevator" | "entrance" | "outdoor"
  label: string | null      # room number, e.g. "1004"
  room_type: string | null  # from ESIM-Type PDF, e.g. "Office", "Research/Nonclass Laboratory"
  department: string | null # from ESIM-Dept PDF
}

Edge {
  id: string
  from_node: string, to_node: string
  weight: float             # distance or time cost
  type: "hallway" | "door" | "stairs" | "elevator" | "outdoor_path"
}
```

Vertical connections (stairs/elevators) are modeled as edges between a node on
floor N and the corresponding node on floor N+1, with a weight reflecting the
extra effort of a floor change.

---

## 3. Backend pipeline

### Step 1 — Upload & rasterize
- Endpoint: `POST /api/floorplans/upload` — **generic, works for any building/
  floor, not hardcoded to Wean Hall.** Accepts:
  - **1 required "base" PDF** per floor — must contain vector wall/room
    geometry plus room-number text (this alone is enough to produce a usable
    room-node graph — confirmed against the real Wean Hall Level 1 PDF: all
    52 rooms extracted with exact coordinates from this file alone).
  - **0–2 optional "overlay" PDFs** per floor (e.g. department, room-type) —
    same coordinate space as the base PDF, add metadata only, not required.
  - Form fields: `building`, `floor`, plus a tag per file (`base`/`dept`/
    `type`) so the parser knows which is which.
- Rasterizes each page to PNG (e.g. via `pdf2image`/`pdftoppm`) for frontend
  display/overlay purposes.

### Step 2 — Deterministic vector extraction
- Use `PyMuPDF` (`fitz`) to walk the PDF's vector drawing commands and text
  objects on the **Base** PDF:
  - Extract line/rect/curve segments → candidate wall geometry.
  - Extract text spans with bounding boxes → room numbers and their (x, y)
    centroids.
  - Extract small arc/curve clusters near room boundaries → candidate door
    positions (door swings render as quarter-circle arcs).
- Cross-reference room numbers against the **Type** and **Dept** PDFs (same
  coordinates, different text overlays) to attach `room_type` and
  `department` to each room node automatically.
- Output: a draft node list (one per room, positioned at its label centroid)
  and a set of candidate wall/door line segments — **not yet a connected
  graph**.

### Step 2b — Extraction script (tested against real Wean Hall Level 1 data)

```python
import fitz  # PyMuPDF — pip install pymupdf --break-system-packages
from dataclasses import dataclass
from typing import Optional
import json

@dataclass
class TextLabel:
    text: str
    x: float
    y: float

def extract_text_labels(pdf_path: str, page_number: int = 0) -> list[TextLabel]:
    doc = fitz.open(pdf_path)
    page = doc[page_number]
    labels = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span["text"].strip()
                if not text:
                    continue
                x0, y0, x1, y1 = span["bbox"]
                labels.append(TextLabel(text=text, x=(x0 + x1) / 2, y=(y0 + y1) / 2))
    doc.close()
    return labels

def extract_vector_geometry(pdf_path: str, page_number: int = 0):
    """Raw wall/door line segments — feed to the corridor-drafting step."""
    doc = fitz.open(pdf_path)
    page = doc[page_number]
    segments = []
    for d in page.get_drawings():
        for item in d["items"]:
            kind = item[0]
            if kind == "l":
                p1, p2 = item[1], item[2]
                segments.append({"type": "line", "p1": (p1.x, p1.y), "p2": (p2.x, p2.y)})
            elif kind == "c":  # bezier curve — often a door swing arc
                segments.append({"type": "curve", "points": [(p.x, p.y) for p in item[1:]]})
    doc.close()
    return segments

def is_room_number(text: str) -> bool:
    """CMU room numbers: digits, optionally with a trailing letter, e.g. '1004', '1001A'."""
    stripped = text.strip()
    return len(stripped) >= 3 and stripped[:4].isdigit()

def build_room_nodes(base_pdf_path: str, building: str, floor: int) -> list[dict]:
    labels = extract_text_labels(base_pdf_path)
    return [
        {
            "id": f"{building}-{floor}-{lbl.text}",
            "building": building, "floor": floor,
            "x": round(lbl.x, 1), "y": round(lbl.y, 1),
            "type": "room", "label": lbl.text,
            "room_type": None, "department": None,
        }
        for lbl in labels if is_room_number(lbl.text)
    ]

def enrich_with_overlay(room_nodes, overlay_pdf_path, field_name, match_radius=20.0):
    """
    KNOWN LIMITATION (confirmed on real data): matching overlay text to rooms by
    nearest-centroid only succeeded for ~15% of rooms — multi-word labels like
    "Research/Nonclass Laboratory" sit off-center from the room number. Before
    relying on this, switch to point-in-polygon matching against each room's
    bounding rectangle (extractable from the vector geometry) instead of
    nearest-point-to-point distance.
    """
    overlay_labels = extract_text_labels(overlay_pdf_path)
    candidate = [l for l in overlay_labels
                 if len(l.text) > 3 and not l.text.replace(",", "").replace("sf", "").strip().isdigit()]
    for node in room_nodes:
        nearest, best_d = None, None
        for l in candidate:
            d = (l.x - node["x"]) ** 2 + (l.y - node["y"]) ** 2
            if best_d is None or d < best_d:
                best_d, nearest = d, l
        if nearest and best_d ** 0.5 <= match_radius:
            node[field_name] = nearest.text
    return room_nodes

def extract_floor_draft(base_pdf_path: str, building: str, floor: int,
                         dept_pdf_path: Optional[str] = None,
                         type_pdf_path: Optional[str] = None) -> dict:
    """Generic entry point — works for any uploaded floor, base file required, overlays optional."""
    room_nodes = build_room_nodes(base_pdf_path, building, floor)
    if dept_pdf_path:
        room_nodes = enrich_with_overlay(room_nodes, dept_pdf_path, "department")
    if type_pdf_path:
        room_nodes = enrich_with_overlay(room_nodes, type_pdf_path, "room_type")
    return {
        "building": building, "floor": floor,
        "nodes": room_nodes,
        "raw_geometry": extract_vector_geometry(base_pdf_path),
    }
```

**Verified results on Wean Hall Level 1**: 52/52 rooms extracted with exact
coordinates from the Base PDF alone; 3,831 wall line segments extracted as
raw geometry. Overlay metadata matching needs the point-in-polygon fix noted
above before it's reliable enough to trust automatically — until then, surface
unmatched `department`/`room_type` fields as blank in the graph editor so a
human can fill them in rather than silently mismatching them.

### Step 3 — Corridor graph drafting (semi-automated)
Reconstructing full corridor topology from raw wall vectors is a genuinely
hard graphics problem — don't try to fully automate it for v1. Instead:
- Auto-place corridor nodes at intersections of large open (unlabeled, "Public
  Corridor" typed) polygons, using simple centroid/skeleton heuristics.
- Send the draft (room nodes + coordinates + room_type/department labels +
  candidate corridor nodes) to **K2 Horizon as structured JSON in the prompt
  text** — ask it to propose which rooms/doors most plausibly connect to which
  corridor node, given their coordinates and adjacency. Treat this purely as a
  **draft suggestion**.
- Surface the draft in the frontend's graph editor (Step 4) for a human
  (you) to confirm, delete, or redraw edges. This human-in-the-loop pass is
  the actual reliable source of truth for v1 — the LLM only saves you typing.

### Step 4 — Graph editor (backend support)
- `GET /api/floorplans/{id}/graph` — return current draft graph + rasterized
  image for overlay.
- `PUT /api/floorplans/{id}/graph` — save edits (add/remove/move nodes, draw
  edges) made in the frontend editor.
- `POST /api/floorplans/{id}/connect-floor` — manually link a stair/elevator
  node on this floor to its counterpart on another floor.

### Step 5 — Pathfinding
- `GET /api/route?from={node_id}&to={node_id}` — run Dijkstra (or A* using
  Euclidean distance as the heuristic) over the full graph (all connected
  floors + outdoor graph once added). Return the ordered node/edge path.

### Step 6 — Directions generation
- Pure-geometry version (no AI, ship this first): at each intermediate node,
  compute the bearing change between incoming and outgoing edges and classify
  it into "continue straight" / "turn left" / "turn right" / "turn around";
  special-case edge type changes ("take the stairs to floor 2", "exit the
  building").
- Optional K2 Horizon enhancement: `POST` the ordered path (with room types/
  departments attached) to K2 Horizon and ask it to phrase the same
  turn-by-turn list as fluent natural language. Cache/store the deterministic
  version as the fallback if this call fails or times out.

---

## 4. K2 Horizon API integration notes
- Standard chat-completion call, structured JSON in the prompt (not an image).
- Two call sites only, both optional/best-effort:
  1. Corridor-connectivity suggestion (Step 3).
  2. Path → natural-language directions (Step 6).
- Wrap both in a try/except with a deterministic fallback — never block core
  functionality on the LLM call succeeding.
- Confirm with a real API call early (before building around it) whether K2
  Horizon's hosted endpoint (via Compass/Cerebras/AWS/Nebius per IFM's
  inference partners) accepts image content blocks at all — treat "text-only"
  as the working assumption above unless that test proves otherwise.

---

## 5. Frontend (React)

- **Upload page**: drag-and-drop PDF upload, shows rasterization progress.
- **Graph editor view**: floor plan image with an SVG/canvas overlay
  (react-konva or plain SVG) showing draft nodes/edges; click to add/move
  nodes, drag to draw edges, delete key to remove; a "floor" selector tab bar
  for multi-floor buildings.
- **Room/floor picker**: searchable dropdowns for start and destination
  (building → floor → room), populated from extracted room labels.
- **Route view**:
  - Highlight the path as a colored line over the 2D floor plan.
  - When the path crosses to a different floor, render a small stairs/
    elevator icon at the transition node, then switch the displayed floor
    plan to the next floor (tabbed or auto-advancing) and continue the
    highlighted path there — same visual language as a multi-day itinerary
    view, but multi-floor.
  - Text directions panel synced to the visual path (highlighting the current
    step as the user scrolls/steps through it).

---

## 6. Build order (milestones)

1. Vector extraction script for Wean Hall Level 1 Base PDF → room node list
   with coordinates + labels (no graph yet). Verify against the rendered PNG.
2. Cross-reference Dept/Type PDFs to attach room_type/department to each node.
3. Manual graph editor (frontend) + save/load endpoints — build the Wean Hall
   1 corridor graph by hand once, to validate the data model end-to-end.
4. Dijkstra pathfinding + deterministic bearing-based directions, room-to-room
   on Wean Hall 1 only.
5. Add a second floor + stair connector node; validate floor-change directions
   and the floor-switching UI.
6. Wire in K2 Horizon for the two optional enhancement calls, with fallbacks.
7. Add outdoor OSM graph + building entrances for building-to-building routes.
8. (Later) 3D visualization layer on top of the same graph data.

---

## 7. Open items to confirm before/while building
- Confirm K2 Horizon's actual request/response schema and auth method from
  IFM/your inference partner's docs — not assumed here.
- Decide stride/weight conventions for stairs vs. flat hallway (how much
  "extra distance" a floor change should cost in the pathfinding weights).
- Decide whether room polygons are needed (for future 3D) or centroid points
  are sufficient for v1's 2D path line.
