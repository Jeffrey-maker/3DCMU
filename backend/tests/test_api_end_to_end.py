from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.models.graph import FloorDraft, FloorGraph, Node
from app.routers import floorplans

WEAN_HALL_PDF = (
    Path(__file__).resolve().parent.parent.parent
    / "floor_plan"
    / "wean_hall"
    / "WEH-1-ESIM-Base.pdf"
)


def _upload(client: TestClient):
    with open(WEAN_HALL_PDF, "rb") as f:
        return client.post(
            "/api/floorplans/upload",
            data={"building": "WEH", "floor": "1"},
            files={"base": ("WEH-1-ESIM-Base.pdf", f, "application/pdf")},
        )


def test_overlay_metadata_refresh_keeps_saved_graph_geometry():
    existing = FloorGraph(
        building="WEH",
        floor=1,
        routing_source="gemini_pathways",
        nodes=[Node(
            id="WEH-1-1001", building="WEH", floor=1, type="room",
            x=25, y=35, label="1001",
        )],
    )
    draft = FloorDraft(
        building="WEH",
        floor=1,
        nodes=[Node(
            id="WEH-1-1001", building="WEH", floor=1, type="room",
            x=125, y=135, label="1001", department="Computer Science", room_type="Office",
        )],
    )

    refreshed = floorplans._refresh_room_metadata(existing, draft)

    assert (refreshed.nodes[0].x, refreshed.nodes[0].y) == (25, 35)
    assert refreshed.nodes[0].department == "Computer Science"
    assert refreshed.nodes[0].room_type == "Office"
    assert refreshed.routing_source == "gemini_pathways"


def test_upload_stores_only_room_and_door_anchors_until_gemini_passages_exist(tmp_path, monkeypatch):
    # Isolate this test's runtime state and force the deterministic
    # directions path regardless of local K2 credentials -- this test's job
    # is to prove the zero-LLM core pipeline works end to end, upload only,
    # independent of today's environment.
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "enable_k2_horizon", False)
    client = TestClient(app)

    r = _upload(client)
    assert r.status_code == 200
    body = r.json()
    assert body["room_count"] == 52
    assert body["corridor_graph"] == "auto_generated"
    assert body["existing_graph_preserved"] is False
    fp_id = body["floorplan_id"]
    assert fp_id == "WEH-1"

    r = client.get(f"/api/floorplans/{fp_id}/graph")
    assert r.status_code == 200
    graph_body = r.json()
    assert graph_body["source"] == "graph"
    graph = graph_body["graph"]
    assert graph["auto_generated"] is True
    assert graph["routing_source"] == "anchors_only"
    # Every extracted space, whichever way it ended up typed: lift shafts
    # come back as "elevator" so routing can change floors through them.
    space_nodes = [n for n in graph["nodes"] if n["type"] in {"room", "stair", "elevator"}]
    assert len(space_nodes) == 52
    assert len(graph["edges"]) > 0
    assert not any(n["type"] == "corridor" for n in graph["nodes"])
    door_ids = {n["id"] for n in graph["nodes"] if n["type"] == "door"}
    assert not any(e["from_node"] in door_ids and e["to_node"] in door_ids for e in graph["edges"])
    assert graph["passageways"] == []
    assert graph["door_attachments"] == []

    # Room-to-room routing intentionally remains unavailable until Gemini's
    # passage lines have been reviewed, validated, and stored.
    by_x = sorted(space_nodes, key=lambda n: n["x"])
    from_id, to_id = by_x[0]["id"], by_x[-1]["id"]

    r = client.get("/api/route", params={"from": from_id, "to": to_id})
    assert r.status_code == 404


def test_human_edit_always_wins_and_clears_auto_generated_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    client = TestClient(app)
    fp_id = _upload(client).json()["floorplan_id"]

    room_nodes = [
        n
        for n in client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]["nodes"]
        if n["type"] == "room"
    ]
    hub = {
        "id": "WEH-1-HUMAN-HUB",
        "building": "WEH",
        "floor": 1,
        "x": 0.0,
        "y": 0.0,
        "type": "corridor",
        "label": None,
        "room_type": None,
        "department": None,
    }
    hand_built = {
        "building": "WEH",
        "floor": 1,
        "nodes": [hub] + room_nodes,
        "edges": [
            {
                "id": f"e-{n['id']}",
                "from_node": hub["id"],
                "to_node": n["id"],
                "weight": 0.0,
                "type": "door",
            }
            for n in room_nodes
        ],
        "auto_generated": True,  # a client must never be able to force this true
    }
    r = client.put(f"/api/floorplans/{fp_id}/graph", json=hand_built)
    assert r.status_code == 200

    saved = client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]
    assert saved["auto_generated"] is False
    assert any(n["id"] == "WEH-1-HUMAN-HUB" for n in saved["nodes"])


def test_reupload_preserves_a_real_human_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    client = TestClient(app)
    fp_id = _upload(client).json()["floorplan_id"]

    room_nodes = [
        n
        for n in client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]["nodes"]
        if n["type"] == "room"
    ][:2]
    hand_built = {
        "building": "WEH",
        "floor": 1,
        "nodes": room_nodes,
        "edges": [
            {
                "id": "e-1",
                "from_node": room_nodes[0]["id"],
                "to_node": room_nodes[1]["id"],
                "weight": 0.0,
                "type": "hallway",
            }
        ],
    }
    client.put(f"/api/floorplans/{fp_id}/graph", json=hand_built)

    r = _upload(client)
    body = r.json()
    assert body["existing_graph_preserved"] is True
    assert body["corridor_graph"] == "preserved"

    preserved = client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]
    assert len(preserved["nodes"]) == 2  # untouched by the re-upload


def test_reupload_regenerates_a_graph_that_was_saved_with_zero_edges(tmp_path, monkeypatch):
    """Covers the real gap this was built for: a human can save a graph
    before drawing any edges (or clear all edges), and that must not be
    treated as meaningful work worth protecting from regeneration."""
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    client = TestClient(app)
    fp_id = _upload(client).json()["floorplan_id"]

    graph = client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]
    client.put(f"/api/floorplans/{fp_id}/graph", json={**graph, "edges": []})
    assert client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]["edges"] == []

    r = _upload(client)
    body = r.json()
    assert body["existing_graph_preserved"] is False
    assert body["corridor_graph"] == "auto_generated"
    assert len(client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]["edges"]) > 0


def test_reupload_with_a_different_floor_plan_discards_the_stale_graph(tmp_path, monkeypatch):
    """Covers a real gap found via manual testing: building+floor alone
    isn't a reliable identity key. If a completely different PDF is
    uploaded under the same building/floor code (by mistake, or while
    testing another building), the old graph -- built for rooms that no
    longer exist in the new draft -- must not be silently kept. A mismatched
    graph is worse than no graph at all, not real work worth protecting."""
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    client = TestClient(app)
    fp_id = _upload(client).json()["floorplan_id"]

    graph = client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]
    assert len(graph["edges"]) > 0  # a real, non-empty graph exists to start

    other_pdf = tmp_path / "other.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    for i, label in enumerate(["9001", "9002", "9003"]):
        page.insert_text((20, 30 + i * 40), label, fontsize=10)
    doc.save(str(other_pdf))
    doc.close()

    with open(other_pdf, "rb") as f:
        r = client.post(
            "/api/floorplans/upload",
            data={"building": "WEH", "floor": "1"},
            files={"base": ("other.pdf", f, "application/pdf")},
        )
    body = r.json()
    assert body["existing_graph_preserved"] is False
    assert body["corridor_graph"] == "auto_generated"

    new_graph = client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]
    new_labels = {n["label"] for n in new_graph["nodes"] if n["label"]}
    assert new_labels == {"9001", "9002", "9003"}


def test_route_404s_for_unknown_node(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    client = TestClient(app)

    r = client.get("/api/route", params={"from": "does-not-exist", "to": "also-not-real"})

    assert r.status_code == 404


def test_graph_404s_before_any_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    client = TestClient(app)

    r = client.get("/api/floorplans/WEH-1/graph")

    assert r.status_code == 404


def test_saved_floorplans_are_listed_for_frontend_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    client = TestClient(app)
    assert client.get("/api/floorplans").json() == {"floorplans": []}

    fp_id = _upload(client).json()["floorplan_id"]
    listed = client.get("/api/floorplans")

    assert listed.status_code == 200
    assert listed.json()["floorplans"] == [
        {
            "floorplan_id": fp_id,
            "building": "WEH",
            "floor": 1,
            "raster_path": f"/api/floorplans/{fp_id}/raster.png",
            "has_graph": True,
            "routing_source": "anchors_only",
            "node_count": listed.json()["floorplans"][0]["node_count"],
            "edge_count": listed.json()["floorplans"][0]["edge_count"],
        }
    ]
    assert listed.json()["floorplans"][0]["node_count"] > 0
    assert listed.json()["floorplans"][0]["edge_count"] > 0


def test_gemini_analysis_requires_explicit_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "enable_gemini_vision", False)
    monkeypatch.setattr(config.settings, "gemini_api_key", "")
    client = TestClient(app)
    fp_id = _upload(client).json()["floorplan_id"]

    r = client.post(f"/api/floorplans/{fp_id}/analyze-gemini")

    assert r.status_code == 503
    assert "GEMINI_API_KEY" in r.json()["detail"]
    assert not (tmp_path / fp_id / "gemini_analysis.json").exists()


def test_gemini_analysis_is_stored_without_changing_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    client = TestClient(app)
    fp_id = _upload(client).json()["floorplan_id"]
    graph_before = client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]
    fake_analysis = {
        "schema_version": 1,
        "status": "advisory_only",
        "floorplan": {"building": "WEH", "floor": 1},
        "model": "test-gemini",
        "created_at": "2026-09-12T00:00:00+00:00",
        "raster_sha256": "abc",
        "coordinate_system": {
            "type": "normalized_image",
            "min": 0,
            "max": 1000,
            "origin": "top_left",
        },
        "suggestions": {
            "summary": "test",
            "room_nodes": [{"room_label": "1001"}],
            "doors": [{"room_label": "1001"}],
            "pathways": [{"id": "p1"}],
            "barriers": [{"description": "column"}],
            "warnings": [],
        },
    }

    async def fake_analyze(*_args, **_kwargs):
        return fake_analysis

    monkeypatch.setattr(floorplans.gemini_vision, "analyze_floorplan", fake_analyze)
    r = client.post(f"/api/floorplans/{fp_id}/analyze-gemini")

    assert r.status_code == 200
    assert r.json()["status"] == "advisory_only"
    assert r.json()["pathway_count"] == 1
    stored = client.get(f"/api/floorplans/{fp_id}/gemini-analysis")
    assert stored.status_code == 200
    assert stored.json() == fake_analysis
    graph_after = client.get(f"/api/floorplans/{fp_id}/graph").json()["graph"]
    assert graph_after == graph_before
