import assert from "node:assert/strict";
import test from "node:test";

import {
  asAccessLinkId, asAccessPointId, asBuildingId, asConnectorId, asEdgeId, asFloorId,
  asMapId, asNodeId, asPlaceId, asSegmentId,
} from "../src/model/ids.js";
import type { MapTopology } from "../src/model/topology.js";
import type { MapAuthoringProject } from "../src/model/authoring.js";
import { removeBuildingFromProject, removeFloorFromProject } from "../src/model/removal.js";
import { validateMapTopology } from "../src/model/validation.js";
import { findIndoorRoute } from "../src/navigation/router.js";

const buildingId = asBuildingId("WEH");
const floorId = asFloorId("WEH:4");
const west = asNodeId("west");
const east = asNodeId("east");
const segmentId = asSegmentId("hall");
const placeId = asPlaceId("WEH:4707");
const pointId = asAccessPointId("door-4707");

const validMap: MapTopology = {
  schemaVersion: 1,
  id: asMapId("test-map"),
  name: "Test map",
  buildings: [{ id: buildingId, name: "Wean Hall" }],
  floors: [{ id: floorId, buildingId, level: "4" }],
  places: [{ id: placeId, buildingId, kind: "room", roomNumber: "4707", name: "4707" }],
  accessPoints: [{ id: pointId, placeId, floorId, connection: "terminal", position: { x: 50, y: 20 } }],
  nodes: [
    { id: west, floorId, kind: "hallway_endpoint", position: { x: 0, y: 0 } },
    { id: east, floorId, kind: "hallway_endpoint", position: { x: 100, y: 0 } },
  ],
  segments: [{ id: segmentId, kind: "hallway", fromNodeId: west, toNodeId: east }],
  edges: [
    { id: asEdgeId("eastbound"), segmentId, fromNodeId: west, toNodeId: east, cost: 100, direction: "east", instruction: "Go east." },
    { id: asEdgeId("westbound"), segmentId, fromNodeId: east, toNodeId: west, cost: 100, direction: "west", instruction: "Go west." },
  ],
  terminalAccessLinks: [
    { id: asAccessLinkId("room-west"), accessPointId: pointId, nodeId: west, cost: 50, toPlaceInstruction: "Continue to 4707." },
    { id: asAccessLinkId("room-east"), accessPointId: pointId, nodeId: east, cost: 50, toPlaceInstruction: "Continue to 4707." },
  ],
  verticalConnectors: [],
};

const asProject = (topology: MapTopology): MapAuthoringProject => ({
  documentVersion: 1,
  topology,
  updatedAt: "2026-01-01T00:00:00.000Z",
  floorPlans: topology.floors.map((floor) => ({
    id: `plan-${floor.id}`,
    floorId: floor.id,
    sourceFileName: `${floor.id}.png`,
    sourceMediaType: "image/png",
    renderedImageDataUrl: "data:image/png;base64,",
    width: 100,
    height: 100,
  })),
});

test("a room can have terminal links from both hallway endpoints", () => {
  assert.deepEqual(validateMapTopology(validMap), []);
  assert.equal(validMap.edges.length, 2);
  assert.equal(validMap.terminalAccessLinks.length, 2);
});

test("terminal links cannot jump floors", () => {
  const invalid: MapTopology = {
    ...validMap,
    floors: [...validMap.floors, { id: asFloorId("WEH:5"), buildingId, level: "5" }],
    nodes: validMap.nodes.map((node, index) => index === 0 ? { ...node, floorId: asFloorId("WEH:5") } : node),
  };
  assert.ok(validateMapTopology(invalid).some(({ code }) => code === "floor_mismatch"));
});

test("routing crosses floors through a connected vertical connector", () => {
  const floor5 = asFloorId("WEH:5");
  const stair5 = asNodeId("stair-5");
  const junction5 = asNodeId("junction-5");
  const room5 = asPlaceId("WEH:5700");
  const room5Access = asAccessPointId("door-5700");
  const floor5Segment = asSegmentId("floor-5-hall");
  const topology: MapTopology = {
    ...validMap,
    floors: [...validMap.floors, { id: floor5, buildingId, level: "5" }],
    places: [...validMap.places, { id: room5, buildingId, kind: "room", roomNumber: "5700", name: "5700" }],
    accessPoints: [...validMap.accessPoints, { id: room5Access, placeId: room5, floorId: floor5, connection: "terminal", position: { x: 40, y: 20 } }],
    nodes: [
      ...validMap.nodes.map((node) => node.id === east ? { ...node, kind: "stair_landing" as const } : node),
      { id: stair5, floorId: floor5, kind: "stair_landing", position: { x: 0, y: 0 } },
      { id: junction5, floorId: floor5, kind: "junction", position: { x: 50, y: 0 } },
    ],
    segments: [...validMap.segments, { id: floor5Segment, kind: "hallway", fromNodeId: stair5, toNodeId: junction5 }],
    edges: [
      ...validMap.edges,
      { id: asEdgeId("floor-5-east"), segmentId: floor5Segment, fromNodeId: stair5, toNodeId: junction5, cost: 50, direction: "east", instruction: "Continue east." },
      { id: asEdgeId("floor-5-west"), segmentId: floor5Segment, fromNodeId: junction5, toNodeId: stair5, cost: 50, direction: "west", instruction: "Continue west." },
    ],
    terminalAccessLinks: [...validMap.terminalAccessLinks, { id: asAccessLinkId("room-5700-link"), accessPointId: room5Access, nodeId: junction5, cost: 20, toPlaceInstruction: "Stop at 5700." }],
    verticalConnectors: [{ id: asConnectorId("central-stair"), kind: "stairs", name: "Central stair", stopNodeIds: [east, stair5], segmentIds: [] }],
  };

  assert.deepEqual(validateMapTopology(topology), []);
  const route = findIndoorRoute(topology, placeId, room5);
  assert.deepEqual(route.floorIds, [floorId, floor5]);
  assert.ok(route.traversals.some(({ kind }) => kind === "stairs"));
  assert.equal(route.arrivalInstruction, "Stop at 5700.");

  assert.throws(
    () => findIndoorRoute(topology, placeId, room5, { stepFree: true }),
    /No step-free route connects these destinations/,
  );

  const elevatorTopology: MapTopology = {
    ...topology,
    nodes: topology.nodes.map((node) => node.id === east || node.id === stair5 ? { ...node, kind: "elevator_lobby" as const } : node),
    verticalConnectors: [{ id: asConnectorId("central-elevator"), kind: "elevator", name: "Central elevator", stopNodeIds: [east, stair5], segmentIds: [] }],
  };
  assert.deepEqual(validateMapTopology(elevatorTopology), []);
  const stepFreeRoute = findIndoorRoute(elevatorTopology, placeId, room5, { stepFree: true });
  assert.ok(stepFreeRoute.traversals.some(({ kind }) => kind === "elevator"));
  assert.ok(stepFreeRoute.traversals.every(({ kind }) => kind !== "stairs"));
});

test("routing crosses between buildings through a directed connector hallway", () => {
  const adjacentBuildingId = asBuildingId("NSH");
  const adjacentFloorId = asFloorId("NSH:4");
  const weanPortal = asNodeId("wean-portal");
  const adjacentPortal = asNodeId("nsh-portal");
  const adjacentHall = asNodeId("nsh-hall");
  const adjacentRoom = asPlaceId("NSH:4100");
  const adjacentAccess = asAccessPointId("door-nsh-4100");
  const bridgeSegment = asSegmentId("wean-nsh-bridge");
  const adjacentHallSegment = asSegmentId("nsh-floor-4-hall");
  const topology: MapTopology = {
    ...validMap,
    buildings: [...validMap.buildings, { id: adjacentBuildingId, name: "Newell-Simon Hall" }],
    floors: [...validMap.floors, { id: adjacentFloorId, buildingId: adjacentBuildingId, level: "4" }],
    places: [...validMap.places, { id: adjacentRoom, buildingId: adjacentBuildingId, kind: "room", roomNumber: "4100", name: "4100" }],
    accessPoints: [...validMap.accessPoints, { id: adjacentAccess, placeId: adjacentRoom, floorId: adjacentFloorId, connection: "terminal", position: { x: 80, y: 20 } }],
    nodes: [
      ...validMap.nodes,
      { id: weanPortal, floorId, kind: "building_portal", position: { x: 140, y: 0 } },
      { id: adjacentPortal, floorId: adjacentFloorId, kind: "building_portal", position: { x: 0, y: 0 } },
      { id: adjacentHall, floorId: adjacentFloorId, kind: "junction", position: { x: 80, y: 0 } },
    ],
    segments: [
      ...validMap.segments,
      { id: asSegmentId("wean-to-portal"), kind: "hallway", fromNodeId: east, toNodeId: weanPortal },
      { id: bridgeSegment, kind: "building_connection", fromNodeId: weanPortal, toNodeId: adjacentPortal, label: "Wean–Newell-Simon connector" },
      { id: adjacentHallSegment, kind: "hallway", fromNodeId: adjacentPortal, toNodeId: adjacentHall },
    ],
    edges: [
      ...validMap.edges,
      { id: asEdgeId("wean-portal-east"), segmentId: asSegmentId("wean-to-portal"), fromNodeId: east, toNodeId: weanPortal, cost: 40, direction: "east", instruction: "Continue east to the connector." },
      { id: asEdgeId("wean-portal-west"), segmentId: asSegmentId("wean-to-portal"), fromNodeId: weanPortal, toNodeId: east, cost: 40, direction: "west", instruction: "Continue west into Wean Hall." },
      { id: asEdgeId("enter-nsh"), segmentId: bridgeSegment, fromNodeId: weanPortal, toNodeId: adjacentPortal, cost: 60, instruction: "Follow the connector hallway into Newell-Simon Hall." },
      { id: asEdgeId("enter-wean"), segmentId: bridgeSegment, fromNodeId: adjacentPortal, toNodeId: weanPortal, cost: 60, instruction: "Follow the connector hallway into Wean Hall." },
      { id: asEdgeId("nsh-east"), segmentId: adjacentHallSegment, fromNodeId: adjacentPortal, toNodeId: adjacentHall, cost: 80, direction: "east", instruction: "Continue east." },
      { id: asEdgeId("nsh-west"), segmentId: adjacentHallSegment, fromNodeId: adjacentHall, toNodeId: adjacentPortal, cost: 80, direction: "west", instruction: "Continue west." },
    ],
    terminalAccessLinks: [...validMap.terminalAccessLinks, { id: asAccessLinkId("nsh-room-link"), accessPointId: adjacentAccess, nodeId: adjacentHall, cost: 20, toPlaceInstruction: "Stop at 4100." }],
  };

  assert.deepEqual(validateMapTopology(topology), []);
  const route = findIndoorRoute(topology, placeId, adjacentRoom);
  assert.deepEqual(route.floorIds, [floorId, adjacentFloorId]);
  const crossing = route.traversals.find(({ kind }) => kind === "building_connection");
  assert.equal(crossing?.destinationFloorId, adjacentFloorId);
  assert.equal(crossing?.instruction, "Follow the connector hallway into Newell-Simon Hall.");
});

test("removing a floor cascades through its floorplan and topology", () => {
  const removed = removeFloorFromProject(asProject(validMap), floorId);
  assert.equal(removed.topology.buildings.length, 1);
  assert.equal(removed.topology.floors.length, 0);
  assert.equal(removed.floorPlans.length, 0);
  assert.equal(removed.topology.places.length, 0);
  assert.equal(removed.topology.accessPoints.length, 0);
  assert.equal(removed.topology.nodes.length, 0);
  assert.equal(removed.topology.segments.length, 0);
  assert.equal(removed.topology.edges.length, 0);
  assert.equal(removed.topology.terminalAccessLinks.length, 0);
  assert.deepEqual(validateMapTopology(removed.topology), []);
});

test("removing a building also removes cross-building connections touching it", () => {
  const adjacentBuildingId = asBuildingId("NSH-remove");
  const adjacentFloorId = asFloorId("NSH-remove:4");
  const weanPortal = asNodeId("wean-remove-portal");
  const adjacentPortal = asNodeId("nsh-remove-portal");
  const bridgeSegment = asSegmentId("remove-bridge");
  const topology: MapTopology = {
    ...validMap,
    buildings: [...validMap.buildings, { id: adjacentBuildingId, name: "Newell-Simon Hall" }],
    floors: [...validMap.floors, { id: adjacentFloorId, buildingId: adjacentBuildingId, level: "4" }],
    nodes: [
      ...validMap.nodes,
      { id: weanPortal, floorId, kind: "building_portal", position: { x: 150, y: 0 } },
      { id: adjacentPortal, floorId: adjacentFloorId, kind: "building_portal", position: { x: 0, y: 0 } },
    ],
    segments: [...validMap.segments, { id: bridgeSegment, kind: "building_connection", fromNodeId: weanPortal, toNodeId: adjacentPortal }],
    edges: [
      ...validMap.edges,
      { id: asEdgeId("remove-enter-nsh"), segmentId: bridgeSegment, fromNodeId: weanPortal, toNodeId: adjacentPortal, cost: 60, instruction: "Enter Newell-Simon Hall." },
      { id: asEdgeId("remove-enter-wean"), segmentId: bridgeSegment, fromNodeId: adjacentPortal, toNodeId: weanPortal, cost: 60, instruction: "Enter Wean Hall." },
    ],
  };

  const removed = removeBuildingFromProject(asProject(topology), adjacentBuildingId);
  assert.deepEqual(removed.topology.buildings.map(({ id }) => id), [buildingId]);
  assert.deepEqual(removed.topology.floors.map(({ id }) => id), [floorId]);
  assert.ok(!removed.topology.nodes.some(({ id }) => id === adjacentPortal));
  assert.ok(removed.topology.nodes.some(({ id }) => id === weanPortal));
  assert.ok(!removed.topology.segments.some(({ id }) => id === bridgeSegment));
  assert.ok(!removed.topology.edges.some(({ segmentId }) => segmentId === bridgeSegment));
  assert.deepEqual(validateMapTopology(removed.topology), []);
});

test("removing a floor repairs a surviving vertical connector group", () => {
  const floor5 = asFloorId("WEH:5-remove");
  const stair4 = asNodeId("remove-stair-4");
  const stair5 = asNodeId("remove-stair-5");
  const stairPlace4 = asPlaceId("remove-stair-place-4");
  const stairPlace5 = asPlaceId("remove-stair-place-5");
  const stairAccess4 = asAccessPointId("remove-stair-access-4");
  const stairAccess5 = asAccessPointId("remove-stair-access-5");
  const topology: MapTopology = {
    ...validMap,
    floors: [...validMap.floors, { id: floor5, buildingId, level: "5" }],
    places: [
      ...validMap.places,
      { id: stairPlace4, buildingId, kind: "stairs", name: "West stair 4" },
      { id: stairPlace5, buildingId, kind: "stairs", name: "West stair 5" },
    ],
    accessPoints: [
      ...validMap.accessPoints,
      { id: stairAccess4, placeId: stairPlace4, floorId, connection: "network_node", nodeId: stair4 },
      { id: stairAccess5, placeId: stairPlace5, floorId: floor5, connection: "network_node", nodeId: stair5 },
    ],
    nodes: [
      ...validMap.nodes,
      { id: stair4, floorId, kind: "stair_landing", position: { x: 120, y: 0 } },
      { id: stair5, floorId: floor5, kind: "stair_landing", position: { x: 0, y: 0 } },
    ],
    verticalConnectors: [{ id: asConnectorId("remove-west-stair"), kind: "stairs", name: "West stair", placeId: stairPlace4, stopNodeIds: [stair4, stair5], segmentIds: [] }],
  };

  const removed = removeFloorFromProject(asProject(topology), floorId);
  assert.equal(removed.topology.verticalConnectors.length, 1);
  assert.deepEqual(removed.topology.verticalConnectors[0]?.stopNodeIds, [stair5]);
  assert.equal(removed.topology.verticalConnectors[0]?.placeId, stairPlace5);
  assert.deepEqual(validateMapTopology(removed.topology), []);
});
