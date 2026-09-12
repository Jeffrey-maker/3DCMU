"""Dijkstra shortest path over a FloorGraph.

Hand-rolled heapq implementation rather than networkx: at the ~50-100 node
scale of a single floor, a graph library buys nothing, and keeping typed
Edge objects flowing through path reconstruction lets directions.py do its
edge-type special-casing (stairs/elevator/outdoor) directly. Revisit if the
combined indoor+outdoor graph (milestone 7+) reaches thousands of nodes.

Edges are treated as undirected (a hallway/door/stair/elevator can be
traversed in either direction) — this is purely a traversal detail; the
reconstructed path still records nodes in actual walking order, so bearing
calculations in directions.py are unaffected by which direction an edge was
originally authored.
"""

import heapq
from dataclasses import dataclass
from typing import Optional

from app.models.graph import Edge, FloorGraph, Node
from app.services import navgrid
from app.services.geometry import WallIndex, distance

_UNMERGEABLE_EDGE_TYPES = {"stairs", "elevator", "outdoor_path"}


@dataclass
class PathStep:
    node: Node
    edge_in: Optional[Edge]  # edge used to arrive here from the previous step; None for the start node


@dataclass
class RouteResult:
    steps: list[PathStep]
    total_weight: float


def _adjacency(graph: FloorGraph) -> dict[str, list[tuple[str, Edge]]]:
    adj: dict[str, list[tuple[str, Edge]]] = {}
    for edge in graph.edges:
        adj.setdefault(edge.from_node, []).append((edge.to_node, edge))
        adj.setdefault(edge.to_node, []).append((edge.from_node, edge))
    return adj


def shortest_path(graph: FloorGraph, start_id: str, end_id: str) -> Optional[RouteResult]:
    nodes_by_id = {n.id: n for n in graph.nodes}
    if start_id not in nodes_by_id or end_id not in nodes_by_id:
        return None

    adjacency = _adjacency(graph)
    dist: dict[str, float] = {start_id: 0.0}
    prev: dict[str, tuple[str, Edge]] = {}
    visited: set[str] = set()
    heap: list[tuple[float, str]] = [(0.0, start_id)]

    while heap:
        d, node_id = heapq.heappop(heap)
        if node_id in visited:
            continue
        visited.add(node_id)
        if node_id == end_id:
            break
        for neighbor_id, edge in adjacency.get(node_id, []):
            candidate = d + edge.weight
            if candidate < dist.get(neighbor_id, float("inf")):
                dist[neighbor_id] = candidate
                prev[neighbor_id] = (node_id, edge)
                heapq.heappush(heap, (candidate, neighbor_id))

    if end_id not in dist:
        return None

    node_ids = [end_id]
    edges_used: list[Edge] = []
    cur = end_id
    while cur != start_id:
        prev_id, edge = prev[cur]
        edges_used.append(edge)
        node_ids.append(prev_id)
        cur = prev_id
    node_ids.reverse()
    edges_used.reverse()

    steps = [PathStep(node=nodes_by_id[node_ids[0]], edge_in=None)]
    for node_id, edge in zip(node_ids[1:], edges_used):
        steps.append(PathStep(node=nodes_by_id[node_id], edge_in=edge))

    return RouteResult(steps=steps, total_weight=dist[end_id])


def simplify_route(steps: list[PathStep], wall_index: WallIndex) -> list[PathStep]:
    """Collapse a resolved multi-edge route into the fewest straight
    segments that still never cross a wall, evaluated across the WHOLE
    route at once -- not edge by edge.

    This matters because the stored graph's auto-generated corridor edges
    are built pairwise (each door connects to its MST-selected neighbor
    independently). A run of several doors that are all actually along one
    straight, open corridor still gets a separate short edge for each hop,
    and each one was only simplified against *its own* two endpoints. Only
    a global pass sees that the whole run was one straight walk, so only a
    global pass can collapse away the small zigzag a per-edge view leaves
    behind at every junction (e.g. a route that dips slightly into the
    corridor and back out again at every single door it passes, instead of
    walking straight past them).

    Never simplifies across a stairs/elevator/outdoor_path edge -- a floor
    change or building exit is a mandatory stop, not a point safe to skip
    past just because it's geometrically unobstructed.
    """
    if len(steps) <= 2:
        return steps

    points = [(s.node.x, s.node.y) for s in steps]
    mandatory = {
        i
        for i, s in enumerate(steps)
        if s.edge_in is not None and s.edge_in.type in _UNMERGEABLE_EDGE_TYPES
    }
    keep = sorted(set(navgrid.simplify_indices(points, wall_index)) | mandatory | {0, len(steps) - 1})

    simplified = [PathStep(node=steps[0].node, edge_in=None)]
    for prev_i, i in zip(keep, keep[1:]):
        prev_node = steps[prev_i].node
        node = steps[i].node
        original_edge = steps[i].edge_in
        if i == prev_i + 1:
            edge = original_edge  # no hops were merged -- keep the real edge as-is
        else:
            edge_type = original_edge.type if original_edge is not None else "hallway"
            edge = Edge(
                id=f"simplified-{prev_node.id}-{node.id}",
                from_node=prev_node.id,
                to_node=node.id,
                weight=round(distance((prev_node.x, prev_node.y), (node.x, node.y)), 2),
                type=edge_type,
            )
        simplified.append(PathStep(node=node, edge_in=edge))
    return simplified
