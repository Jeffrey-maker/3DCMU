from app.services import navgrid
from app.services.geometry import WallIndex


def test_finds_a_direct_path_with_no_walls():
    walls = WallIndex([])
    path = navgrid.find_path((0, 0), (100, 0), walls)

    assert path is not None
    assert path[0] == (0, 0)
    assert path[-1] == (100, 0)
    # unobstructed straight line -- grid path length should barely exceed the true distance
    assert navgrid.path_length(path) < 105


def test_routes_around_a_finite_wall_instead_of_failing():
    # A wall directly between start and end, but with open space above it.
    walls = WallIndex([((50, -20), (50, 20))])
    path = navgrid.find_path((0, 0), (100, 0), walls)

    assert path is not None
    # the path must actually go around -- longer than the blocked straight line
    assert navgrid.path_length(path) > 100


def test_returns_none_when_the_target_is_fully_enclosed():
    # A closed box around (50, 50) with no gap -- truly unreachable from outside.
    box = [
        ((30, 30), (70, 30)),
        ((70, 30), (70, 70)),
        ((70, 70), (30, 70)),
        ((30, 70), (30, 30)),
    ]
    walls = WallIndex(box)
    path = navgrid.find_path((0, 0), (50, 50), walls)

    assert path is None


def test_endpoint_snapping_never_adds_an_unchecked_wall_crossing():
    walls = WallIndex([((1, -10), (1, 10))])

    path = navgrid.find_path((2.9, 0), (20, 0), walls)

    assert path is not None
    assert all(not walls.crosses_wall(a, b) for a, b in zip(path, path[1:]))


def test_cost_multiplier_prefers_a_longer_low_cost_passage():
    walls = WallIndex([((50, -20), (50, 20))])

    path = navgrid.find_path(
        (0, 0),
        (100, 0),
        walls,
        cost_multiplier=lambda point: 25.0 if point[1] < 0 else 1.0,
    )

    assert path is not None
    assert max(point[1] for point in path) > 20


def test_simplify_path_collapses_collinear_points():
    points = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
    walls = WallIndex([])

    simplified = navgrid.simplify_path(points, walls)

    assert simplified == [(0.0, 0.0), (30.0, 0.0)]


def test_simplify_path_keeps_a_real_corner():
    # An L-shaped path around a wall -- the corner waypoint must survive
    # simplification, or the simplified path would cut through the wall.
    points = [(0.0, 0.0), (50.0, 0.0), (50.0, 50.0), (100.0, 50.0)]
    walls = WallIndex([((60, -10), (60, 30))])  # blocks a straight cut from (50,0) to (100,50)

    simplified = navgrid.simplify_path(points, walls)

    assert len(simplified) >= 3
    assert (50.0, 0.0) in simplified or (50.0, 50.0) in simplified
