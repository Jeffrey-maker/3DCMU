from fastapi import APIRouter, HTTPException, Query

from app.llm.client import get_reasoning_client
from app.models.graph import FloorGraph
from app.services import (
    corridor_heuristic,
    directions,
    graph_store,
    multi_floor,
    passage_graph,
    pathfinding,
)
from app.services.geometry import WallIndex

router = APIRouter(tags=["route"])


def _find_graph_containing(node_id: str) -> tuple[str, FloorGraph] | tuple[None, None]:
    """Scan saved graphs for the one containing this node id. Linear over
    all floorplans — fine at today's scale (a handful of floors); milestone
    5's floor-merge work would replace this with a real index if needed."""
    for fp_id in graph_store.list_floorplans():
        if not graph_store.graph_exists(fp_id):
            continue
        graph = graph_store.load_graph(fp_id)
        if any(n.id == node_id for n in graph.nodes):
            return fp_id, graph
    return None, None


@router.get("/api/route")
def get_route(from_: str = Query(..., alias="from"), to: str = Query(...)):
    from_fp, from_graph = _find_graph_containing(from_)
    to_fp, _ = _find_graph_containing(to)

    if from_graph is None:
        raise HTTPException(
            status_code=404, detail=f"Start node '{from_}' not found in any saved graph"
        )
    if to_fp is None:
        raise HTTPException(
            status_code=404, detail=f"Destination node '{to}' not found in any saved graph"
        )
    if from_fp != to_fp:
        # Different floors: route over every floor at once so the search can
        # weigh taking the stairs beside you against the lift down the hall.
        routing_graph, _ = multi_floor.build_routing_graph(graph_store.list_floorplans())
        if not any(n.id == to for n in routing_graph.nodes):
            raise HTTPException(
                status_code=404, detail=f"Destination node '{to}' is not reachable from any linked floor"
            )
    else:
        routing_graph = passage_graph.materialize_for_routing(from_graph)
    result = pathfinding.shortest_path(routing_graph, from_, to)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No path found between these nodes — check the graph is fully connected",
        )

    # The stored graph's auto-generated corridor edges are built pairwise
    # (each door connects to its MST-selected neighbor independently), so a
    # resolved route can pass through several short edges in a row that are
    # all actually one straight, open-corridor walk. Re-simplify the whole
    # resolved route globally against the floor's real walls before turning
    # it into directions -- see pathfinding.simplify_route for why a
    # per-edge view can't catch this on its own.
    try:
        if from_fp != to_fp:
            # A multi-floor route must not be globally straightened: the two
            # floors' drawings share no coordinate frame, so a "shortcut"
            # between points on different sheets is meaningless.
            steps = result.steps
        elif from_graph.routing_source in passage_graph.PASSAGE_ROUTING_SOURCES:
            # Keep the exact line network and door transitions. A global RDP
            # shortcut would leave the passage line even without crossing walls.
            steps = result.steps
        else:
            draft = graph_store.load_draft(from_fp)
            wall_index = WallIndex(corridor_heuristic.wall_segments(draft.raw_geometry))
            steps = pathfinding.simplify_route(result.steps, wall_index)
    except Exception:
        steps = result.steps  # simplification is a presentation nicety, never block a real route on it

    deterministic_steps = directions.generate_directions(steps)
    deterministic_texts = [s.text for s in deterministic_steps]

    context = {
        "path": [
            {
                "label": s.node.label,
                "room_type": s.node.room_type,
                "department": s.node.department,
            }
            for s in steps
        ]
    }
    try:
        phrased = get_reasoning_client().phrase_directions(deterministic_texts, context)
    except Exception:
        phrased = None

    return {
        "path": [
            {
                "node_id": s.node.id,
                "label": s.node.label,
                "type": s.node.type,
                "x": s.node.x,
                "y": s.node.y,
                "building": s.node.building,
                "floor": s.node.floor,
            }
            for s in steps
        ],
        "total_weight": result.total_weight,
        "directions_source": "k2_horizon" if phrased else "deterministic",
        "deterministic_directions": [
            {"text": s.text, "node_id": s.node_id} for s in deterministic_steps
        ],
        "phrased_directions": phrased,
    }
