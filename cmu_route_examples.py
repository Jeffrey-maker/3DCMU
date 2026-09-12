"""Runnable Scott Hall / Wean Hall indoor-routing examples.

This is a landmark graph digitized from the supplied floor-plan PDFs.  It is
useful immediately for the example rooms below.  Add nodes and edges from the
remaining plans to expand coverage.  Edge costs are relative walking costs,
not surveyed distances in feet.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from typing import Iterable


@dataclass(frozen=True)
class Place:
    id: str
    label: str
    building: str
    floor: int


@dataclass(frozen=True)
class Edge:
    destination: str
    cost: float
    instruction: str
    accessible: bool = True


class CampusRouter:
    def __init__(self) -> None:
        self.places: dict[str, Place] = {}
        self.graph: dict[str, list[Edge]] = {}

    def add_place(self, place: Place) -> None:
        if place.id in self.places:
            raise ValueError(f"duplicate place: {place.id}")
        self.places[place.id] = place
        self.graph[place.id] = []

    def connect(
        self,
        a: str,
        b: str,
        cost: float,
        a_to_b: str,
        b_to_a: str,
        *,
        accessible: bool = True,
    ) -> None:
        if a not in self.places or b not in self.places:
            raise ValueError(f"unknown endpoint in edge {a!r} <-> {b!r}")
        if cost <= 0:
            raise ValueError("edge cost must be positive")
        self.graph[a].append(Edge(b, cost, a_to_b, accessible))
        self.graph[b].append(Edge(a, cost, b_to_a, accessible))

    def route(self, start: str, destination: str, *, wheelchair: bool = False) -> dict[str, object]:
        """Find the least-cost route with Dijkstra's algorithm."""
        if start not in self.places or destination not in self.places:
            known = ", ".join(sorted(p for p in self.places if "corridor" not in p and "core" not in p))
            raise ValueError(f"unknown room; known public endpoints: {known}")

        distances = {start: 0.0}
        previous: dict[str, tuple[str, Edge]] = {}
        order = count()
        queue = [(0.0, next(order), start)]

        while queue:
            distance, _, current = heappop(queue)
            if distance != distances.get(current):
                continue
            if current == destination:
                break
            for edge in self.graph[current]:
                if wheelchair and not edge.accessible:
                    continue
                candidate = distance + edge.cost
                if candidate < distances.get(edge.destination, float("inf")):
                    distances[edge.destination] = candidate
                    previous[edge.destination] = (current, edge)
                    heappush(queue, (candidate, next(order), edge.destination))

        if destination not in distances:
            mode = "wheelchair-accessible " if wheelchair else ""
            raise ValueError(f"no {mode}route from {start} to {destination}")

        steps: list[dict[str, object]] = []
        current = destination
        while current != start:
            prior, edge = previous[current]
            place = self.places[current]
            steps.append({
                "arrive_at": place.label,
                "building": place.building,
                "floor": place.floor,
                "instruction": edge.instruction,
            })
            current = prior
        steps.reverse()
        return {
            "start": self.places[start].label,
            "destination": self.places[destination].label,
            "relative_cost": distances[destination],
            "steps": steps,
        }


def build_scott_wean_router() -> CampusRouter:
    router = CampusRouter()
    places: Iterable[Place] = (
        Place("WEH-5222", "Wean 5222", "Wean Hall", 5),
        Place("WEH-5130", "Wean 5130", "Wean Hall", 5),
        Place("WEH5-west-corridor", "Wean Level 5 west corridor", "Wean Hall", 5),
        Place("WEH5-central-core", "Wean Level 5 central stair/elevator core", "Wean Hall", 5),
        Place("WEH5-east-corridor", "Wean Level 5 east corridor 5300", "Wean Hall", 5),
        Place("WEH-5343", "Wean 5343", "Wean Hall", 5),
        Place("WEH5-west-stair", "Wean west stair near 5129", "Wean Hall", 5),
        Place("WEH4-west-stair", "Wean west stair near 4129", "Wean Hall", 4),
        Place("WEH4-corridor-4100", "Wean corridor 4100", "Wean Hall", 4),
        Place("WEH4-corridor-4000", "Wean central corridor 4000", "Wean Hall", 4),
        Place("WEH4-central-core", "Wean Level 4 central elevator core", "Wean Hall", 4),
        Place("WEH4-corridor-4300", "Wean corridor 4300", "Wean Hall", 4),
        Place("WEH-4325", "Wean 4325", "Wean Hall", 4),
        Place("WEH4-scott-link", "Wean-to-Scott Level 4 passage", "Wean Hall", 4),
        Place("SH4-wean-link", "Scott-to-Wean Level 4 passage", "Scott Hall", 4),
        Place("SH-4N120A", "Scott 4N120A", "Scott Hall", 4),
        Place("SH-4N103", "Scott 4N103", "Scott Hall", 4),
        Place("SH4-north-corridor", "Scott north corridor 4N000", "Scott Hall", 4),
        Place("SH4-south-junction", "Scott south junction near 4S000/4S100", "Scott Hall", 4),
        Place("SH4-south-corridor", "Scott south corridor", "Scott Hall", 4),
        Place("SH-4S417", "Scott 4S417", "Scott Hall", 4),
    )
    for place in places:
        router.add_place(place)

    def link(a, b, cost, forward, backward, accessible=True):
        router.connect(a, b, cost, forward, backward, accessible=accessible)

    link("WEH-5222", "WEH5-west-corridor", 1, "Exit room 5222 into the west corridor.", "Enter room 5222.")
    link("WEH-5130", "WEH5-west-corridor", 1, "Exit room 5130 into the west corridor.", "Enter room 5130.")
    link("WEH5-west-corridor", "WEH5-central-core", 5, "Follow the corridor east toward rooms 5200 and 5008.", "Follow the corridor west toward the 5100/5200 rooms.")
    link("WEH5-central-core", "WEH5-east-corridor", 5, "Continue east into corridor 5300.", "Continue west to the central core.")
    link("WEH5-east-corridor", "WEH-5343", 4, "Follow the east corridor to room 5343.", "Exit 5343 and follow the corridor west.")
    link("WEH5-west-corridor", "WEH5-west-stair", 2, "Go to the west stair beside room 5129.", "Leave the stair and enter the west corridor.")
    link("WEH5-west-stair", "WEH4-west-stair", 3, "Descend one floor to Level 4.", "Climb one floor to Level 5.", accessible=False)
    link("WEH4-west-stair", "WEH4-corridor-4100", 1, "Exit the stair into corridor 4100.", "Follow corridor 4100 to the west stair.")
    link("WEH4-corridor-4100", "WEH4-corridor-4000", 4, "Continue east to central corridor 4000.", "Continue west into corridor 4100.")
    link("WEH5-central-core", "WEH4-central-core", 4, "Take the elevator down to Level 4.", "Take the elevator up to Level 5.")
    link("WEH4-central-core", "WEH4-corridor-4000", 1, "Exit the elevator into corridor 4000.", "Enter the central elevator core.")
    link("WEH4-corridor-4000", "WEH4-corridor-4300", 5, "Continue east into corridor 4300.", "Follow corridor 4300 west to corridor 4000.")
    link("WEH4-corridor-4300", "WEH-4325", 3, "Follow corridor 4300 to room 4325.", "Exit 4325 into corridor 4300.")
    link("WEH4-corridor-4000", "WEH4-scott-link", 2, "Follow signs for Scott Hall Level 4.", "Enter Wean central corridor 4000.")
    link("WEH4-scott-link", "SH4-wean-link", 2, "Cross the enclosed passage into Scott Hall.", "Cross the enclosed passage into Wean Hall.")
    link("SH4-wean-link", "SH4-north-corridor", 2, "Enter Scott's north corridor 4N000.", "Follow signs for the Wean Hall passage.")
    link("SH-4N120A", "SH4-north-corridor", 1, "Exit 4N120A into corridor 4N000.", "Enter room 4N120A.")
    link("SH-4N103", "SH4-north-corridor", 1, "Exit 4N103 into corridor 4N000.", "Enter room 4N103.")
    link("SH4-north-corridor", "SH4-south-junction", 5, "Travel south to the junction near 4S000/4S100.", "Travel north into corridor 4N000.")
    link("SH4-south-junction", "SH4-south-corridor", 2, "Continue into the long south corridor.", "Return north to the 4S000/4S100 junction.")
    link("SH4-south-corridor", "SH-4S417", 7, "Continue past the 4S200 and 4S400 room groups to 4S417.", "Exit 4S417 and follow the corridor north.")
    return router


def print_route(result: dict[str, object]) -> None:
    print(f"\n{result['start']} -> {result['destination']}")
    for number, step in enumerate(result["steps"], 1):
        print(f"  {number}. {step['instruction']}")
    print(f"  Relative cost: {result['relative_cost']}")


if __name__ == "__main__":
    navigation = build_scott_wean_router()
    examples = (
        ("WEH-5222", "WEH-5343"),
        ("SH-4N120A", "SH-4S417"),
        ("SH-4N103", "WEH-4325"),
        ("WEH-5130", "WEH-4325"),
        ("WEH-5222", "SH-4S417"),
    )
    for start, destination in examples:
        print_route(navigation.route(start, destination))
