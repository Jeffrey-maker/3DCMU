"""Shared 2D geometry primitives used by both the sparse wall-aware MST
(corridor_heuristic) and the grid-based pathfinder (navgrid)."""

import math

Point = tuple[float, float]


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _orientation(a: Point, b: Point, c: Point) -> int:
    val = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(val) < 1e-9:
        return 0
    return 1 if val > 0 else 2


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    return (
        min(a[0], b[0]) - 1e-6 <= p[0] <= max(a[0], b[0]) + 1e-6
        and min(a[1], b[1]) - 1e-6 <= p[1] <= max(a[1], b[1]) + 1e-6
    )


def segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    o1, o2 = _orientation(p1, p2, p3), _orientation(p1, p2, p4)
    o3, o4 = _orientation(p3, p4, p1), _orientation(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, p2, p3):
        return True
    if o2 == 0 and _on_segment(p1, p2, p4):
        return True
    if o3 == 0 and _on_segment(p3, p4, p1):
        return True
    if o4 == 0 and _on_segment(p3, p4, p2):
        return True
    return False


class WallIndex:
    """Spatial hash of wall line segments, bucketed by position, so a
    cell-to-cell (or door-to-door) edge check only tests nearby walls
    instead of all of them -- essential for a fine-grained grid, where a
    naive check-against-every-wall would be far too slow."""

    def __init__(self, walls: list[tuple[Point, Point]], bucket_size: float = 40.0):
        self.bucket_size = bucket_size
        self.buckets: dict[tuple[int, int], list[tuple[Point, Point]]] = {}
        for w1, w2 in walls:
            for bucket in self._buckets_for_segment(w1, w2):
                self.buckets.setdefault(bucket, []).append((w1, w2))

    def _bucket_of(self, p: Point) -> tuple[int, int]:
        return (int(p[0] // self.bucket_size), int(p[1] // self.bucket_size))

    def _buckets_for_segment(self, w1: Point, w2: Point):
        bx1, by1 = self._bucket_of(w1)
        bx2, by2 = self._bucket_of(w2)
        for bx in range(min(bx1, bx2), max(bx1, bx2) + 1):
            for by in range(min(by1, by2), max(by1, by2) + 1):
                yield (bx, by)

    def crosses_wall(self, a: Point, b: Point, ignore_radius: float = 0.0) -> bool:
        seen: set[tuple[Point, Point]] = set()
        for bucket in self._buckets_for_segment(a, b):
            for w1, w2 in self.buckets.get(bucket, []):
                if (w1, w2) in seen:
                    continue
                seen.add((w1, w2))
                if ignore_radius > 0 and (
                    distance(w1, a) <= ignore_radius
                    or distance(w2, a) <= ignore_radius
                    or distance(w1, b) <= ignore_radius
                    or distance(w2, b) <= ignore_radius
                ):
                    continue
                if segments_intersect(a, b, w1, w2):
                    return True
        return False
