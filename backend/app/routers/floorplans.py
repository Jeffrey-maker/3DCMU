from datetime import datetime, timezone
from typing import Optional
import asyncio
import hashlib

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
import fitz

from app.config import settings
from app.models.graph import FloorDraft, FloorGraph
from app.services import (
    corridor_centerline,
    network_repair,
    space_classifier,
    corridor_heuristic,
    extraction,
    gemini_vision,
    graph_store,
    passage_graph,
    rasterize,
)

router = APIRouter(prefix="/api/floorplans", tags=["floorplans"])
_generation_locks: dict[str, asyncio.Lock] = {}


@router.get("")
def get_floorplans():
    """List every persisted floor so the frontend can reopen one after refresh."""
    floorplans = []
    for fp_id in graph_store.list_floorplans():
        if not graph_store.draft_exists(fp_id):
            continue
        draft = graph_store.load_draft(fp_id)
        graph = graph_store.load_graph(fp_id) if graph_store.graph_exists(fp_id) else None
        floorplans.append(
            {
                "floorplan_id": fp_id,
                "building": draft.building,
                "floor": draft.floor,
                "raster_path": draft.raster_path,
                "has_graph": graph is not None,
                "routing_source": graph.routing_source if graph is not None else None,
                "node_count": len(graph.nodes) if graph is not None else len(draft.nodes),
                "edge_count": len(graph.edges) if graph is not None else 0,
            }
        )
    return {"floorplans": floorplans}


def _graph_matches_draft(existing: FloorGraph, draft: FloorDraft) -> bool:
    """An existing graph is only worth preserving if it actually describes
    THIS floor plan. building+floor alone isn't a reliable enough identity
    key -- someone can upload a different PDF entirely under the same
    building/floor code (by mistake, or while testing), and a graph full of
    rooms that no longer exist in the new draft is worse than no graph at
    all, not real work worth protecting."""
    # Auto-generated graphs are disposable build artifacts. Re-uploading a
    # floor should rebuild them with the current routing algorithm; only a
    # graph explicitly saved by a human is protected.
    if existing.auto_generated or not existing.edges:
        return False
    existing_room_ids = {n.id for n in existing.nodes if n.type == "room"}
    if not existing_room_ids:
        return False
    draft_ids = {n.id for n in draft.nodes}
    overlap = len(existing_room_ids & draft_ids) / len(existing_room_ids)
    return overlap >= 0.5


_EXTRACTED_NODE_TYPES = {"room", "stair", "elevator"}


def _refresh_room_metadata(existing: FloorGraph, draft: FloorDraft) -> FloorGraph:
    """Apply fresh overlay metadata without moving or rebuilding graph nodes.

    `type` is refreshed alongside the labels because stair/elevator is itself
    derived from the overlays (see extraction.apply_vertical_circulation) --
    leaving it stale would keep a re-uploaded floor's vertical circulation
    invisible to routing. Only extraction-owned types are rewritten, so a
    door or corridor node a human placed is never retyped.
    """
    draft_nodes = {node.id: node for node in draft.nodes}
    nodes = []
    for node in existing.nodes:
        fresh = draft_nodes.get(node.id)
        if fresh is None or node.type not in _EXTRACTED_NODE_TYPES:
            nodes.append(node)
            continue
        nodes.append(node.model_copy(update={
            "label": fresh.label,
            "department": fresh.department,
            "room_type": fresh.room_type,
            "type": fresh.type if fresh.type in _EXTRACTED_NODE_TYPES else node.type,
        }))
    return existing.model_copy(update={"nodes": nodes})


def _build_passages_on_upload(
    fp_id: str, draft: FloorDraft, base_pdf_path: str, type_pdf_path: Optional[str]
) -> Optional[FloorGraph]:
    """Derive the passage network at upload time, or None to fall back.

    Returns None rather than raising for any floor this can't handle -- a PDF
    with no space-type overlay, one whose two sheets can't be registered, or
    a corridor color the report also uses for something else. Uploading must
    still succeed in all of those cases; they just fall back to anchors-only
    and the explicit tracing button.
    """
    if type_pdf_path is None:
        return None
    try:
        centerlines = corridor_centerline.compute(base_pdf_path, type_pdf_path, draft)
        if not centerlines:
            return None
        centerlines, _ = network_repair.finalize(centerlines, draft)
        with fitz.open(base_pdf_path) as pdf:
            width, height = pdf[0].rect.width, pdf[0].rect.height
        result = passage_graph.build_passage_graph(
            draft,
            FloorGraph(building=draft.building, floor=draft.floor, nodes=draft.nodes),
            corridor_centerline.as_suggestions(centerlines, width, height),
            width,
            height,
            {"pdf_bounds": [0, 0, width, height]},
            "space_type_centerlines",
        )
        return result.graph
    except (ValueError, KeyError, OSError):
        return None


@router.post("/upload")
async def upload_floorplan(
    building: str = Form(...),
    floor: int = Form(...),
    base: UploadFile = File(...),
    dept: Optional[UploadFile] = File(None),
    type: Optional[UploadFile] = File(None),
):
    """Generic upload — works for any building/floor. Only `base` is
    required; `dept`/`type` overlays add metadata only. `type` (e.g. a
    facilities space-inventory report with a colored Public Corridor/
    Stairway/Elevator legend) is also handed to Gemini as a second visual
    reference, matched by room number, when tracing passage centerlines --
    it is never geometrically aligned with `base` for that purpose. Re-
    uploading `base` regenerates the draft/raster but never overwrites an
    existing hand-built graph.json."""
    fp_id = graph_store.floorplan_id(building, floor)
    graph_store.floorplan_dir(fp_id).mkdir(parents=True, exist_ok=True)

    base_path = graph_store.base_pdf_path(fp_id)
    base_bytes = await base.read()
    same_pdf = base_path.exists() and base_path.read_bytes() == base_bytes
    base_path.write_bytes(base_bytes)

    dept_path: Optional[str] = None
    if dept is not None:
        p = graph_store.overlay_pdf_path(fp_id, "dept")
        p.write_bytes(await dept.read())
        dept_path = str(p)
    elif graph_store.overlay_pdf_path(fp_id, "dept").exists():
        dept_path = str(graph_store.overlay_pdf_path(fp_id, "dept"))

    type_path: Optional[str] = None
    if type is not None:
        p = graph_store.overlay_pdf_path(fp_id, "type")
        p.write_bytes(await type.read())
        type_path = str(p)
    elif graph_store.overlay_pdf_path(fp_id, "type").exists():
        type_path = str(graph_store.overlay_pdf_path(fp_id, "type"))

    raster_path = graph_store.raster_path(fp_id)
    rasterize.render_to_png(str(base_path), str(raster_path))

    draft = extraction.extract_floor_draft(
        str(base_path),
        building=building,
        floor=floor,
        dept_pdf_path=dept_path,
        type_pdf_path=type_path,
    )
    draft.raster_path = f"/api/floorplans/{fp_id}/raster.png"
    graph_store.save_draft(fp_id, draft)

    # A graph with real edges that actually describes THIS floor plan
    # represents real work (hand-built or a previously accepted auto-graph)
    # and must never be silently clobbered. A missing graph, one saved with
    # zero edges (e.g. a human clicked Save before drawing anything), or one
    # that no longer matches this draft (a different PDF uploaded under the
    # same building/floor code) has no such work worth protecting -- generate
    # a best-effort connected corridor graph automatically so routing works
    # immediately after uploading just the base PDF.
    existing = graph_store.load_graph(fp_id) if graph_store.graph_exists(fp_id) else None
    existing_graph_preserved = existing is not None and _graph_matches_draft(existing, draft)
    if existing is not None and existing.routing_source in passage_graph.PASSAGE_ROUTING_SOURCES:
        existing_graph_preserved = same_pdf
    corridor_graph_status = "preserved"
    if existing_graph_preserved and existing is not None:
        # A same-floor overlay reupload enriches the current graph in place.
        # Passage geometry and hand-edited node positions remain untouched.
        graph_store.save_graph(fp_id, _refresh_room_metadata(existing, draft))
    else:
        # With a space-type overlay the whole passage network is derived
        # geometrically in a couple of seconds and costs nothing, so there is
        # no reason to make someone press a button for it -- an uploaded
        # floor should just be routable. Without that overlay, tracing needs
        # a paid vision call, which stays an explicit choice.
        built = await run_in_threadpool(
            _build_passages_on_upload, fp_id, draft, str(base_path), type_path
        )
        if built is not None:
            graph_store.save_graph(fp_id, built)
            corridor_graph_status = "passages_generated"
        else:
            legacy_graph = corridor_heuristic.generate_auto_graph(draft)
            auto_graph = passage_graph.strip_legacy_connections(draft, legacy_graph)
            graph_store.save_graph(fp_id, auto_graph)
            corridor_graph_status = "auto_generated"

    return {
        "floorplan_id": fp_id,
        "room_count": len(draft.nodes),
        "raw_geometry_count": len(draft.raw_geometry),
        "raster_path": draft.raster_path,
        "existing_graph_preserved": existing_graph_preserved,
        "corridor_graph": corridor_graph_status,
    }


@router.get("/{floorplan_id}/raster.png")
def get_raster(floorplan_id: str):
    from fastapi.responses import FileResponse

    path = graph_store.raster_path(floorplan_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Floorplan raster not found")
    return FileResponse(path, media_type="image/png")


@router.get("/{floorplan_id}/graph")
def get_graph(floorplan_id: str):
    """Returns the current hand-edited graph if one has been saved, else the
    raw extraction draft (nodes only, no edges yet) for a human to start
    building from in the editor."""
    if not graph_store.draft_exists(floorplan_id):
        raise HTTPException(status_code=404, detail="Floorplan not found")

    draft = graph_store.load_draft(floorplan_id)
    if graph_store.graph_exists(floorplan_id):
        graph = graph_store.load_graph(floorplan_id)
        source = "graph"
    else:
        graph = FloorGraph(building=draft.building, floor=draft.floor, nodes=draft.nodes, edges=[])
        source = "draft"

    return {
        "source": source,
        "graph": graph.model_dump(),
        "raster_path": draft.raster_path,
    }


@router.put("/{floorplan_id}/graph")
def put_graph(floorplan_id: str, graph: FloorGraph):
    if not graph_store.draft_exists(floorplan_id):
        raise HTTPException(status_code=404, detail="Floorplan not found")
    # An explicit PUT is always a deliberate human edit -- it always wins
    # over, and clears, any prior auto-generated status.
    graph = graph.model_copy(update={"auto_generated": False})
    saved = graph_store.save_graph(floorplan_id, graph)
    return {"saved": True, "node_count": len(saved.nodes), "edge_count": len(saved.edges)}


@router.post("/{floorplan_id}/analyze-gemini")
async def analyze_with_gemini(floorplan_id: str):
    """Send the raster to Gemini on explicit request and store its advisory JSON.

    This endpoint never mutates graph.json. A human/deterministic validation
    step must review the saved suggestions before applying any of them.
    """
    if not graph_store.draft_exists(floorplan_id):
        raise HTTPException(status_code=404, detail="Floorplan not found")
    raster = graph_store.raster_path(floorplan_id)
    if not raster.exists():
        raise HTTPException(status_code=404, detail="Floorplan raster not found")

    with fitz.open(graph_store.base_pdf_path(floorplan_id)) as pdf:
        width, height = pdf[0].rect.width, pdf[0].rect.height
    space_type_pdf = graph_store.overlay_pdf_path(floorplan_id, "type")
    try:
        analysis = await gemini_vision.analyze_floorplan(
            raster, graph_store.load_draft(floorplan_id), width, height,
            space_type_pdf_path=space_type_pdf if space_type_pdf.exists() else None,
            base_pdf_path=graph_store.base_pdf_path(floorplan_id),
        )
    except gemini_vision.GeminiVisionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    graph_store.save_gemini_analysis(floorplan_id, analysis)
    suggestions = analysis["suggestions"]
    return {
        "saved": True,
        "status": analysis["status"],
        "model": analysis["model"],
        "room_node_count": len(suggestions.get("room_nodes", [])),
        "door_count": len(suggestions.get("doors", [])),
        "pathway_count": len(suggestions["pathways"]),
        "barrier_count": len(suggestions.get("barriers", [])),
        "analysis": analysis,
    }


@router.get("/{floorplan_id}/gemini-analysis")
def get_gemini_analysis(floorplan_id: str):
    if not graph_store.draft_exists(floorplan_id):
        raise HTTPException(status_code=404, detail="Floorplan not found")
    if not graph_store.gemini_analysis_exists(floorplan_id):
        raise HTTPException(status_code=404, detail="Gemini analysis not found")
    return graph_store.load_gemini_analysis(floorplan_id)


class BuildPassagesRequest(BaseModel):
    graph: FloorGraph | None = None
    refresh: bool = False


@router.post("/{floorplan_id}/generate-pathways")
async def generate_pathways(floorplan_id: str, request: BuildPassagesRequest):
    """Trace/cache passage lines, build the navigation graph, and persist it."""
    if not graph_store.draft_exists(floorplan_id):
        raise HTTPException(status_code=404, detail="Floorplan not found")
    lock = _generation_locks.setdefault(floorplan_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="Passage tracing is already running for this floor.")
    async with lock:
        draft = graph_store.load_draft(floorplan_id)
        raster = graph_store.raster_path(floorplan_id)
        if not raster.exists():
            raise HTTPException(status_code=404, detail="Floorplan raster not found")
        raster_hash = hashlib.sha256(raster.read_bytes()).hexdigest()
        space_type_pdf = graph_store.overlay_pdf_path(floorplan_id, "type")
        space_type_pdf = space_type_pdf if space_type_pdf.exists() else None
        space_type_hash = (
            hashlib.sha256(rasterize.render_to_bytes(str(space_type_pdf))).hexdigest()
            if space_type_pdf is not None else None
        )
        stored = graph_store.load_graph(floorplan_id) if graph_store.graph_exists(floorplan_id) else None
        existing = request.graph or stored or FloorGraph(building=draft.building, floor=draft.floor, nodes=draft.nodes)
        if existing.auto_generated and request.graph is None and draft.space_doors:
            # Door nodes in an auto-generated graph are build artifacts, not
            # authored work. Carrying them over would stack a fresh set of
            # detected doorways on top of the previous run's every time this
            # is re-run; rebuild from the draft instead. Only safe when the
            # draft can supply doors itself -- otherwise the previous run's
            # doors are the only ones there are. A human-saved graph
            # (auto_generated cleared by PUT) is never discarded this way.
            existing = existing.model_copy(update={"nodes": [n.model_copy() for n in draft.nodes]})
        if existing.building != draft.building or existing.floor != draft.floor:
            raise HTTPException(status_code=422, detail="Graph does not belong to this floor.")

        base_pdf = graph_store.base_pdf_path(floorplan_id)
        with fitz.open(base_pdf) as pdf:
            width, height = pdf[0].rect.width, pdf[0].rect.height

        # Preferred path: derive centerlines from the space-type report's own
        # Public Corridor geometry. It is exact, identical on every run, and
        # cannot omit a wing the way whole-floor vision tracing did, so it is
        # recomputed rather than cached (a couple of seconds, no API call).
        centerlines = (
            await run_in_threadpool(corridor_centerline.compute, str(base_pdf), str(space_type_pdf), draft)
            if space_type_pdf is not None
            else None
        )
        repair_report: dict = {}
        if centerlines:
            centerlines, repair_report = await run_in_threadpool(
                network_repair.finalize, centerlines, draft
            )
        classified_source = None
        if centerlines is None:
            # No space-type report for this floor. Ask the model the one
            # question it is reliable at -- which numbered spaces are
            # circulation -- then derive the network from those outlines with
            # the same geometry. Refused if the answer doesn't hold up.
            categories = await space_classifier.classify(raster.read_bytes(), draft)
            if categories:
                polygons = space_classifier.corridor_polygons(draft, categories)
                centerlines = await run_in_threadpool(
                    corridor_centerline.compute_from_polygons, draft, polygons
                )
                if centerlines:
                    def in_corridor(point, _polys=polygons):
                        return any(
                            corridor_heuristic._point_in_polygon(point, p) for p in _polys
                        )

                    centerlines, repair_report = await run_in_threadpool(
                        network_repair.finalize, centerlines, draft, in_corridor
                    )
                    classified_source = "classified_spaces"
        analysis = graph_store.load_gemini_analysis(floorplan_id) if graph_store.gemini_analysis_exists(floorplan_id) else None
        cached = False
        if centerlines:
            analysis = {
                "schema_version": 2,
                "task": "passage_centerlines",
                "status": "deterministic",
                "routing_source": classified_source or "space_type_centerlines",
                "floorplan": {"building": draft.building, "floor": draft.floor},
                "model": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "raster_sha256": raster_hash,
                "space_type_sha256": space_type_hash,
                "coordinate_system": {"type": "pdf_points", "pdf_bounds": [0, 0, width, height]},
                "suggestions": corridor_centerline.as_suggestions(centerlines, width, height),
            }
        else:
            cached = bool(analysis and analysis.get("schema_version") == 2
                          and analysis.get("task") == "passage_centerlines"
                          and analysis.get("accepted_for_routing") is True
                          and analysis.get("model") == settings.gemini_model
                          and analysis.get("raster_sha256") == raster_hash
                          and analysis.get("space_type_sha256") == space_type_hash
                          and not request.refresh)
            if not cached:
                try:
                    analysis = await gemini_vision.analyze_floorplan(
                        raster, draft, width, height, space_type_pdf_path=space_type_pdf,
                        base_pdf_path=base_pdf,
                    )
                except gemini_vision.GeminiVisionError as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
        assert analysis is not None

        # Check again after the remote call; don't apply old-image coordinates
        # over a replacement upload or overwrite an intervening human save.
        def unchanged() -> bool:
            current = graph_store.load_graph(floorplan_id) if graph_store.graph_exists(floorplan_id) else None
            return hashlib.sha256(raster.read_bytes()).hexdigest() == raster_hash and current == stored

        if not unchanged():
            raise HTTPException(status_code=409, detail="Floorplan changed during tracing. Reload and retry.")
        try:
            result = await run_in_threadpool(
                passage_graph.build_passage_graph, draft, existing, analysis["suggestions"],
                width, height, analysis.get("coordinate_system"),
                "space_type_centerlines" if centerlines else "gemini_pathways",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        room_count = len([node for node in existing.nodes if node.type == "room"])
        connected_count = result.report["connected_room_count"]
        required_count = max(1, round(room_count * 0.7))
        if room_count and connected_count < required_count:
            source = "The corridor centerlines" if centerlines else "Gemini's passage lines"
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{source} reached only {connected_count} of {room_count} rooms; "
                    f"at least {required_count} are required. Retrace the whole floor."
                ),
            )
        if not unchanged():
            raise HTTPException(status_code=409, detail="Floorplan changed during building. Reload and retry.")
        analysis = {**analysis, "accepted_for_routing": True}
        graph_store.save_gemini_analysis(floorplan_id, analysis)
        graph_store.backup_graph(floorplan_id)
        saved = graph_store.save_graph(floorplan_id, result.graph)
        return {
            "saved": True, "graph": saved.model_dump(), "report": result.report,
            "model": analysis["model"], "created_at": analysis["created_at"],
            "used_cached_analysis": cached, "usage": analysis.get("usage", {}),
            "passage_source": analysis.get("routing_source", "gemini_pathways"),
            "network_repair": repair_report,
        }
