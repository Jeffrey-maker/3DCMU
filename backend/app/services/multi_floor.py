"""Join several floors into one graph so a route can change level.

Floors cannot be aligned by coordinates: each sheet is plotted on its own
scale and origin, so the same lift shaft sits at y=208 on one floor's
drawing and y=390 on the next, and the building outlines differ in both
size and shape.

The room numbering says it instead. CMU numbers a space by its floor
followed by a position that stays the same up the building, so stair 4129
and stair 5129 are the same stairwell, and lift 4001A and 5001A are the
same car. Matching vertical circulation on that shared suffix needs no
geometry at all, and it only ever links spaces both floors already agree
are stairs or lifts.
"""

from app.config import settings
from app.models.graph import Edge, FloorGraph, Node
from app.services import graph_store, passage_graph

VERTICAL_TYPES = {"stair": "stairs", "elevator": "elevator"}
VERTICAL_WEIGHTS = {
    "stairs": settings.stair_edge_weight,
    "elevator": settings.elevator_edge_weight,
}


def position_key(node: Node) -> str | None:
    """The part of a room number that identifies the shaft, not the floor.

    "4129" on floor 4 and "5129" on floor 5 both reduce to "129". Returns
    None when the label doesn't start with its own floor number, because
    then the convention doesn't hold and guessing would invent a link.
    """
    if node.label is None:
        return None
    prefix = str(node.floor)
    if not node.label.startswith(prefix) or len(node.label) <= len(prefix):
        return None
    return node.label[len(prefix):]


def vertical_links(graphs: list[FloorGraph]) -> list[Edge]:
    """Edges joining the same shaft on one floor to the next.

    Only consecutive floors are linked: a lift obviously serves the whole
    building, but modelling it as one hop per floor keeps the cost honest
    (three floors up should cost more than one) and lets the search decide
    between the stairs next door and the lift down the corridor.
    """
    by_key: dict[tuple[str, str], list[Node]] = {}
    for graph in graphs:
        for node in graph.nodes:
            if node.type not in VERTICAL_TYPES:
                continue
            key = position_key(node)
            if key is not None:
                by_key.setdefault((node.type, key), []).append(node)

    edges: list[Edge] = []
    for (node_type, key), nodes in by_key.items():
        ordered = sorted(nodes, key=lambda n: n.floor)
        edge_type = VERTICAL_TYPES[node_type]
        for lower, upper in zip(ordered, ordered[1:]):
            if upper.floor - lower.floor != 1:
                continue
            edges.append(
                Edge(
                    id=f"vertical-{edge_type}-{lower.id}-{upper.id}",
                    from_node=lower.id,
                    to_node=upper.id,
                    weight=VERTICAL_WEIGHTS[edge_type],
                    type=edge_type,
                )
            )
    return edges


def build_routing_graph(floorplan_ids: list[str]) -> tuple[FloorGraph, dict[str, int]]:
    """One graph spanning the given floors, ready for Dijkstra.

    Each floor is materialized exactly as single-floor routing would do it,
    then the vertical links are added on top.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []
    floors: list[FloorGraph] = []
    node_floor: dict[str, int] = {}

    for fp_id in sorted(floorplan_ids):
        if not graph_store.graph_exists(fp_id):
            continue
        graph = passage_graph.materialize_for_routing(graph_store.load_graph(fp_id))
        floors.append(graph)
        nodes.extend(graph.nodes)
        edges.extend(graph.edges)
        for node in graph.nodes:
            node_floor[node.id] = graph.floor

    if not floors:
        return FloorGraph(building="", floor=0), {}

    edges.extend(vertical_links(floors))
    first = floors[0]
    return (
        FloorGraph(
            building=first.building,
            floor=first.floor,
            nodes=nodes,
            edges=edges,
            routing_source=first.routing_source,
            page_width=first.page_width,
            page_height=first.page_height,
        ),
        node_floor,
    )
