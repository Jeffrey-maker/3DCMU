"""Gemini traces public passage centerlines; rooms/doors stay deterministic."""

import asyncio
import base64
import hashlib
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import fitz
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.models.graph import FloorDraft


class GeminiVisionError(RuntimeError):
    pass


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
MAX_REQUEST_ATTEMPTS = 4


async def _post_with_retries(
    client: httpx.AsyncClient,
    endpoint: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
) -> httpx.Response:
    """Retry transient Gemini failures because this integration uses raw REST.

    Google's SDK normally provides this behavior. Keep the bounded retry here
    so one temporary 503 does not force a user to rerun the whole-floor job.
    """
    for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
        try:
            response = await client.post(endpoint, headers=headers, json=json)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code not in RETRYABLE_STATUS_CODES
                or attempt == MAX_REQUEST_ATTEMPTS
            ):
                raise
            retry_after = exc.response.headers.get("retry-after", "")
        except httpx.TimeoutException:
            if attempt == MAX_REQUEST_ATTEMPTS:
                raise
            retry_after = ""

        try:
            delay = min(60.0, max(0.0, float(retry_after)))
        except ValueError:
            delay = 0.0
        if delay == 0:
            base_delay = float(2 ** (attempt - 1))
            delay = base_delay + random.uniform(0, base_delay * 0.25)
        await asyncio.sleep(delay)

    raise RuntimeError("unreachable")


class ImagePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x_normalized: float = Field(ge=0, le=1000)
    y_normalized: float = Field(ge=0, le=1000)


class Pathway(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str
    points: list[ImagePoint] = Field(min_length=2, max_length=200)
    confidence: float = Field(ge=0, le=1)
    barrier_notes: str


class PassageSuggestions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    # Bounded to keep a runaway response from reaching the O(n^2) junction
    # pass, but generous: the geometric builder legitimately emits one entry
    # per branch between junctions, and a floor with large open walkable
    # spaces runs to several hundred.
    pathways: list[Pathway] = Field(max_length=4000)
    warnings: list[str]


def _response_schema() -> dict:
    """Use the generateContent Schema subset; validate all bounds locally.

    Expand Pydantic references and omit large array bounds, which can exceed
    the provider's schema compilation limits for nested passage arrays.
    """
    schema = PassageSuggestions.model_json_schema()
    definitions = schema.get("$defs", {})

    def expand(value: dict) -> dict:
        if "$ref" in value:
            return expand(definitions[value["$ref"].rsplit("/", 1)[1]])
        result = {"type": value["type"].upper()}
        if "properties" in value:
            result["properties"] = {name: expand(prop) for name, prop in value["properties"].items()}
        if "items" in value:
            result["items"] = expand(value["items"])
        if "required" in value:
            result["required"] = value["required"]
        return result

    return expand(schema)


def _prompt(
    draft: FloorDraft,
    has_space_type_reference: bool = False,
    overlay_categories: dict[str, str] | None = None,
) -> str:
    from app.services import corridor_heuristic

    door_count = len(corridor_heuristic._cluster_door_points(draft.raw_geometry))
    if overlay_categories:
        descriptions = []
        if "public_corridor" in overlay_categories:
            descriptions.append(
                f"a translucent {overlay_categories['public_corridor']} tint over Public Corridor areas "
                "-- trace your passage centerlines through these areas, and treat their extent as ground "
                "truth for public circulation even where it disagrees with what the bare linework alone "
                "would suggest"
            )
        if "stairway" in overlay_categories:
            descriptions.append(
                f"a translucent {overlay_categories['stairway']} tint over Stairway areas -- route a "
                "connector into these the same as any other door"
            )
        if "elevator" in overlay_categories:
            descriptions.append(
                f"a translucent {overlay_categories['elevator']} tint over Elevator areas -- route a "
                "connector into these the same as any other door"
            )
        space_type_note = (
            " This floor plan image has already been marked up with an official facilities "
            "space-type classification, precisely aligned to this same drawing: "
            + "; and ".join(descriptions)
            + ". Areas with no tint are private rooms -- do not trace through them except via a "
            "room's own door connector, which the application already handles separately."
        )
    elif has_space_type_reference:
        space_type_note = (
            " A second reference image follows the floor plan: an official space-type "
            "report for this same floor, with its own printed color-coded legend (a "
            "\"SPACE TYPE\" table) and the same room numbers. Read that legend directly "
            "from the image -- do not assume fixed colors -- and find the category "
            "meaning public corridor (commonly labeled something like \"Public Corridor\" "
            "or code \"W06\"), plus stairway (\"W07\") and elevator (\"W02\"). Match rooms "
            "between the two images BY ROOM NUMBER, not by position -- the two images may "
            "use different page sizes, scales, or crops. Treat that report as ground "
            "truth for which numbered areas are public circulation versus private rooms: "
            "trace passage centerlines only through areas the report classifies as public "
            "corridor, and route a connector into stairway/elevator areas the same as any "
            "other door. If a numbered area's classification is genuinely ambiguous from "
            "the report, fall back to the floor plan's own drawn geometry and add a "
            "warning."
        )
    else:
        space_type_note = ""
    return (
        "Trace ONLY the walkable public passage CENTERLINES in this architectural "
        f"floor plan ({draft.building}, floor {draft.floor}). Return polylines for "
        "all corridors, passage branches, and circulation through public spaces. "
        "Small green dots mark existing door candidates. Use them only to check "
        "that your public passage network reaches every part of the floor; do not "
        f"connect the dots to each other. There are approximately {door_count} green "
        "door dots. Trace the passage serving every cluster of dots, including short "
        "branches, alcoves, and every wing. Do not stop after finding only the most "
        "obvious main corridor: this floor's shape is very unlikely to be a simple "
        "rectangle -- it may have wings, appendages, or extensions running far from the "
        "main corridor spine in any direction (including a long extension running "
        "mostly vertically, unlike the main body), and each one needs its own traced "
        "centerline reaching all the way to its far end. Before returning, mentally "
        "divide the image into a 3x3 grid (nine cells: top/middle/bottom crossed with "
        "left/center/right) and separately verify each cell that contains any door dots "
        "or open circulation space also contains a piece of your traced network -- a "
        "cell with door dots but no nearby traced line means you missed a branch there. "
        "Then check that your ENTIRE traced network forms a SINGLE connected system: if "
        "you find two or more groups of passage lines that never touch or join, you have "
        "almost certainly missed a connecting corridor segment between them -- go back "
        "and trace it, extending an existing line to reach the other group at a real "
        "corridor junction. Only leave the network split into more than one group if the "
        "floor plan itself shows no possible walkable connection (e.g. a courtyard, or "
        "wings that only connect through a different floor), and say so explicitly in a "
        "warning. Do not return room nodes, door nodes, "
        "room-to-room edges, or lines inside "
        "private offices/classrooms/closets. The application already has room and "
        "door nodes and will connect them to your lines. A numbered area may itself "
        "be a corridor or lobby: follow its actual circulation space."
        f"{space_type_note} "
        "Follow the middle of the passage, with points at bends and junctions, "
        "not a zigzag between doors. Include branches and loops. Where passage "
        "lines meet, use exactly the same coordinates for the common junction. "
        "Avoid walls, columns, desks, railings, furniture, and other obstacles. "
        "Never draw through a wall or outside the building. Add a warning when "
        "you cannot identify a passage confidently. Coordinates are normalized "
        "to the ENTIRE supplied cropped FIRST image (the floor plan): x=0 at the left, "
        "x=1000 at the right, y=0 at the top and y=1000 at the bottom"
        + (
            " -- never coordinates from the second reference image. "
            if has_space_type_reference and not overlay_categories
            else ". "
        )
        + "Return only the requested JSON."
    )


def _crop_to_floorplan(
    pixmap: fitz.Pixmap, draft: FloorDraft, page_width: float, page_height: float
) -> tuple[bytes, tuple[float, float, float, float]]:
    """Give vision tokens to the drawing rather than the page's blank margins."""
    from app.services import corridor_heuristic

    polygon = corridor_heuristic.exterior_space_polygon(draft.raw_geometry)
    if polygon:
        margin = 18.0
        left = max(0.0, min(p[0] for p in polygon) - margin)
        top = max(0.0, min(p[1] for p in polygon) - margin)
        right = min(page_width, max(p[0] for p in polygon) + margin)
        bottom = min(page_height, max(p[1] for p in polygon) + margin)
    else:
        left, top, right, bottom = 0.0, 0.0, page_width, page_height

    x_scale, y_scale = pixmap.width / page_width, pixmap.height / page_height
    x0, y0 = round(left * x_scale), round(top * y_scale)
    x1, y1 = round(right * x_scale), round(bottom * y_scale)
    source = pixmap.samples
    samples = b"".join(
        source[y * pixmap.stride + x0 * pixmap.n : y * pixmap.stride + x1 * pixmap.n]
        for y in range(y0, y1)
    )
    cropped = fitz.Pixmap(pixmap.colorspace, x1 - x0, y1 - y0, samples, pixmap.alpha)
    cropped.set_dpi(pixmap.xres, pixmap.yres)
    color = (16, 185, 129, 255) if cropped.alpha else (16, 185, 129)
    for door_x, door_y in corridor_heuristic._cluster_door_points(draft.raw_geometry):
        center_x = round(door_x * x_scale) - x0
        center_y = round(door_y * y_scale) - y0
        for dx in range(-6, 7):
            for dy in range(-6, 7):
                if dx * dx + dy * dy <= 36:
                    px, py = center_x + dx, center_y + dy
                    if 0 <= px < cropped.width and 0 <= py < cropped.height:
                        cropped.set_pixel(px, py, color)
    return cropped.tobytes("png"), (left, top, right, bottom)


async def analyze_floorplan(
    raster_path: Path,
    draft: FloorDraft,
    page_width: float = 1224.0,
    page_height: float = 792.0,
    space_type_pdf_path: Path | None = None,
    base_pdf_path: Path | None = None,
) -> dict[str, Any]:
    if not settings.enable_gemini_vision or not settings.gemini_api_key:
        raise GeminiVisionError(
            "Set ENABLE_GEMINI_VISION=true and GEMINI_API_KEY in backend/.env, "
            "then restart the backend to use Gemini."
        )

    original_image = raster_path.read_bytes()
    base_pixmap = fitz.Pixmap(str(raster_path))

    # Both PDFs are usually plotted from the same CAD model at a slightly
    # different page scale. When we can register them precisely (see
    # space_type_overlay), draw the report's Public Corridor/Stairway/
    # Elevator areas directly onto a fresh render of the base plan instead of
    # sending Gemini two separately-scaled images and hoping it cross-
    # references them visually by room number.
    overlay_categories: dict[str, str] | None = None
    space_type_image: bytes | None = None
    if space_type_pdf_path is not None and space_type_pdf_path.exists():
        from app.services import rasterize, space_type_overlay

        overlay = None
        if base_pdf_path is not None and base_pdf_path.exists():
            dpi = round(base_pixmap.width / page_width * 72)
            overlay = space_type_overlay.build_overlay_pixmap(
                str(base_pdf_path), str(space_type_pdf_path), dpi
            )
        if overlay is not None:
            base_pixmap = fitz.Pixmap(overlay.png_bytes)
            overlay_categories = overlay.matched_categories
        else:
            # Couldn't register the two PDFs precisely enough to draw with --
            # fall back to sending the report as its own, unaligned image.
            space_type_image = rasterize.render_to_bytes(str(space_type_pdf_path))

    image, crop_bounds = _crop_to_floorplan(base_pixmap, draft, page_width, page_height)

    # Leave room for base64 expansion, schema and prompt in the 20 MB request.
    total_image_bytes = len(image) + len(space_type_image or b"")
    if total_image_bytes > 14 * 1024 * 1024:
        raise GeminiVisionError("Floor-plan image is too large. Reduce RASTER_DPI and re-upload.")

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    parts: list[dict[str, Any]] = [
        {"text": _prompt(
            draft,
            has_space_type_reference=space_type_image is not None,
            overlay_categories=overlay_categories,
        )},
        {"inline_data": {
            "mime_type": "image/png", "data": base64.b64encode(image).decode("ascii"),
        }},
    ]
    if space_type_image is not None:
        parts.append({"inline_data": {
            "mime_type": "image/png", "data": base64.b64encode(space_type_image).decode("ascii"),
        }})
    request_body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _response_schema(),
            # Passage completeness needs a deliberate whole-image scan. The
            # stable Flash model handles medium thinking without the 3.8
            # request failures observed for this workload.
            "thinkingConfig": {"thinkingLevel": settings.gemini_thinking_level},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await _post_with_retries(
                client,
                endpoint, headers={"x-goog-api-key": settings.gemini_api_key}, json=request_body,
            )
            payload = response.json()
        candidate = payload["candidates"][0]
        if candidate.get("finishReason") != "STOP":
            raise GeminiVisionError("Gemini did not finish tracing the passages. Please retry.")
        content = "".join(
            part["text"] for part in candidate["content"]["parts"]
            if isinstance(part.get("text"), str) and not part.get("thought")
        )
        suggestions = PassageSuggestions.model_validate_json(content).model_dump()
    except httpx.HTTPStatusError as exc:
        # Do not expose credentials or echo arbitrary provider response bodies.
        status = exc.response.status_code
        hint = {
            400: "Check that the configured model supports image input and structured output.",
            401: "Check GEMINI_API_KEY.",
            403: "Check the API key's permissions and Gemini API access.",
            404: "Check GEMINI_MODEL; this model is not available to the API key.",
            429: "The Gemini quota remained unavailable after automatic retries. Check billing/limits.",
            503: f"Gemini remained temporarily unavailable after {MAX_REQUEST_ATTEMPTS} attempts. Try again later.",
        }.get(status, f"The request failed after up to {MAX_REQUEST_ATTEMPTS} attempts.")
        raise GeminiVisionError(f"Gemini returned HTTP {status}. {hint}") from exc
    except httpx.TimeoutException as exc:
        raise GeminiVisionError("Gemini timed out while tracing passages. Please retry.") from exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise GeminiVisionError("Gemini did not return valid passage lines. Please retry.") from exc

    return {
        "schema_version": 2,
        "task": "passage_centerlines",
        "status": "advisory_only",
        "floorplan": {"building": draft.building, "floor": draft.floor},
        "model": settings.gemini_model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raster_sha256": hashlib.sha256(original_image).hexdigest(),
        "input_image_sha256": hashlib.sha256(image).hexdigest(),
        "space_type_sha256": (
            hashlib.sha256(space_type_image).hexdigest() if space_type_image is not None else None
        ),
        "space_type_overlay": overlay_categories,
        "response_id": payload.get("responseId"),
        "usage": payload.get("usageMetadata", {}),
        "coordinate_system": {
            "type": "normalized_cropped_image", "min": 0, "max": 1000,
            "origin": "top_left", "pdf_bounds": list(crop_bounds),
        },
        "suggestions": suggestions,
    }
