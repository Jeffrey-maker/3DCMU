"""Extract conservative authoring suggestions from one ESIM PDF.

The editor remains the source of truth.  This module deliberately returns
only room-label positions and public-corridor centerlines; it does not infer
doors, vertical connectors, or cross-building links.
"""

from pathlib import Path
import re
import tempfile

import fitz
from pydantic import BaseModel, Field

from app.models.graph import FloorDraft
from app.services import corridor_centerline, extraction


class NormalizedPoint(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class RoomSuggestion(BaseModel):
    id: str
    room_number: str
    position: NormalizedPoint


class HallwaySuggestion(BaseModel):
    id: str
    points: list[NormalizedPoint]


class FloorSuggestionSet(BaseModel):
    schema_version: int = 1
    page: int
    page_width: float
    page_height: float
    rooms: list[RoomSuggestion]
    hallways: list[HallwaySuggestion]
    warnings: list[str] = Field(default_factory=list)


def _room_pattern(level: str) -> re.Pattern[str]:
    """Use the selected floor to avoid treating dimensions as room numbers."""
    normalized = level.strip().upper()
    if re.fullmatch(r"\d+", normalized):
        return re.compile(rf"^{re.escape(normalized)}\d{{2,3}}[A-Z]?$", re.IGNORECASE)
    if re.fullmatch(r"[A-Z]", normalized):
        return re.compile(rf"^{re.escape(normalized)}\d{{2,4}}[A-Z]?$", re.IGNORECASE)
    # Unknown naming scheme: stay conservative and require both letters and
    # a substantial numeric portion. Humans can still add anything omitted.
    return re.compile(r"^(?:[A-Z]{1,3}-?)?\d{3,4}[A-Z]?$", re.IGNORECASE)


def _normalized(x: float, y: float, width: float, height: float) -> NormalizedPoint:
    return NormalizedPoint(
        x=round(max(0.0, min(1.0, x / width)), 7),
        y=round(max(0.0, min(1.0, y / height)), 7),
    )


def _single_page_pdf(pdf_bytes: bytes, page_number: int, destination: Path) -> tuple[float, float]:
    source = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        index = page_number - 1
        if index < 0 or index >= len(source):
            raise ValueError(f"PDF page {page_number} does not exist.")
        page = source[index]
        width, height = float(page.rect.width), float(page.rect.height)
        output = fitz.open()
        try:
            output.insert_pdf(source, from_page=index, to_page=index)
            output.save(destination)
        finally:
            output.close()
        return width, height
    finally:
        source.close()


def extract_suggestions(pdf_bytes: bytes, page_number: int, level: str) -> FloorSuggestionSet:
    """Return suggestions in normalized coordinates for one imported page."""
    with tempfile.TemporaryDirectory(prefix="contour-esim-") as directory:
        pdf_path = Path(directory) / "page.pdf"
        width, height = _single_page_pdf(pdf_bytes, page_number, pdf_path)
        labels = extraction.extract_text_labels(str(pdf_path))
        pattern = _room_pattern(level)

        # ESIM reports sometimes print a room number more than once (base label
        # plus classification label). Keep the first occurrence so accepting
        # suggestions can never create duplicate places with the same number.
        rooms: list[RoomSuggestion] = []
        seen_labels: set[str] = set()
        for label in labels:
            room_number = label.text.strip().upper()
            if room_number in seen_labels or pattern.fullmatch(room_number) is None:
                continue
            seen_labels.add(room_number)
            rooms.append(
                RoomSuggestion(
                    id=f"room-{room_number.lower()}",
                    room_number=room_number,
                    position=_normalized(label.x, label.y, width, height),
                )
            )

        warnings: list[str] = []
        if not rooms:
            warnings.append(f"No room labels matching level {level or 'unknown'} were found.")

        raw_geometry = extraction.extract_vector_geometry(str(pdf_path))
        draft = FloorDraft(building="", floor=0, raw_geometry=raw_geometry)
        centerlines: list[list[tuple[float, float]]] | None = None

        # The analyzed PDF is also the displayed PDF, so no cross-PDF
        # registration is necessary. Reuse the deterministic color-mask and
        # skeletonization stages directly in the PDF's own coordinate space.
        color = corridor_centerline.corridor_color(str(pdf_path))
        if color is None:
            warnings.append("No unambiguous Public Corridor fill was found; rooms are still available.")
        else:
            corridor = corridor_centerline._corridor_mask(str(pdf_path), color)
            if corridor:
                mask = corridor_centerline.walkable_mask(
                    str(pdf_path), draft, corridor_centerline.IDENTITY, corridor
                )
                mask = corridor_centerline._components_touching(mask, corridor)
                centerlines = corridor_centerline._skeletonize(
                    mask, corridor_centerline.IDENTITY, draft
                )
            if not centerlines:
                warnings.append("The Public Corridor fill did not produce usable hallway lines.")

        hallways = [
            HallwaySuggestion(
                id=f"hallway-{index}",
                points=[_normalized(x, y, width, height) for x, y in line],
            )
            for index, line in enumerate(centerlines or [])
            if len(line) >= 2
        ]
        return FloorSuggestionSet(
            page=page_number,
            page_width=width,
            page_height=height,
            rooms=rooms,
            hallways=hallways,
            warnings=warnings,
        )
