import type { MapAuthoringProject } from "../model/authoring.js";
import { asBuildingId, asMapId } from "../model/ids.js";

const buildingId = asBuildingId("building-main");

export function createEmptyProject(): MapAuthoringProject {
  return {
    documentVersion: 1,
    updatedAt: new Date().toISOString(),
    floorPlans: [],
    topology: {
      schemaVersion: 1,
      id: asMapId("contour-project"),
      name: "Untitled map",
      buildings: [{ id: buildingId, name: "Main building" }],
      floors: [], places: [], accessPoints: [], nodes: [], segments: [], edges: [],
      terminalAccessLinks: [], verticalConnectors: [],
    },
  };
}

export function downloadProject(project: MapAuthoringProject): void {
  const blob = new Blob([JSON.stringify(project, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${project.topology.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "map"}.contour.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function readProjectFile(file: File): Promise<MapAuthoringProject> {
  const parsed = JSON.parse(await file.text()) as Partial<MapAuthoringProject>;
  if (parsed.documentVersion !== 1 || !parsed.topology || !Array.isArray(parsed.floorPlans)) {
    throw new Error("This is not a supported Contour project file.");
  }
  return parsed as MapAuthoringProject;
}
