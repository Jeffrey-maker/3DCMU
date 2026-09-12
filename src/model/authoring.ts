import type { FloorId } from "./ids.js";
import type { MapTopology } from "./topology.js";

export interface FloorPlanAsset {
  readonly id: string;
  readonly floorId: FloorId;
  readonly sourceFileName: string;
  readonly sourceMediaType: string;
  readonly sourcePage?: number;
  readonly renderedImageDataUrl: string;
  readonly width: number;
  readonly height: number;
}
export interface MapAuthoringProject {
  readonly documentVersion: 1;
  readonly topology: MapTopology;
  readonly floorPlans: readonly FloorPlanAsset[];
  readonly updatedAt: string;
}
