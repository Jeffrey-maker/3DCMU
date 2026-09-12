"""Pure-geometry turn-by-turn directions — zero AI, ships as the permanent
fallback even once K2 Horizon phrasing is wired in.

Coordinate convention (load-bearing): PDF/MuPDF page space has origin
top-left, y increasing DOWNWARD (confirmed on the real Wean Hall PDF: page
rect (0,0,1224,792), no rotation). Walking east (1,0) then turning to face
south (0,1, i.e. toward larger y) yields turn_deg=+90 below, which is a
clockwise/right turn on a north-up map — so positive turn_deg = right turn,
negative = left turn. This sign convention is pinned by a regression test in
test_directions.py; if the geometry convention ever changes, that test must
be updated deliberately, not silently.
"""

import math
from dataclasses import dataclass
from typing import Optional

from app.models.graph import Node
from app.services.pathfinding import PathStep

STRAIGHT_THRESHOLD_DEG = 20
TURNAROUND_THRESHOLD_DEG = 160


@dataclass
class DirectionStep:
    text: str
    node_id: str  # anchors this instruction to a node, for frontend step-highlighting


def _bearing_turn_deg(
    v_in: tuple[float, float], v_out: tuple[float, float]
) -> Optional[float]:
    """Signed turn angle in degrees, range (-180, 180]. None for a degenerate
    (near-zero-length) incoming/outgoing vector, e.g. a door node coincident
    with a room centroid."""
    mag_in = math.hypot(*v_in)
    mag_out = math.hypot(*v_out)
    if mag_in < 1e-6 or mag_out < 1e-6:
        return None
    cross = v_in[0] * v_out[1] - v_in[1] * v_out[0]
    dot = v_in[0] * v_out[0] + v_in[1] * v_out[1]
    return math.degrees(math.atan2(cross, dot))


def _classify_turn(turn_deg: float) -> str:
    if abs(turn_deg) <= STRAIGHT_THRESHOLD_DEG:
        return "continue straight"
    if abs(turn_deg) >= TURNAROUND_THRESHOLD_DEG:
        return "turn around"
    return "turn right" if turn_deg > 0 else "turn left"


def _describe_node(node: Node) -> str:
    if node.label:
        return f"Room {node.label}"
    return node.type.replace("_", " ")


def generate_directions(steps: list[PathStep]) -> list[DirectionStep]:
    if not steps:
        return []

    first = steps[0].node
    if len(steps) == 1:
        return [
            DirectionStep(
                text=f"You are already at Room {first.label or first.id}.",
                node_id=first.id,
            )
        ]

    second = steps[1].node
    directions: list[DirectionStep] = [
        DirectionStep(
            text=f"Start at Room {first.label or first.id}, head toward {_describe_node(second)}.",
            node_id=first.id,
        )
    ]

    n = len(steps)
    for i in range(1, n - 1):  # intermediate nodes only — start/final handled separately
        prev_node = steps[i - 1].node
        cur_node = steps[i].node
        next_step = steps[i + 1]
        next_node = next_step.node
        edge_in_next = next_step.edge_in

        if edge_in_next is not None and edge_in_next.type in ("stairs", "elevator"):
            mode = "stairs" if edge_in_next.type == "stairs" else "elevator"
            directions.append(
                DirectionStep(
                    text=f"Take the {mode} to floor {next_node.floor}.",
                    node_id=cur_node.id,
                )
            )
            continue

        if next_node.type == "entrance" or (
            edge_in_next is not None and edge_in_next.type == "outdoor_path"
        ):
            directions.append(
                DirectionStep(text="Exit the building.", node_id=cur_node.id)
            )
            continue

        v_in = (cur_node.x - prev_node.x, cur_node.y - prev_node.y)
        v_out = (next_node.x - cur_node.x, next_node.y - cur_node.y)
        turn_deg = _bearing_turn_deg(v_in, v_out)
        action = "continue straight" if turn_deg is None else _classify_turn(turn_deg)
        directions.append(
            DirectionStep(text=f"{action.capitalize()}.", node_id=cur_node.id)
        )

    last = steps[-1].node
    directions.append(
        DirectionStep(
            text=f"Arrive at Room {last.label or last.id}.", node_id=last.id
        )
    )
    return directions
