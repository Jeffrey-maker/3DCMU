import type { FloorId } from "./ids.js";
import type { MapTopology, Point2D } from "./topology.js";

export interface RoomSuggestion {
  readonly id: string;
  readonly roomNumber: string;
  readonly position: Point2D;
}

export interface HallwaySuggestion {
  readonly id: string;
  readonly points: readonly Point2D[];
}

export interface FloorPlanSuggestions {
  readonly rooms: readonly RoomSuggestion[];
  readonly hallways: readonly HallwaySuggestion[];
  readonly warnings: readonly string[];
}

export interface FloorPlanAsset {
  readonly id: string;
  readonly floorId: FloorId;
  readonly sourceFileName: string;
  readonly sourceMediaType: string;
  readonly sourcePage?: number;
  readonly renderedImageDataUrl: string;
  readonly width: number;
  readonly height: number;
  readonly suggestions?: FloorPlanSuggestions;
}
export interface MapAuthoringProject {
  readonly documentVersion: 1;
  readonly topology: MapTopology;
  readonly floorPlans: readonly FloorPlanAsset[];
  readonly updatedAt: string;
}
