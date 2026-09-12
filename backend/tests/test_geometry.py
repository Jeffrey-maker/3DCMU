from app.services.geometry import WallIndex, segments_intersect


def test_segments_intersect_crossing_lines():
    assert segments_intersect((0, 0), (10, 10), (0, 10), (10, 0)) is True


def test_segments_intersect_parallel_lines_do_not_cross():
    assert segments_intersect((0, 0), (10, 0), (0, 5), (10, 5)) is False


def test_segments_intersect_disjoint_segments():
    assert segments_intersect((0, 0), (1, 1), (100, 100), (101, 101)) is False


def test_wall_index_detects_a_crossing_wall():
    walls = WallIndex([((5, -10), (5, 10))])

    assert walls.crosses_wall((0, 0), (10, 0)) is True
    assert walls.crosses_wall((0, 0), (4, 0)) is False  # doesn't reach the wall


def test_wall_index_ignore_radius_excludes_a_walls_own_opening():
    # A short wall stub whose endpoint sits right next to the query point --
    # e.g. a door's own local frame/jamb detail -- must not block movement
    # when explicitly ignored near that endpoint.
    walls = WallIndex([((1, 0), (1, 10))])

    assert walls.crosses_wall((0, 0), (2, 0)) is True
    assert walls.crosses_wall((0, 0), (2, 0), ignore_radius=5.0) is False


def test_wall_index_handles_many_buckets_without_missing_a_wall():
    # A wall far from the origin in its own bucket must still be found by a
    # segment that spans into that bucket.
    walls = WallIndex([((500, -10), (500, 10))], bucket_size=40.0)

    assert walls.crosses_wall((0, 0), (1000, 0)) is True
