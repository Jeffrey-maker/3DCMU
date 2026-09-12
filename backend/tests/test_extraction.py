import json
from pathlib import Path

from app.models.graph import Node, WallSegment
from app.services import extraction

WEAN_HALL_PDF = str(
    Path(__file__).resolve().parent.parent.parent
    / "floor_plan"
    / "wean_hall"
    / "WEH-1-ESIM-Base.pdf"
)
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "expected_wean_hall_1_draft.json"


def test_extraction_matches_validated_baseline():
    """Regression guard: a PyMuPDF version bump or a refactor that silently
    changes extraction behavior should fail this test immediately, not get
    discovered later against real data."""
    draft = extraction.extract_floor_draft(WEAN_HALL_PDF, building="WEH", floor=1)
    expected = json.loads(FIXTURE.read_text())

    lines = sum(1 for seg in draft.raw_geometry if seg.type == "line")
    curves = sum(1 for seg in draft.raw_geometry if seg.type == "curve")

    assert len(draft.nodes) == expected["room_count"]
    assert lines == expected["line_segment_count"]
    assert curves == expected["curve_count"]
    assert sorted(n.label for n in draft.nodes) == expected["labels"]


def test_room_nodes_are_well_formed():
    draft = extraction.extract_floor_draft(WEAN_HALL_PDF, building="WEH", floor=1)
    for node in draft.nodes:
        assert node.id == f"WEH-1-{node.label}"
        assert node.building == "WEH"
        assert node.floor == 1
        # Lift shafts are typed from the CAD layer, so not every extracted
        # space is a plain room any more.
        assert node.type in {"room", "stair", "elevator"}
        assert isinstance(node.x, float)
        assert isinstance(node.y, float)


def test_is_room_number_filters_area_labels():
    assert extraction.is_room_number("1004")
    assert extraction.is_room_number("1001A")
    assert not extraction.is_room_number("130sf")
    assert not extraction.is_room_number("1,431sf")
    assert not extraction.is_room_number("sf")


def _node(label, x, y):
    return Node(id=f"B-1-{label}", building="B", floor=1, x=x, y=y, type="room", label=label)


def test_leader_line_correction_follows_a_short_diagonal_tick_to_its_target():
    """Small rooms too tiny to fit their label get the label pulled outside
    via a short diagonal leader line ending in a dot at the room's real
    position -- confirmed against 6 real cases on Wean Hall 1 (e.g. room
    1007, a narrow closet). The far endpoint of that tick, not the label
    centroid, is the room's true position."""
    room = _node("1007", 75.1, 475.6)  # raw label centroid, actually outside the real room
    raw_geometry = [
        WallSegment(type="line", points=[(91.4, 479.8), (109.0, 486.2)]),  # the leader tick
        WallSegment(type="line", points=[(62.7, 479.8), (87.4, 479.8)]),  # the label's underline (axis-aligned, must be ignored)
    ]

    corrected = extraction.apply_leader_line_corrections([room], raw_geometry)

    assert (corrected[0].x, corrected[0].y) == (109.0, 486.2)


def test_leader_line_correction_leaves_normal_rooms_untouched():
    room = _node("2000", 300.0, 300.0)
    raw_geometry = [
        WallSegment(type="line", points=[(0, 0), (500, 0)]),  # ordinary axis-aligned wall, far away
    ]

    corrected = extraction.apply_leader_line_corrections([room], raw_geometry)

    assert (corrected[0].x, corrected[0].y) == (300.0, 300.0)


def test_leader_line_correction_ignores_long_diagonal_segments():
    """A long diagonal line (e.g. an angled wall) must not be mistaken for a
    short leader tick."""
    room = _node("2001", 0.0, 0.0)
    raw_geometry = [
        WallSegment(type="line", points=[(2.0, 2.0), (200.0, 200.0)]),  # diagonal but too long
    ]

    corrected = extraction.apply_leader_line_corrections([room], raw_geometry)

    assert (corrected[0].x, corrected[0].y) == (0.0, 0.0)


def test_leader_line_correction_on_real_wean_hall_data():
    """Pins the exact correction for a known real case so a future change to
    the detection thresholds is a visible, deliberate decision."""
    draft = extraction.extract_floor_draft(WEAN_HALL_PDF, building="WEH", floor=1)
    room_1007 = next(n for n in draft.nodes if n.label == "1007")
    assert (room_1007.x, room_1007.y) == (109.0, 486.2)


def test_one_leader_tick_cannot_move_two_nearby_room_labels():
    rooms = [_node("4001", 0.0, 0.0), _node("4002", 1.0, 0.0)]
    raw_geometry = [
        WallSegment(
            type="line",
            points=[(5.0, 5.0), (20.0, 20.0)],
            layer="A-SPAC-IDEN-LEDR",
        )
    ]

    corrected = extraction.apply_leader_line_corrections(rooms, raw_geometry)

    moved = [node for node in corrected if (node.x, node.y) == (20.0, 20.0)]
    assert len(moved) == 1
