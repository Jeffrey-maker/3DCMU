import assert from "node:assert/strict";
import test from "node:test";

import { acceptHallwaySuggestions, acceptRoomSuggestions } from "../src/editor/suggestions.js";
import { createEmptyProject } from "../src/editor/project.js";
import { asBuildingId, asFloorId } from "../src/model/ids.js";
import { validateMapTopology } from "../src/model/validation.js";

const floorId = asFloorId("floor-test");

function projectWithSuggestions() {
  const project = createEmptyProject();
  return {
    ...project,
    topology: {
      ...project.topology,
      floors: [{ id: floorId, buildingId: asBuildingId("building-main"), level: "4" }],
    },
    floorPlans: [{
      id: "plan-test",
      floorId,
      sourceFileName: "WEH-4-ESIM-Type.pdf",
      sourceMediaType: "application/pdf",
      sourcePage: 1,
      renderedImageDataUrl: "data:image/png;base64,",
      width: 1000,
      height: 700,
      suggestions: {
        rooms: [{ id: "room-4210", roomNumber: "4210", position: { x: 100, y: 120 } }],
        hallways: [{ id: "hallway-1", points: [{ x: 10, y: 20 }, { x: 50, y: 20 }, { x: 50, y: 80 }] }],
        warnings: [],
      },
    }],
  };
}

test("room suggestions become terminal places but remain unlinked", () => {
  const accepted = acceptRoomSuggestions(projectWithSuggestions(), floorId);
  assert.equal(accepted.topology.places.length, 1);
  assert.equal(accepted.topology.places[0]?.kind, "room");
  assert.equal(accepted.topology.accessPoints[0]?.connection, "terminal");
  assert.equal(accepted.topology.terminalAccessLinks.length, 0);
  assert.equal(accepted.floorPlans[0]?.suggestions?.rooms.length, 0);
  assert.deepEqual(validateMapTopology(accepted.topology), []);
});

test("hallway suggestions become reviewable bidirectional topology", () => {
  const accepted = acceptHallwaySuggestions(projectWithSuggestions(), floorId);
  assert.equal(accepted.topology.nodes.length, 3);
  assert.equal(accepted.topology.segments.length, 2);
  assert.equal(accepted.topology.edges.length, 4);
  assert.ok(accepted.topology.edges.every((edge) => edge.cost > 0));
  assert.equal(accepted.floorPlans[0]?.suggestions?.hallways.length, 0);
  assert.deepEqual(validateMapTopology(accepted.topology), []);
});
