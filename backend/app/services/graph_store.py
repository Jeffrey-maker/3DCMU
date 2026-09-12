"""JSON-file-per-floor persistence for drafts, graphs, and AI review data.

One JSON blob per floorplan id (e.g. "WEH-1"), matching the API's own
read-whole-blob/replace-whole-blob shape. See build plan decision 3 for why
this beats SQLite at this scale (single-human editing, no relational queries).
"""

from pathlib import Path
from typing import Any
from datetime import datetime, timezone
from uuid import uuid4

import json

from app.config import settings
from app.models.graph import Edge, FloorDraft, FloorGraph, Node

_STAIR_ELEVATOR_WEIGHTS = {
    "stairs": settings.stair_edge_weight,
    "elevator": settings.elevator_edge_weight,
}


def floorplan_id(building: str, floor: int) -> str:
    return f"{building}-{floor}"


def floorplan_dir(fp_id: str) -> Path:
    return settings.data_dir / fp_id


def _graph_path(fp_id: str) -> Path:
    return floorplan_dir(fp_id) / "graph.json"


def _draft_path(fp_id: str) -> Path:
    return floorplan_dir(fp_id) / "draft.json"


def _gemini_analysis_path(fp_id: str) -> Path:
    return floorplan_dir(fp_id) / "gemini_analysis.json"


def raster_path(fp_id: str) -> Path:
    return floorplan_dir(fp_id) / "raster.png"


def base_pdf_path(fp_id: str) -> Path:
    return floorplan_dir(fp_id) / "base.pdf"


def overlay_pdf_path(fp_id: str, kind: str) -> Path:
    return floorplan_dir(fp_id) / f"{kind}.pdf"


def draft_exists(fp_id: str) -> bool:
    return _draft_path(fp_id).exists()


def graph_exists(fp_id: str) -> bool:
    return _graph_path(fp_id).exists()


def gemini_analysis_exists(fp_id: str) -> bool:
    return _gemini_analysis_path(fp_id).exists()


def save_draft(fp_id: str, draft: FloorDraft) -> None:
    floorplan_dir(fp_id).mkdir(parents=True, exist_ok=True)
    _draft_path(fp_id).write_text(draft.model_dump_json(indent=2))


def load_draft(fp_id: str) -> FloorDraft:
    return FloorDraft.model_validate_json(_draft_path(fp_id).read_text())


def _recompute_weight(edge: Edge, nodes_by_id: dict[str, Node]) -> float:
    if edge.type in _STAIR_ELEVATOR_WEIGHTS:
        return _STAIR_ELEVATOR_WEIGHTS[edge.type]
    if len(edge.points) >= 2:
        return round(
            sum(
                ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                for a, b in zip(edge.points, edge.points[1:])
            ),
            2,
        )
    a, b = nodes_by_id.get(edge.from_node), nodes_by_id.get(edge.to_node)
    if a is None or b is None:
        # Cross-floor or dangling reference (e.g. milestone-5 floor connectors)
        # — nothing in this floor's node list to compute a distance from.
        return edge.weight
    return round(((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5, 2)


def save_graph(fp_id: str, graph: FloorGraph) -> FloorGraph:
    """Persist a human-edited graph. Recomputes every edge weight from node
    coordinates server-side rather than trusting the client — the backend is
    the single source of truth for the number that actually drives
    pathfinding. Stairs/elevator edges get the fixed config-driven constants
    instead of a distance.
    """
    nodes_by_id = {n.id: n for n in graph.nodes}
    recomputed_edges = [
        edge.model_copy(update={"weight": _recompute_weight(edge, nodes_by_id)})
        for edge in graph.edges
    ]
    graph = graph.model_copy(update={"edges": recomputed_edges})
    floorplan_dir(fp_id).mkdir(parents=True, exist_ok=True)
    _graph_path(fp_id).write_text(graph.model_dump_json(indent=2))
    return graph


def load_graph(fp_id: str) -> FloorGraph:
    return FloorGraph.model_validate_json(_graph_path(fp_id).read_text())


def backup_graph(fp_id: str) -> None:
    """Keep a recoverable copy before replacing connections with passage routes."""
    if not graph_exists(fp_id):
        return
    directory = floorplan_dir(fp_id) / "graph-backups"
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = directory / f"{stamp}-{uuid4().hex[:8]}.json"
    path.write_bytes(_graph_path(fp_id).read_bytes())


def save_gemini_analysis(fp_id: str, analysis: dict[str, Any]) -> None:
    """Atomically persist an advisory vision result without touching the graph."""
    floorplan_dir(fp_id).mkdir(parents=True, exist_ok=True)
    path = _gemini_analysis_path(fp_id)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(analysis, indent=2))
    temporary.replace(path)


def load_gemini_analysis(fp_id: str) -> dict[str, Any]:
    return json.loads(_gemini_analysis_path(fp_id).read_text())


def list_floorplans() -> list[str]:
    if not settings.data_dir.exists():
        return []
    return sorted(p.name for p in settings.data_dir.iterdir() if p.is_dir())
