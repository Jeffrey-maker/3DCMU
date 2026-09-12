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


def test_a_room_with_two_doors_is_not_used_as_a_shortcut():
    """Walking through someone's office to save a few feet is not a route.
    A room is only ever an endpoint; the detour via the corridor must win."""
    from app.models.graph import Edge, FloorGraph, Node
    from app.services import pathfinding

    def node(i, t, x, y):
        return Node(id=i, building="WEH", floor=4, x=float(x), y=float(y), type=t)

    # Two corridor doors either side of room R. Through R is short (20),
    # around the corridor is long (100) -- but through R is not allowed.
    nodes = [
        node("start", "room", 0, 0), node("d1", "door", 10, 0),
        node("R", "room", 20, 0), node("d2", "door", 30, 0),
        node("end", "room", 40, 0),
        node("c1", "corridor", 10, 50), node("c2", "corridor", 30, 50),
    ]
    edges = [
        Edge(id="e1", from_node="start", to_node="d1", weight=1, type="door"),
        Edge(id="e2", from_node="d1", to_node="R", weight=10, type="door"),
        Edge(id="e3", from_node="R", to_node="d2", weight=10, type="door"),
        Edge(id="e4", from_node="d2", to_node="end", weight=1, type="door"),
        Edge(id="e5", from_node="d1", to_node="c1", weight=50, type="hallway"),
        Edge(id="e6", from_node="c1", to_node="c2", weight=50, type="hallway"),
        Edge(id="e7", from_node="c2", to_node="d2", weight=50, type="hallway"),
    ]
    graph = FloorGraph(building="WEH", floor=4, nodes=nodes, edges=edges)

    result = pathfinding.shortest_path(graph, "start", "end")

    assert result is not None
    visited = [s.node.id for s in result.steps]
    assert "R" not in visited, "must not cut through the room"
    assert "c1" in visited and "c2" in visited, "should go round via the corridor"


def test_a_room_endpoint_is_still_reachable():
    from app.models.graph import Edge, FloorGraph, Node
    from app.services import pathfinding

    nodes = [
        Node(id="a", building="W", floor=1, x=0, y=0, type="room"),
        Node(id="d", building="W", floor=1, x=5, y=0, type="door"),
        Node(id="b", building="W", floor=1, x=10, y=0, type="room"),
    ]
    edges = [
        Edge(id="1", from_node="a", to_node="d", weight=5, type="door"),
        Edge(id="2", from_node="d", to_node="b", weight=5, type="door"),
    ]
    result = pathfinding.shortest_path(FloorGraph(building="W", floor=1, nodes=nodes, edges=edges), "a", "b")
    assert result is not None and [s.node.id for s in result.steps] == ["a", "d", "b"]


def test_vertical_links_match_shafts_by_room_number_not_coordinates():
    """Floors are drawn on different scales and origins, so the same lift sits
    at different coordinates on each sheet. The numbering carries the truth:
    4001A and 5001A are one shaft."""
    from app.models.graph import FloorGraph, Node
    from app.services import multi_floor

    def lift(floor, label, x, y):
        return Node(id=f"WEH-{floor}-{label}", building="WEH", floor=floor,
                    x=float(x), y=float(y), type="elevator", label=label)

    four = FloorGraph(building="WEH", floor=4, nodes=[lift(4, "4001A", 546, 208)])
    five = FloorGraph(building="WEH", floor=5, nodes=[lift(5, "5001A", 541, 390)])

    links = multi_floor.vertical_links([four, five])

    assert len(links) == 1
    assert {links[0].from_node, links[0].to_node} == {"WEH-4-4001A", "WEH-5-5001A"}
    assert links[0].type == "elevator"


def test_vertical_links_skip_shafts_that_do_not_line_up():
    from app.models.graph import FloorGraph, Node
    from app.services import multi_floor

    four = FloorGraph(building="WEH", floor=4, nodes=[
        Node(id="a", building="WEH", floor=4, x=0, y=0, type="stair", label="4676"),
    ])
    five = FloorGraph(building="WEH", floor=5, nodes=[
        Node(id="b", building="WEH", floor=5, x=0, y=0, type="stair", label="5426"),
    ])
    assert multi_floor.vertical_links([four, five]) == []


def test_position_key_requires_the_label_to_start_with_its_floor():
    from app.models.graph import Node
    from app.services import multi_floor

    ok = Node(id="x", building="W", floor=4, x=0, y=0, type="stair", label="4129")
    odd = Node(id="y", building="W", floor=4, x=0, y=0, type="stair", label="FMS4-1")
    assert multi_floor.position_key(ok) == "129"
    assert multi_floor.position_key(odd) is None
