from app.models.graph import FloorDraft, Node, WallSegment
from app.services.corridor_heuristic import generate_auto_graph
from app.services.pathfinding import shortest_path


def _room(id_, x, y, label):
    return Node(id=id_, building="B", floor=1, x=x, y=y, type="room", label=label)


def _curve(x, y):
    return WallSegment(type="curve", points=[(x, y)])


def _all_pairs_connected(graph, rooms) -> bool:
    return all(
        shortest_path(graph, a.id, b.id) is not None
        for a in rooms
        for b in rooms
        if a.id != b.id
    )


def test_generates_fully_connected_graph_from_detected_doors():
    rooms = [_room("A", 0, 0, "A"), _room("B", 100, 0, "B"), _room("C", 200, 0, "C")]
    raw_geometry = [_curve(5, 0), _curve(95, 0), _curve(195, 0)]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    assert graph.auto_generated is True
    assert _all_pairs_connected(graph, rooms)


def test_nearby_door_curve_fragments_cluster_into_one_door_node():
    rooms = [_room("A", 0, 0, "A"), _room("B", 100, 0, "B")]
    # Two curve fragments 3pt apart near B -- e.g. the arc plus a door-leaf
    # line -- must merge into a single door, not be double-counted.
    raw_geometry = [_curve(5, 0), _curve(97, 0), _curve(100, 0)]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    door_nodes = [n for n in graph.nodes if n.type == "door"]
    assert len(door_nodes) == 2


def test_falls_back_to_direct_room_mst_when_no_doors_detected():
    rooms = [_room("A", 0, 0, "A"), _room("B", 100, 0, "B"), _room("C", 200, 0, "C")]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=[])

    graph = generate_auto_graph(draft)

    assert all(n.type == "room" for n in graph.nodes)  # no door nodes fabricated
    assert _all_pairs_connected(graph, rooms)


def test_single_room_produces_no_edges_without_error():
    rooms = [_room("A", 0, 0, "A")]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=[])

    graph = generate_auto_graph(draft)

    assert [n.id for n in graph.nodes] == ["A"]
    assert graph.edges == []


def test_every_room_gets_exactly_one_door_edge():
    rooms = [_room("A", 0, 0, "A"), _room("B", 100, 0, "B"), _room("C", 200, 0, "C")]
    raw_geometry = [_curve(5, 0), _curve(95, 0), _curve(195, 0)]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    door_edges = [e for e in graph.edges if e.type == "door"]
    assert len(door_edges) == 3
    assert {e.from_node for e in door_edges} == {"A", "B", "C"}


def test_hallway_edges_route_around_a_wall_instead_of_through_it():
    """The exact bug reported against real Wean Hall data: a blind MST would
    connect A's and B's doors with a straight line even though a wall sits
    directly between them, cutting through a room. With C offering a clear
    detour, the wall-aware MST must prefer routing through C over crossing
    the wall directly."""
    rooms = [_room("A", 0, 0, "A"), _room("B", 100, 0, "B"), _room("C", 50, 60, "C")]
    raw_geometry = [
        _curve(5, 0),  # A's door
        _curve(95, 0),  # B's door
        _curve(50, 55),  # C's door
        WallSegment(type="line", points=[(50, -20), (50, 20)]),  # blocks the direct A-B line
    ]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    hallway_edges = [e for e in graph.edges if e.type == "hallway"]
    door_a, door_b = "B-1-door-0", "B-1-door-1"  # assigned in room-processing order: A, B, C
    assert not any({e.from_node, e.to_node} == {door_a, door_b} for e in hallway_edges)
    assert _all_pairs_connected(graph, rooms)


def test_hallway_mst_falls_back_to_crossing_a_wall_if_no_detour_exists():
    """Full connectivity is a hard requirement. Here the candidate-selection
    MST has no wall-respecting straight line to pick between the two doors,
    but the grid pathfinder is smarter than that pre-filter -- since the
    blocking wall is finite, it finds a real (longer) detour around it
    rather than needing to fall back to a wall-crossing straight edge. The
    connectivity guarantee is what's under test either way."""
    rooms = [_room("A", 0, 0, "A"), _room("B", 100, 0, "B")]
    raw_geometry = [
        _curve(5, 0),
        _curve(95, 0),
        WallSegment(type="line", points=[(50, -50), (50, 50)]),  # blocks the only possible connection
    ]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    assert _all_pairs_connected(graph, rooms)


def test_materializes_corridor_waypoints_for_a_path_that_must_bend():
    """The real payoff of grid-based materialization: a connection that has
    to bend around an obstacle is represented as real intermediate corridor
    nodes tracing the actual walkable route, not a single straight edge
    that would cut through the wall."""
    rooms = [_room("A", 0, 0, "A"), _room("B", 100, 0, "B")]
    raw_geometry = [
        _curve(5, 0),
        _curve(95, 0),
        # A wall between the doors, open above y=20 -- forces a real bend.
        WallSegment(type="line", points=[(50, -30), (50, 20)]),
    ]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    waypoints = [n for n in graph.nodes if n.type == "corridor"]
    assert len(waypoints) > 0
    assert _all_pairs_connected(graph, rooms)


def test_does_not_fabricate_a_wall_crossing_edge_when_truly_enclosed():
    """An unresolved connection is safer than displaying a route through a
    wall. The editor can surface and repair disconnected CAD edge cases."""
    rooms = [_room("A", 0, 0, "A"), _room("B", 50, 50, "B")]
    raw_geometry = [
        _curve(5, 0),
        _curve(50, 50),
        # A closed box fully encloses B's door with no gap at all.
        WallSegment(type="line", points=[(30, 30), (70, 30)]),
        WallSegment(type="line", points=[(70, 30), (70, 70)]),
        WallSegment(type="line", points=[(70, 70), (30, 70)]),
        WallSegment(type="line", points=[(30, 70), (30, 30)]),
    ]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    assert shortest_path(graph, "A", "B") is None


def test_rejects_an_implausibly_long_room_to_door_detour():
    """A room must not escape around the outside of a building just to reach
    a geometrically nearby but wall-separated door."""
    rooms = [_room("A", 0, 0, "A"), _room("B", 200, 0, "B")]
    raw_geometry = [
        _curve(195, 0),
        WallSegment(type="line", points=[(100, -100), (100, 100)]),
    ]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    assert shortest_path(graph, "A", "B") is None


def test_routes_around_furniture_in_a_public_passage():
    """Open public space is walkable, but a desk/table outline within it is
    a real obstacle and the corridor path must bend around it."""
    rooms = [_room("A", 0, 0, "A"), _room("B", 100, 0, "B")]
    raw_geometry = [
        WallSegment(type="curve", points=[(5, 0)], layer="ARCH|A-DOOR"),
        WallSegment(type="curve", points=[(95, 0)], layer="ARCH|A-DOOR"),
        WallSegment(type="line", points=[(50, -5), (50, 5)], layer="ARCH|A-FURN"),
    ]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    waypoints = [n for n in graph.nodes if n.type == "corridor"]
    assert len(waypoints) > 0
    assert _all_pairs_connected(graph, rooms)


def test_ignores_annotation_layers_as_navigation_barriers():
    rooms = [_room("A", 0, 0, "A"), _room("B", 100, 0, "B")]
    raw_geometry = [
        WallSegment(type="curve", points=[(5, 0)], layer="ARCH|A-DOOR"),
        WallSegment(type="curve", points=[(95, 0)], layer="ARCH|A-DOOR"),
        WallSegment(type="line", points=[(50, -5), (50, 5)], layer="G-ANNO-NOTE"),
    ]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    waypoints = [n for n in graph.nodes if n.type == "corridor"]
    assert waypoints == []
    assert _all_pairs_connected(graph, rooms)


def test_reconstructs_exterior_space_boundary_polygon():
    rooms = [_room("A", 10, 10, "A"), _room("B", 90, 10, "B")]
    raw_geometry = [
        WallSegment(type="curve", points=[(10, 20)], layer="ARCH|A-DOOR"),
        WallSegment(type="curve", points=[(90, 20)], layer="ARCH|A-DOOR"),
        WallSegment(type="line", points=[(0, 0), (100, 0)], layer="A-SPAC-EXTR"),
        WallSegment(type="line", points=[(100, 0), (100, 100)], layer="A-SPAC-EXTR"),
        WallSegment(type="line", points=[(100, 100), (0, 100)], layer="A-SPAC-EXTR"),
        WallSegment(type="line", points=[(0, 100), (0, 0)], layer="A-SPAC-EXTR"),
    ]

    from app.services.corridor_heuristic import exterior_space_polygon

    polygon = exterior_space_polygon(raw_geometry)
    assert polygon is not None
    assert set(polygon) == {(0, 0), (100, 0), (100, 100), (0, 100)}


def test_prefers_the_real_door_layer_when_present():
    """Once real CAD layer data is available, door detection should use the
    actual "ARCH|A-DOOR" layer rather than every curve on the page -- a
    curve tagged as something else (e.g. a stair tread) must not be
    mistaken for a door."""
    rooms = [_room("A", 0, 0, "A"), _room("B", 100, 0, "B")]
    raw_geometry = [
        WallSegment(type="curve", points=[(5, 0)], layer="ARCH|A-DOOR"),
        WallSegment(type="curve", points=[(95, 0)], layer="ARCH|A-DOOR"),
        # A stair-tread curve, far from both real doors -- would form its own
        # distinct (bogus) third door cluster if the layer weren't respected.
        WallSegment(type="curve", points=[(50, 200)], layer="ARCH|A-FLOR-STRS"),
    ]
    draft = FloorDraft(building="B", floor=1, nodes=rooms, raw_geometry=raw_geometry)

    graph = generate_auto_graph(draft)

    door_nodes = [n for n in graph.nodes if n.type == "door"]
    assert len(door_nodes) == 2  # the stair curve wasn't clustered in as a third door
