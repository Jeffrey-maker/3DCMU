import fitz

from app.models.graph import Node
from app.services.extraction import enrich_with_overlay, extract_text_labels


def _make_overlay_pdf(path: str, text: str, x: float, y: float) -> None:
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((x, y), text, fontsize=10)
    doc.save(path)
    doc.close()


def _measured_centroid(overlay_path: str):
    """insert_text's (x, y) is a baseline origin, not the rendered span's
    bbox centroid — measure the real centroid via the same extraction path
    the app uses, so test node positions are exact rather than assumed."""
    [label] = extract_text_labels(overlay_path)
    return label


def test_enrich_matches_a_nearby_label(tmp_path):
    overlay_path = str(tmp_path / "overlay.pdf")
    _make_overlay_pdf(overlay_path, "Office", 50.0, 50.0)
    measured = _measured_centroid(overlay_path)
    node = Node(
        id="B-1-100", building="B", floor=1,
        x=measured.x + 3, y=measured.y, type="room", label="100",
    )

    enriched = enrich_with_overlay([node], overlay_path, "room_type", match_radius=20.0)

    assert enriched[0].room_type == "Office"


def test_enrich_leaves_far_labels_unmatched(tmp_path):
    overlay_path = str(tmp_path / "overlay.pdf")
    _make_overlay_pdf(overlay_path, "Office", 50.0, 50.0)
    measured = _measured_centroid(overlay_path)
    node = Node(
        id="B-1-100", building="B", floor=1,
        x=measured.x + 100, y=measured.y, type="room", label="100",
    )

    enriched = enrich_with_overlay([node], overlay_path, "room_type", match_radius=20.0)

    assert enriched[0].room_type is None


def test_enrich_ignores_area_labels_as_candidates(tmp_path):
    """Overlay PDFs also contain square-footage text (e.g. "130sf") next to
    each room type/department label -- these must never be matched in as a
    room_type/department value themselves."""
    overlay_path = str(tmp_path / "overlay.pdf")
    _make_overlay_pdf(overlay_path, "130sf", 50.0, 50.0)
    measured = _measured_centroid(overlay_path)
    node = Node(
        id="B-1-100", building="B", floor=1,
        x=measured.x, y=measured.y, type="room", label="100",
    )

    enriched = enrich_with_overlay([node], overlay_path, "room_type", match_radius=20.0)

    assert enriched[0].room_type is None


def test_enrich_documents_off_center_label_limitation(tmp_path):
    """KNOWN LIMITATION called out in the build spec: nearest-centroid
    matching only succeeded for ~15% of rooms on real data, because
    multi-word labels like "Research/Nonclass Laboratory" often sit off to
    the side of the room's centroid rather than dead-center. This test pins
    that a label just outside match_radius is left blank (not guessed) --
    if this ever needs to improve, it should be a deliberate switch to
    point-in-polygon matching (per the spec), not an incidental radius bump."""
    overlay_path = str(tmp_path / "overlay.pdf")
    _make_overlay_pdf(overlay_path, "Research/Nonclass Laboratory", 50.0, 50.0)
    measured = _measured_centroid(overlay_path)
    node = Node(
        id="B-1-100", building="B", floor=1,
        x=measured.x - 40, y=measured.y, type="room", label="100",
    )

    enriched = enrich_with_overlay([node], overlay_path, "room_type", match_radius=20.0)

    assert enriched[0].room_type is None
