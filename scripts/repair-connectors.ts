import { readFile, writeFile } from "node:fs/promises";
import type { MapAuthoringProject } from "../src/model/authoring.js";
import { repairUnambiguousConnectorStops } from "../src/navigation/audit.js";

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) {
  throw new Error("Usage: repair-connectors <input.contour.json> <output.contour.json>");
}
const project = JSON.parse(await readFile(inputPath, "utf8")) as MapAuthoringProject;
const result = repairUnambiguousConnectorStops(project.topology);
const output: MapAuthoringProject = {
  ...project,
  topology: result.topology,
  updatedAt: new Date().toISOString(),
};
await writeFile(outputPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ repairs: result.repairs }, null, 2));
