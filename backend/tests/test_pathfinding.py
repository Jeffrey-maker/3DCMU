from app.models.graph import Edge, FloorGraph, Node
from app.services.pathfinding import shortest_path


def _node(id_, x, y, type_="corridor"):
    return Node(id=id_, building="B", floor=1, x=x, y=y, type=type_)


def _edge(id_, a, b, weight, type_="hallway"):
    return Edge(id=id_, from_node=a, to_node=b, weight=weight, type=type_)


def test_shortest_path_picks_lower_weight_route_over_direct_expensive_edge():
    nodes = [_node("A", 0, 0), _node("B", 10, 0), _node("C", 0, 10), _node("D", 10, 10)]
    edges = [
        _edge("e1", "A", "B", 100),
        _edge("e2", "A", "C", 1),
        _edge("e3", "C", "D", 1),
        _edge("e4", "D", "B", 1),
    ]
    graph = FloorGraph(building="B", floor=1, nodes=nodes, edges=edges)

    result = shortest_path(graph, "A", "B")

    assert result is not None
    assert [s.node.id for s in result.steps] == ["A", "C", "D", "B"]
    assert result.total_weight == 3


def test_edges_are_traversable_in_either_direction():
    nodes = [_node("A", 0, 0), _node("B", 10, 0)]
    edges = [_edge("e1", "B", "A", 5)]  # authored B->A; traveling A->B must still work
    graph = FloorGraph(building="B", floor=1, nodes=nodes, edges=edges)

    result = shortest_path(graph, "A", "B")

    assert result is not None
    assert [s.node.id for s in result.steps] == ["A", "B"]
    assert result.total_weight == 5


def test_no_path_between_disconnected_components_returns_none():
    nodes = [_node("A", 0, 0), _node("B", 10, 0)]
    graph = FloorGraph(building="B", floor=1, nodes=nodes, edges=[])

    assert shortest_path(graph, "A", "B") is None


def test_unknown_node_id_returns_none():
    nodes = [_node("A", 0, 0)]
    graph = FloorGraph(building="B", floor=1, nodes=nodes, edges=[])

    assert shortest_path(graph, "A", "does-not-exist") is None


def test_trivial_path_when_start_equals_end():
    nodes = [_node("A", 0, 0)]
    graph = FloorGraph(building="B", floor=1, nodes=nodes, edges=[])

    result = shortest_path(graph, "A", "A")

    assert result is not None
    assert [s.node.id for s in result.steps] == ["A"]
    assert result.total_weight == 0
