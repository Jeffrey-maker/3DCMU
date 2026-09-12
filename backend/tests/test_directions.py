import pytest

from app.models.graph import Edge, Node
from app.services.directions import _bearing_turn_deg, _classify_turn, generate_directions
from app.services.pathfinding import PathStep


def _node(id_, x, y, type_="corridor", floor=1, label=None):
    return Node(id=id_, building="B", floor=floor, x=x, y=y, type=type_, label=label)


def test_east_then_south_is_a_right_turn():
    """Pins the coordinate-convention sign check from the build plan: PDF
    space has y increasing downward, so walking east then turning to face
    south (toward larger y) is a clockwise/right turn on a north-up map, and
    must yield a POSITIVE turn_deg. If this ever flips, it must be a
    deliberate, reviewed change -- not a silent regression that quietly
    starts telling people to turn the wrong way."""
    turn_deg = _bearing_turn_deg(v_in=(1, 0), v_out=(0, 1))
    assert turn_deg == pytest.approx(90.0)
    assert _classify_turn(turn_deg) == "turn right"


def test_east_then_north_is_a_left_turn():
    turn_deg = _bearing_turn_deg(v_in=(1, 0), v_out=(0, -1))
    assert turn_deg == pytest.approx(-90.0)
    assert _classify_turn(turn_deg) == "turn left"


def test_continuing_east_is_straight():
    turn_deg = _bearing_turn_deg(v_in=(1, 0), v_out=(1, 0))
    assert turn_deg == pytest.approx(0.0)
    assert _classify_turn(turn_deg) == "continue straight"


def test_reversing_direction_is_turn_around():
    turn_deg = _bearing_turn_deg(v_in=(1, 0), v_out=(-1, 0))
    assert abs(turn_deg) == pytest.approx(180.0)
    assert _classify_turn(turn_deg) == "turn around"


def test_degenerate_vector_returns_none():
    assert _bearing_turn_deg(v_in=(0, 0), v_out=(1, 0)) is None
    assert _bearing_turn_deg(v_in=(1, 0), v_out=(0, 0)) is None


def test_straight_line_path_end_to_end():
    steps = [
        PathStep(node=_node("A", 0, 0, label="A"), edge_in=None),
        PathStep(
            node=_node("B", 10, 0),
            edge_in=Edge(id="e1", from_node="A", to_node="B", weight=10, type="hallway"),
        ),
        PathStep(
            node=_node("C", 20, 0, label="C"),
            edge_in=Edge(id="e2", from_node="B", to_node="C", weight=10, type="hallway"),
        ),
    ]

    texts = [d.text for d in generate_directions(steps)]

    assert texts == [
        "Start at Room A, head toward corridor.",
        "Continue straight.",
        "Arrive at Room C.",
    ]


def test_stairs_edge_produces_take_the_stairs_instruction():
    steps = [
        PathStep(node=_node("A", 0, 0, label="A"), edge_in=None),
        PathStep(
            node=_node("STAIR1", 10, 0, type_="stair"),
            edge_in=Edge(id="e1", from_node="A", to_node="STAIR1", weight=10, type="hallway"),
        ),
        PathStep(
            node=_node("STAIR2", 10, 0, type_="stair", floor=2),
            edge_in=Edge(id="e2", from_node="STAIR1", to_node="STAIR2", weight=250, type="stairs"),
        ),
        PathStep(
            node=_node("B", 20, 0, floor=2, label="B"),
            edge_in=Edge(id="e3", from_node="STAIR2", to_node="B", weight=10, type="hallway"),
        ),
    ]

    texts = [d.text for d in generate_directions(steps)]

    assert "Take the stairs to floor 2." in texts


def test_outdoor_path_produces_exit_instruction():
    steps = [
        PathStep(node=_node("A", 0, 0, label="A"), edge_in=None),
        PathStep(
            node=_node("DOOR", 10, 0, type_="door"),
            edge_in=Edge(id="e1", from_node="A", to_node="DOOR", weight=10, type="hallway"),
        ),
        PathStep(
            node=_node("OUT", 10, 10, type_="outdoor"),
            edge_in=Edge(id="e2", from_node="DOOR", to_node="OUT", weight=10, type="outdoor_path"),
        ),
    ]

    texts = [d.text for d in generate_directions(steps)]

    assert "Exit the building." in texts


def test_single_node_path_reports_already_there():
    steps = [PathStep(node=_node("A", 0, 0, label="A"), edge_in=None)]

    result = generate_directions(steps)

    assert len(result) == 1
    assert "already at Room A" in result[0].text


def test_empty_path_returns_no_directions():
    assert generate_directions([]) == []
