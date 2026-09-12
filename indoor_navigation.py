"""Build connected 3-D indoor maps and find routes between rooms.

The input maps are intentionally simple: each floor is a 2-D sequence where
``#`` is blocked and every other value is walkable. Room names and connector
locations are supplied separately, so maps can come from an image parser,
CAD export, or a hand-written grid.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import hypot
from typing import Hashable, Iterable, Mapping, Sequence

Coordinate = tuple[str, int, int, int]  # building, level, row, column


@dataclass(frozen=True)
class Floor:
    building: str
    level: int
    grid: tuple[tuple[object, ...], ...]
    cell_size: float = 1.0
    origin: tuple[float, float] = (0.0, 0.0)
    floor_height: float = 3.0

    def __post_init__(self) -> None:
        if not self.grid or not self.grid[0]:
            raise ValueError("floor grid cannot be empty")
        width = len(self.grid[0])
        if any(len(row) != width for row in self.grid):
            raise ValueError("floor grid must be rectangular")
        if self.cell_size <= 0 or self.floor_height <= 0:
            raise ValueError("cell_size and floor_height must be positive")


@dataclass(frozen=True)
class Connector:
    """A walkable link between two cells, for example a stair or elevator."""

    name: str
    a: Coordinate
    b: Coordinate
    cost: float = 1.0

    def __post_init__(self) -> None:
        if self.cost <= 0:
            raise ValueError("connector cost must be positive")


class IndoorMap3D:
    """A collection of 2-D floors connected into one navigable 3-D graph."""

    def __init__(self, *, blocked: Iterable[object] = ("#", 0, False)) -> None:
        self._blocked = set(blocked)
        self._floors: dict[tuple[str, int], Floor] = {}
        self._rooms: dict[Hashable, Coordinate] = {}
        self._connectors: list[Connector] = []

    def add_floor(
        self,
        building: str,
        level: int,
        grid: Sequence[Sequence[object]],
        *,
        rooms: Mapping[Hashable, tuple[int, int]] | None = None,
        cell_size: float = 1.0,
        origin: tuple[float, float] = (0.0, 0.0),
        floor_height: float = 3.0,
    ) -> None:
        """Add one 2-D map. ``rooms`` maps room IDs to ``(row, column)``."""
        key = (building, level)
        if key in self._floors:
            raise ValueError(f"floor already exists: {building!r} level {level}")
        floor = Floor(building, level, tuple(tuple(row) for row in grid), cell_size, origin, floor_height)
        self._floors[key] = floor
        for room_id, (row, column) in (rooms or {}).items():
            coordinate = (building, level, row, column)
            self._validate_coordinate(coordinate)
            if not self._is_open(coordinate):
                raise ValueError(f"room {room_id!r} is on a blocked cell")
            if room_id in self._rooms:
                raise ValueError(f"duplicate room ID: {room_id!r}")
            self._rooms[room_id] = coordinate

    def connect(
        self,
        name: str,
        a: Coordinate,
        b: Coordinate,
        *,
        cost: float = 1.0,
    ) -> None:
        """Connect two walkable cells, usually on different floors/buildings."""
        self._validate_coordinate(a)
        self._validate_coordinate(b)
        if not self._is_open(a) or not self._is_open(b):
            raise ValueError("connector endpoints must be walkable")
        self._connectors.append(Connector(name, a, b, cost))

    def route(self, start: Hashable | Coordinate, goal: Hashable | Coordinate) -> list[dict[str, object]]:
        """Return the shortest route as steps; raise ``ValueError`` if unreachable."""
        source = self._resolve(start)
        target = self._resolve(goal)
        distances: dict[Coordinate, float] = {source: 0.0}
        previous: dict[Coordinate, tuple[Coordinate, str]] = {}
        queue: list[tuple[float, int, Coordinate]] = [(0.0, 0, source)]
        counter = 1
        while queue:
            _, _, current = heappop(queue)
            if current == target:
                return self._format_route(self._reconstruct(previous, source, target))
            current_distance = distances[current]
            for neighbor, edge_name, edge_cost in self._neighbors(current):
                new_distance = current_distance + edge_cost
                if new_distance < distances.get(neighbor, float("inf")):
                    distances[neighbor] = new_distance
                    previous[neighbor] = (current, edge_name)
                    priority = new_distance + self._heuristic(neighbor, target)
                    heappush(queue, (priority, counter, neighbor))
                    counter += 1
        raise ValueError(f"no route from {start!r} to {goal!r}")

    def to_3d(self) -> dict[str, object]:
        """Return a JSON-serializable node/edge representation of the 3-D map."""
        nodes: list[dict[str, object]] = []
        edges: list[dict[str, object]] = []
        for floor in self._floors.values():
            for row in range(len(floor.grid)):
                for column in range(len(floor.grid[0])):
                    coordinate = (floor.building, floor.level, row, column)
                    if not self._is_open(coordinate):
                        continue
                    nodes.append({"id": self._node_id(coordinate), "building": floor.building,
                                  "level": floor.level, "row": row, "column": column,
                                  "position": self._position(coordinate)})
                    for neighbor in self._walking_neighbors(coordinate):
                        if self._node_id(coordinate) < self._node_id(neighbor):
                            edges.append({"from": self._node_id(coordinate), "to": self._node_id(neighbor)})
        for connector in self._connectors:
            edges.append({"from": self._node_id(connector.a), "to": self._node_id(connector.b),
                          "name": connector.name, "cost": connector.cost})
        return {"nodes": nodes, "edges": edges}

    def _validate_coordinate(self, coordinate: Coordinate) -> None:
        building, level, row, column = coordinate
        floor = self._floors.get((building, level))
        if floor is None or row < 0 or column < 0 or row >= len(floor.grid) or column >= len(floor.grid[0]):
            raise ValueError(f"unknown or out-of-bounds coordinate: {coordinate!r}")

    def _is_open(self, coordinate: Coordinate) -> bool:
        floor = self._floors[(coordinate[0], coordinate[1])]
        return floor.grid[coordinate[2]][coordinate[3]] not in self._blocked

    def _resolve(self, point: Hashable | Coordinate) -> Coordinate:
        coordinate = self._rooms.get(point, point)
        if not isinstance(coordinate, tuple) or len(coordinate) != 4:
            raise ValueError(f"unknown room or invalid coordinate: {point!r}")
        self._validate_coordinate(coordinate)
        if not self._is_open(coordinate):
            raise ValueError(f"route endpoint is blocked: {coordinate!r}")
        return coordinate

    def _neighbors(self, coordinate: Coordinate):
        for neighbor in self._walking_neighbors(coordinate):
            yield neighbor, "walk", 1.0
        for connector in self._connectors:
            if connector.a == coordinate:
                yield connector.b, connector.name, connector.cost
            elif connector.b == coordinate:
                yield connector.a, connector.name, connector.cost

    def _walking_neighbors(self, coordinate: Coordinate):
        building, level, row, column = coordinate
        floor = self._floors[(building, level)]
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbor = (building, level, row + dr, column + dc)
            if 0 <= neighbor[2] < len(floor.grid) and 0 <= neighbor[3] < len(floor.grid[0]) and self._is_open(neighbor):
                yield neighbor

    @staticmethod
    def _heuristic(a: Coordinate, b: Coordinate) -> float:
        # A zero heuristic across floors keeps custom connector costs (including
        # costs below one) admissible while still allowing A* on a floor.
        return hypot(a[2] - b[2], a[3] - b[3]) if a[:2] == b[:2] else 0.0

    @staticmethod
    def _reconstruct(previous, source, target):
        result = [(source, "start")]
        current = target
        tail = []
        while current != source:
            prior, edge_name = previous[current]
            tail.append((current, edge_name))
            current = prior
        return result + list(reversed(tail))

    def _format_route(self, route):
        return [{"building": c[0], "level": c[1], "row": c[2], "column": c[3], "via": via,
                 "position": self._position(c)} for c, via in route]

    def _position(self, coordinate):
        floor = self._floors[(coordinate[0], coordinate[1])]
        return {"x": floor.origin[0] + coordinate[3] * floor.cell_size,
                "y": floor.origin[1] + coordinate[2] * floor.cell_size,
                "z": coordinate[1] * floor.floor_height}

    @staticmethod
    def _node_id(coordinate):
        return f"{coordinate[0]}:{coordinate[1]}:{coordinate[2]}:{coordinate[3]}"
