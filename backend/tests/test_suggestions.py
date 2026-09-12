import fitz
from fastapi.testclient import TestClient

from app.main import app
from app.services.suggestions import _room_pattern, extract_suggestions


def test_room_patterns_follow_numeric_and_lettered_floor_names():
    numeric = _room_pattern("4")
    assert numeric.fullmatch("4210")
    assert numeric.fullmatch("4001A")
    assert not numeric.fullmatch("5122")

    lettered = _room_pattern("A")
    assert lettered.fullmatch("A353")
    assert lettered.fullmatch("A100A")
    assert not lettered.fullmatch("B372")


def test_single_pdf_returns_deduplicated_normalized_room_suggestions():
    document = fitz.open()
    page = document.new_page(width=200, height=100)
    page.insert_text((20, 30), "4210")
    page.insert_text((22, 50), "4210")
    page.insert_text((120, 70), "4211A")
    page.insert_text((20, 90), "5122")
    pdf_bytes = document.tobytes()
    document.close()

    result = extract_suggestions(pdf_bytes, page_number=1, level="4")

    assert [room.room_number for room in result.rooms] == ["4210", "4211A"]
    assert all(0 <= room.position.x <= 1 for room in result.rooms)
    assert all(0 <= room.position.y <= 1 for room in result.rooms)
    assert result.hallways == []


def test_suggestion_endpoint_accepts_one_pdf_without_a_base_plan():
    document = fitz.open()
    page = document.new_page(width=200, height=100)
    page.insert_text((20, 30), "4210")
    pdf_bytes = document.tobytes()
    document.close()

    response = TestClient(app).post(
        "/api/extract-suggestions",
        data={"page": "1", "level": "4"},
        files={"pdf": ("WEH-4-ESIM-Type.pdf", pdf_bytes, "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["rooms"][0]["room_number"] == "4210"
    assert response.json()["hallways"] == []
