from app.models.graph import Node, WallSegment
from app.services import corridor_centerline as centerline
from app.services import extraction, space_type_overlay
from app.services.gemini_vision import PassageSuggestions


def _rectangle(x0: int, y0: int, x1: int, y1: int) -> set[tuple[int, int]]:
    return {(x, y) for x in range(x0, x1) for y in range(y0, y1)}


def test_thinning_reduces_a_thick_bar_to_a_one_cell_line():
    skeleton = centerline._thin(_rectangle(0, 0, 40, 9))
    assert skeleton
    # One cell per column at most: a thinned horizontal bar is a single line.
    per_column: dict[int, int] = {}
    for x, _y in skeleton:
        per_column[x] = per_column.get(x, 0) + 1
    assert max(per_column.values()) == 1
    # And it should span most of the bar's length rather than collapse to a dot.
    assert max(per_column) - min(per_column) >= 30


def test_closing_repairs_holes_punched_by_labels_drawn_over_the_fill():
    bar = _rectangle(0, 0, 40, 12)
    with_hole = bar - _rectangle(18, 4, 23, 8)
    closed = centerline._close(with_hole, centerline.CLOSE_RADIUS)
    assert _rectangle(18, 4, 23, 8) <= closed


def test_trace_keeps_a_short_link_between_junctions_but_drops_dead_end_spurs():
    # Two long horizontal runs joined by a short vertical link, plus a stub.
    skeleton = {(x, 0) for x in range(20)} | {(x, 4) for x in range(20)}
    skeleton |= {(10, 1), (10, 2), (10, 3)}  # short junction-to-junction link
    skeleton |= {(5, 1), (5, 2)}  # dead-end spur

    runs = centerline._trace(skeleton)
    cells = {cell for run in runs for cell in run}

    assert (10, 2) in cells, "short link between two junctions must survive"
    assert (5, 2) not in cells, "short dead-end spur should be pruned"


def test_simplify_collapses_a_straight_run_but_keeps_a_corner():
    straight = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0), (15.0, 0.0)]
    assert centerline._simplify(straight, 2.0) == [(0.0, 0.0), (15.0, 0.0)]

    corner = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert centerline._simplify(corner, 2.0) == corner


def test_as_suggestions_matches_the_vision_response_schema():
    lines = [[(0.0, 0.0), (612.0, 396.0)], [(612.0, 396.0), (1224.0, 792.0)]]
    payload = centerline.as_suggestions(lines, 1224.0, 792.0)

    validated = PassageSuggestions.model_validate(payload)
    assert len(validated.pathways) == 2
    assert all(p.confidence == 1.0 for p in validated.pathways)
    # Coordinates normalize to the full page, so a page corner maps to 1000.
    last = validated.pathways[1].points[-1]
    assert last.x_normalized == 1000.0
    assert last.y_normalized == 1000.0


def test_axis_fit_recovers_a_known_scale_and_offset():
    scale, offset = space_type_overlay._fit_axis([(0.0, 5.0), (10.0, 25.0), (20.0, 45.0)])
    assert round(scale, 6) == 2.0
    assert round(offset, 6) == 5.0


def _room(label: str, x: float, y: float) -> Node:
    return Node(id=f"WEH-4-{label}", building="WEH", floor=4, x=x, y=y, type="room", label=label)


def test_vertical_circulation_requires_confirming_cad_geometry(monkeypatch):
    """A stair label can land nearest some unrelated room when the real
    stairwell carries no room number; without drawn stair linework there, the
    room must stay a room rather than become a fake floor change."""
    monkeypatch.setattr(
        space_type_overlay,
        "vertical_circulation",
        lambda base, type_pdf: {"stair": [(100.0, 100.0), (500.0, 500.0)], "elevator": []},
    )
    nodes = [_room("4001", 105.0, 100.0), _room("4002", 505.0, 500.0)]
    geometry = [
        WallSegment(type="line", points=[(104.0, 101.0), (110.0, 101.0)], layer="ARCH|A-FLOR-STRS")
    ]

    result = extraction.apply_vertical_circulation(nodes, geometry, "base.pdf", "type.pdf")

    by_label = {n.label: n for n in result}
    assert by_label["4001"].type == "stair", "confirmed by nearby stair linework"
    assert by_label["4002"].type == "room", "no stair linework nearby -> left alone"


def test_vertical_circulation_never_claims_one_room_twice(monkeypatch):
    monkeypatch.setattr(
        space_type_overlay,
        "vertical_circulation",
        lambda base, type_pdf: {"stair": [(100.0, 100.0)], "elevator": [(102.0, 100.0)]},
    )
    nodes = [_room("4001", 100.0, 100.0)]
    geometry = [
        WallSegment(type="line", points=[(100.0, 100.0), (106.0, 100.0)], layer="ARCH|A-FLOR-STRS"),
        WallSegment(type="line", points=[(100.0, 100.0), (106.0, 100.0)], layer="ARCH|A-FLOR-EVTR"),
    ]

    result = extraction.apply_vertical_circulation(nodes, geometry, "base.pdf", "type.pdf")
    assert sum(1 for n in result if n.type in {"stair", "elevator"}) == 1


def test_door_ownership_assigns_a_door_to_the_rooms_whose_boundary_it_pierces():
    """Two rooms side by side sharing a wall at x=100. The door in that shared
    wall belongs to both; the door in the far wall belongs only to the right
    room -- proximity alone would happily hand it to the left one."""
    from app.services import passage_graph

    left = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    right = [(100.0, 0.0), (200.0, 0.0), (200.0, 100.0), (100.0, 100.0)]
    rooms = [_room("100", 50.0, 50.0), _room("200", 150.0, 50.0)]
    shared_door = Node(
        id="d-shared", building="WEH", floor=4, x=100.0, y=50.0, type="door"
    )
    far_door = Node(id="d-far", building="WEH", floor=4, x=200.0, y=50.0, type="door")

    room_polygon, door_polygons = passage_graph._door_ownership(
        rooms, [shared_door, far_door], [left, right]
    )

    assert room_polygon["WEH-4-100"] == 0
    assert room_polygon["WEH-4-200"] == 1
    assert door_polygons["d-shared"] == {0, 1}
    assert door_polygons["d-far"] == {1}, "far wall door must not be owned by the left room"


def test_split_subpaths_separates_two_disjoint_loops():
    """One drawing can hold several loops; chaining them into a single outline
    would produce a polygon enclosing neither room."""
    def ring(x0, y0, x1, y1):
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return list(zip(corners, corners[1:] + corners[:1]))

    polygons = extraction.split_subpaths(ring(0, 0, 10, 10) + ring(100, 100, 120, 120))

    assert len(polygons) == 2
    assert all(len(p) >= 4 for p in polygons)
    assert min(x for x, _ in polygons[0]) == 0
    assert min(x for x, _ in polygons[1]) == 100


def test_split_subpaths_keeps_one_continuous_loop_together():
    corners = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    edges = list(zip(corners, corners[1:] + corners[:1]))

    polygons = extraction.split_subpaths(edges)

    assert len(polygons) == 1


def _box(x0, y0, x1, y1):
    return [(float(x0), float(y0)), (float(x1), float(y0)), (float(x1), float(y1)), (float(x0), float(y1))]


def test_classifier_result_is_refused_when_corridors_do_not_join_up():
    """The model is only trusted when its answer holds up geometrically: real
    circulation is walkable end to end, so scattered unconnected spaces mean
    it labelled rooms, not corridors."""
    from app.models.graph import FloorDraft
    from app.services import space_classifier

    polygons = [_box(0, 0, 20, 20), _box(500, 500, 520, 520), _box(900, 100, 920, 120)]
    nodes = [
        _room("1", 10, 10), _room("2", 510, 510), _room("3", 910, 110),
    ]
    draft = FloorDraft(building="WEH", floor=4, nodes=nodes, room_polygons=polygons)
    categories = {"1": "corridor", "2": "corridor", "3": "corridor"}

    space_classifier.MAX_CORRIDOR_COMPONENTS = 2
    try:
        assert space_classifier.corridor_polygons(draft, categories) == []
    finally:
        space_classifier.MAX_CORRIDOR_COMPONENTS = 4


def test_bridging_pulls_in_the_connector_the_model_skipped():
    """Two corridor wings with an unlabelled link between them: the link is
    the only outline touching both, so it must be adopted."""
    from app.models.graph import FloorDraft
    from app.services import space_classifier

    left = _box(0, 0, 100, 20)
    link = _box(100, 0, 130, 20)
    right = _box(130, 0, 230, 20)
    far = _box(600, 600, 620, 620)
    polygons = [left, link, right, far]
    nodes = [_room("L", 50, 10), _room("K", 115, 10), _room("R", 180, 10), _room("F", 610, 610)]
    draft = FloorDraft(building="WEH", floor=4, nodes=nodes, room_polygons=polygons)

    chosen = space_classifier.corridor_polygons(draft, {"L": "corridor", "R": "corridor"})

    assert link in chosen, "the connector between two wings should be adopted"
    assert far not in chosen, "an unrelated distant outline must not be"


def test_compute_from_polygons_returns_none_without_corridors():
    from app.models.graph import FloorDraft

    draft = FloorDraft(building="WEH", floor=4)
    assert centerline.compute_from_polygons(draft, []) is None


def _wall(x0, y0, x1, y1):
    return WallSegment(type="line", points=[(float(x0), float(y0)), (float(x1), float(y1))],
                       layer="ARCH|A-WALL")


def test_prune_splits_a_line_where_it_clips_a_wall():
    """A coarse mask can leave a spine grazing an obstacle. Left in, that
    segment is rejected later and takes the network's connectivity with it,
    so it is cut here instead."""
    from app.models.graph import FloorDraft
    from app.services import network_repair

    draft = FloorDraft(building="WEH", floor=4, raw_geometry=[_wall(50, -10, 50, 10)])
    line = [(0.0, 0.0), (40.0, 0.0), (60.0, 0.0), (100.0, 0.0)]

    kept = network_repair.prune([line], draft)

    flattened = [p for run in kept for p in run]
    assert (40.0, 0.0) in flattened and (60.0, 0.0) in flattened
    assert all(
        not (a == (40.0, 0.0) and b == (60.0, 0.0))
        for run in kept for a, b in zip(run, run[1:])
    ), "the segment crossing the wall must not survive"


def test_bridge_joins_two_pieces_through_a_real_gap():
    """Two corridor stubs either side of a doorway-width gap in a wall: the
    grid can walk it, so the network should come back as one piece."""
    from app.models.graph import FloorDraft
    from app.services import network_repair

    # A wall along x=50 with an opening between y=-6 and y=6.
    draft = FloorDraft(building="WEH", floor=4, raw_geometry=[
        _wall(50, 6, 50, 120), _wall(50, -120, 50, -6),
    ])
    left = [(0.0, 0.0), (40.0, 0.0)]
    right = [(60.0, 0.0), (100.0, 0.0)]

    assert len(network_repair._components([left, right])) == 2
    joined, added = network_repair.bridge([left, right], draft)

    assert added == 1
    assert len(network_repair._components(joined)) == 1


def test_bridge_refuses_to_invent_a_link_through_a_solid_wall():
    from app.models.graph import FloorDraft
    from app.services import network_repair

    draft = FloorDraft(building="WEH", floor=4, raw_geometry=[_wall(50, -400, 50, 400)])
    joined, added = network_repair.bridge([[(0.0, 0.0), (40.0, 0.0)], [(60.0, 0.0), (100.0, 0.0)]], draft)

    assert added == 0
    assert len(network_repair._components(joined)) == 2
