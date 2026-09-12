"""Import compact 3D reference geometry from the read-only ybc graph."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


SOURCE_REF = "origin/ybc"
SOURCE_PATH = "backend/data/floorplans/WEH-4/graph.json"
OUTPUT = Path(__file__).parents[1] / "campus_route_ui" / "ybc-reference.js"


def main() -> None:
    raw = subprocess.run(
        ["git", "show", f"{SOURCE_REF}:{SOURCE_PATH}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    graph = json.loads(raw)
    source_commit = subprocess.run(
        ["git", "rev-parse", SOURCE_REF],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    payload = {
        "sourceBranch": "ybc",
        "sourceCommit": source_commit,
        "floors": {
            "WEH-4": {
                "pageWidth": graph["page_width"],
                "pageHeight": graph["page_height"],
                "nodes": graph["nodes"],
                "edges": graph["edges"],
                "passageways": graph["passageways"],
                "doorAttachments": graph["door_attachments"],
                "unconnectedRoomIds": graph["unconnected_room_ids"],
            }
        },
    }
    OUTPUT.write_text(
        "export const ybcReference = " + json.dumps(payload, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    print(f"Imported ybc {source_commit[:7]} reference graph into {OUTPUT}")


if __name__ == "__main__":
    main()
