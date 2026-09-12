#!/usr/bin/env python3
"""Dev convenience: POST the real Wean Hall Level 1 PDF to a locally running
API, so you don't have to drag-and-drop it by hand every time backend/data/
gets wiped.

Usage (with the backend running at http://localhost:8000):
    python3 scripts/seed_wean_hall.py
"""

import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API_BASE = "http://localhost:8000"
PDF_PATH = Path(__file__).resolve().parent.parent / "floor_plan" / "wean_hall" / "WEH-1-ESIM-Base.pdf"


def main() -> None:
    if not PDF_PATH.exists():
        print(f"Not found: {PDF_PATH}")
        raise SystemExit(1)

    boundary = uuid.uuid4().hex
    pdf_bytes = PDF_PATH.read_bytes()

    parts = []
    for field, value in (("building", "WEH"), ("floor", "1")):
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="base"; filename="{PDF_PATH.name}"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + pdf_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    request = urllib.request.Request(
        f"{API_BASE}/api/floorplans/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request) as response:
            print(response.read().decode())
    except urllib.error.URLError as exc:
        print(f"Couldn't reach {API_BASE} -- is the backend running? ({exc})")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
