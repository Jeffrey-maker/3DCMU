"""Extract room labels and normalized positions from CMU floor-plan PDFs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from pypdf import PdfReader

ROOM_PATTERN = re.compile(r"(?:\d{4}[A-Z]?|[346][NS]\d{3}[A-Z]?|(?:LL|EV|PH)[A-Z0-9]+)", re.I)
SCOTT_LEVELS = {"LL": 0, "EV": 1, "3": 3, "4": 4, "5": 5, "6": 6, "PH": 7}


def floor_identity(path: Path) -> tuple[str, str, int]:
    if path.name.startswith("WEH-"):
        label = path.name.split("-")[1]
        return "WEH", label, int(label)
    label = path.name.split("-")[1]
    return "SH", label, SCOTT_LEVELS[label]


def extract_floor(path: Path) -> dict[str, object]:
    building, level_label, level = floor_identity(path)
    page = PdfReader(str(path)).pages[0]
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    rooms: dict[str, dict[str, float | str]] = {}

    def visitor(text, cm, tm, _font, size):
        room = text.strip().replace(" ", "").upper()
        if size < 60 or not ROOM_PATTERN.fullmatch(room):
            return
        if building == "WEH" and not room.startswith(str(level)):
            return
        current = rooms.get(room)
        if current is None or size > current["fontSize"]:
            page_x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            page_y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            rooms[room] = {
                "id": f"{building}-{room}",
                "name": f"{'Wean' if building == 'WEH' else 'Scott'} {room}",
                "x": round(float(page_x) / width, 6),
                "z": round(1 - float(page_y) / height, 6),
                "fontSize": round(float(size), 2),
            }

    page.extract_text(visitor_text=visitor)
    for room in rooms.values():
        room.pop("fontSize")
    return {
        "id": f"{building}-{level_label}",
        "building": building,
        "buildingName": "Wean Hall" if building == "WEH" else "Scott Hall",
        "level": level,
        "levelLabel": level_label,
        "source": path.name,
        "image": f"./assets/floors/{building.lower()}-{level_label.lower()}.png",
        "rooms": sorted(rooms.values(), key=lambda room: room["id"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scott", type=Path)
    parser.add_argument("wean", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    paths = sorted(args.scott.glob("*.pdf")) + sorted(args.wean.glob("*.pdf"))
    floors = [extract_floor(path) for path in paths]
    room_count = sum(len(floor["rooms"]) for floor in floors)
    payload = {"generatedFrom": [str(args.scott), str(args.wean)], "roomCount": room_count, "floors": floors}
    args.output.write_text("export const floorData = " + json.dumps(payload, indent=2) + ";\n", encoding="utf-8")
    print(f"Extracted {room_count} labeled spaces from {len(floors)} plans into {args.output}")


if __name__ == "__main__":
    main()
