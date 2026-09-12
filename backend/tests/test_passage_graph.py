import asyncio
import hashlib

import fitz
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.graph import FloorDraft, FloorGraph, Node, WallSegment
from app.services import gemini_vision, graph_store, passage_graph, pathfinding
from app.services.geometry import WallIndex


def node(name, x, y, kind="room"):
    return Node(id=name, building="TEST", floor=1, x=x, y=y, type=kind, label=name)


def suggestions(*lines, width=1000, height=1000):
    return {
        "summary": "Test passages", "warnings": [],
        "pathways": [{"id": f"line-{i}", "confidence": 0.95, "barrier_notes": "",
                      "points": [{"x_normalized": x * 1000 / width, "y_normalized": y * 1000 / height}
                                 for x, y in points]} for i, points in enumerate(lines)],
    }


def build(nodes, lines, walls=(), width=1000, height=1000):
    draft = FloorDraft(building="TEST", floor=1, nodes=[n for n in nodes if n.type == "room"],
                       raw_geometry=[WallSegment(type="line", points=[a, b], layer="ARCH|A-WALL")
                                     for a, b in walls])
    current = FloorGraph(building="TEST", floor=1, nodes=nodes)
    return passage_graph.build_passage_graph(draft, current, suggestions(*lines, width=width, height=height), width, height)


def test_rooms_route_via_doors_and_projections_in_middle_of_line():
    anchors = [node("A", 30, 50), node("B", 130, 50), node("dA", 30, 80, "door"), node("dB", 130, 80, "door")]
    result = build(anchors, [[(10, 100), (190, 100)]], width=200, height=300)
    assert result.graph.nodes == anchors
    assert not any(node.type == "corridor" for node in result.graph.nodes)
    assert not any(
        edge.from_node in {"dA", "dB"} and edge.to_node in {"dA", "dB"}
        for edge in result.graph.edges
    )
    route = pathfinding.shortest_path(passage_graph.materialize_for_routing(result.graph), "A", "B")
    assert route is not None
    assert [s.node.type for s in route.steps] == ["room", "door", "corridor", "corridor", "door", "room"]
    assert [(s.node.x, s.node.y) for s in route.steps[2:4]] == [(30, 100), (130, 100)]
    assert route.total_weight == pytest.approx(200)


@pytest.mark.parametrize("branch", [[(100, 100), (100, 160)], [(100, 50), (100, 160)]])
def test_t_and_cross_intersections_are_real_routing_junctions(branch):
    result = build([node("A", 20, 70), node("B", 140, 150),
                    node("dA", 20, 90, "door"), node("dB", 120, 150, "door")],
                   [[(10, 100), (180, 100)], branch])
    route = pathfinding.shortest_path(passage_graph.materialize_for_routing(result.graph), "A", "B")
    assert route is not None
    assert any((step.node.x, step.node.y) == (100, 100) for step in route.steps)
    assert not any(n.type == "corridor" for n in result.graph.nodes)
    assert sum((100, 100) in line.points for line in result.graph.passageways) >= 2


def test_nearest_line_across_wall_is_skipped_and_no_room_shortcut_is_created():
    walls = [((80, 0), (80, 200))]
    anchors = [node("A", 100, 30), node("dA", 100, 50, "door")]
    result = build(anchors, [[(70, 10), (70, 190)], [(140, 10), (140, 190)]], walls, width=200, height=200)
    attachment = next(a for a in result.graph.door_attachments if a.door_node_id == "dA")
    assert attachment.pathway_point[0] == 140
    assert attachment.pathway_point[0] != 70
    assert result.report["network_count"] == 2
    index = WallIndex(walls)
    assert all(not index.crosses_wall(a, b)
               for points in [attachment.points, *[line.points for line in result.graph.passageways]]
               for a, b in zip(points, points[1:]))


def test_blocked_room_is_reported_instead_of_connected_through_wall():
    result = build([node("A", 20, 50), node("dA", 100, 50, "door")],
                   [[(120, 10), (120, 190)]], [((60, 0), (60, 200))], width=200, height=200)
    assert result.graph.unconnected_room_ids == ["A"]
    assert not any("A" in (e.from_node, e.to_node) for e in result.graph.edges)


def test_passage_bends_around_public_space_barrier():
    walls = [((70, 94), (90, 94)), ((90, 94), (90, 106)), ((90, 106), (70, 106)), ((70, 106), (70, 94))]
    result = build([], [[(20, 100), (140, 100)]], walls, width=200, height=200)
    assert result.report["repaired_segment_count"] == 1
    index = WallIndex(walls)
    for line in result.graph.passageways:
        assert all(not index.crosses_wall(a, b) for a, b in zip(line.points, line.points[1:]))


def setup_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "enable_k2_horizon", False)
    monkeypatch.setattr(settings, "gemini_model", "test-vision")
    fp = "TEST-1"
    anchors = [node("A", 30, 50), node("B", 130, 50), node("dA", 30, 80, "door"), node("dB", 130, 80, "door")]
    draft = FloorDraft(building="TEST", floor=1, nodes=anchors[:2], raster_path=f"/api/floorplans/{fp}/raster.png")
    graph_store.save_draft(fp, draft)
    graph = graph_store.save_graph(fp, FloorGraph(building="TEST", floor=1, nodes=anchors))
    with fitz.open() as pdf:
        page = pdf.new_page(width=200, height=300)
        page.get_pixmap().save(graph_store.raster_path(fp))
        pdf.save(graph_store.base_pdf_path(fp))
    analysis = {
        "schema_version": 2, "task": "passage_centerlines", "status": "advisory_only",
        "model": "test-vision", "created_at": "2026-09-12T00:00:00Z",
        "raster_sha256": hashlib.sha256(graph_store.raster_path(fp).read_bytes()).hexdigest(),
        "suggestions": suggestions([(10, 100), (190, 100)], width=200, height=300),
    }
    return fp, graph, analysis


def test_build_endpoint_applies_caches_and_routes_on_exact_passage_lines(tmp_path, monkeypatch):
    fp, old_graph, analysis = setup_floor(tmp_path, monkeypatch)
    calls = []

    async def fake_analyze(*args, **kwargs):
        calls.append(args)
        return analysis

    monkeypatch.setattr(gemini_vision, "analyze_floorplan", fake_analyze)
    client = TestClient(app)
    built = client.post(f"/api/floorplans/{fp}/generate-pathways", json={})
    assert built.status_code == 200, built.text
    assert built.json()["used_cached_analysis"] is False
    assert built.json()["graph"]["routing_source"] == "gemini_pathways"
    assert built.json()["graph"]["page_height"] == 300
    assert not any(n["type"] == "corridor" for n in built.json()["graph"]["nodes"])
    assert not any(
        e["from_node"].startswith("d") and e["to_node"].startswith("d")
        for e in built.json()["graph"]["edges"]
    )
    assert built.json()["graph"]["passageways"]
    assert list((graph_store.floorplan_dir(fp) / "graph-backups").glob("*.json"))
    route = client.get("/api/route", params={"from": "A", "to": "B"})
    assert route.status_code == 200
    assert [n["type"] for n in route.json()["path"]] == ["room", "door", "corridor", "corridor", "door", "room"]
    assert {n["building"] for n in route.json()["path"]} == {"TEST"}
    repeated = client.post(f"/api/floorplans/{fp}/generate-pathways", json={})
    assert repeated.status_code == 200
    assert repeated.json()["used_cached_analysis"] is True
    assert len(calls) == 1
    retraced = client.post(f"/api/floorplans/{fp}/generate-pathways", json={"refresh": True})
    assert retraced.status_code == 200
    assert len(calls) == 2
    assert [n for n in graph_store.load_graph(fp).nodes if n.type != "corridor"] == old_graph.nodes


def test_failed_provider_call_keeps_previous_graph(tmp_path, monkeypatch):
    fp, previous, _ = setup_floor(tmp_path, monkeypatch)

    async def fail(*args, **kwargs):
        raise gemini_vision.GeminiVisionError("Quota exhausted")

    monkeypatch.setattr(gemini_vision, "analyze_floorplan", fail)
    response = TestClient(app).post(f"/api/floorplans/{fp}/generate-pathways", json={})
    assert response.status_code == 503
    assert graph_store.load_graph(fp) == previous


def test_vision_request_only_traces_lines_and_preserves_usage_metadata(tmp_path, monkeypatch):
    fp, _, _ = setup_floor(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "enable_gemini_vision", True)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    output = suggestions([(10, 100), (190, 100)])
    import json

    async def post(_client, url, *, headers, json):
        assert headers["x-goog-api-key"] == "test-key"
        schema = json["generationConfig"]["responseSchema"]
        assert set(schema["properties"]) == {"summary", "pathways", "warnings"}
        assert json["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "medium"}
        assert json["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"thought": True, "text": "Thinking"}, {"text": content},
            ]}}], "usageMetadata": {"totalTokenCount": 123}, "responseId": "test-response",
        })

    content = json.dumps(output)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = asyncio.run(gemini_vision.analyze_floorplan(graph_store.raster_path(fp), graph_store.load_draft(fp)))
    assert result["suggestions"] == output
    assert result["usage"]["totalTokenCount"] == 123
    assert result["response_id"] == "test-response"


def test_vision_retries_transient_503_before_succeeding(tmp_path, monkeypatch):
    fp, _, _ = setup_floor(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "enable_gemini_vision", True)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    output = suggestions([(10, 100), (190, 100)])
    import json

    attempts = []
    delays = []

    async def post(_client, url, *, headers, json):
        attempts.append(url)
        request = httpx.Request("POST", url)
        if len(attempts) < 3:
            return httpx.Response(503, request=request)
        return httpx.Response(200, request=request, json={
            "candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"text": content},
            ]}}],
            "usageMetadata": {"totalTokenCount": 123},
        })

    async def sleep(delay):
        delays.append(delay)

    content = json.dumps(output)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(gemini_vision.asyncio, "sleep", sleep)
    result = asyncio.run(
        gemini_vision.analyze_floorplan(
            graph_store.raster_path(fp), graph_store.load_draft(fp)
        )
    )

    assert result["suggestions"] == output
    assert len(attempts) == 3
    assert len(delays) == 2
