import type { FloorPlanSuggestions } from "../model/authoring.js";

interface ApiPoint { readonly x: number; readonly y: number }
interface ApiSuggestionResponse {
  readonly rooms: readonly { readonly id: string; readonly room_number: string; readonly position: ApiPoint }[];
  readonly hallways: readonly { readonly id: string; readonly points: readonly ApiPoint[] }[];
  readonly warnings: readonly string[];
}

export async function extractPdfSuggestions(
  file: File,
  page: number,
  level: string,
  width: number,
  height: number,
): Promise<FloorPlanSuggestions> {
  const form = new FormData();
  form.append("pdf", file, file.name);
  form.append("page", String(page));
  form.append("level", level);
  try {
    const response = await fetch("/api/extract-suggestions", { method: "POST", body: form });
    if (!response.ok) {
      const payload = await response.json().catch(() => null) as { detail?: string } | null;
      throw new Error(payload?.detail ?? `Suggestion service returned ${response.status}.`);
    }
    const payload = await response.json() as ApiSuggestionResponse;
    return {
      rooms: payload.rooms.map((room) => ({
        id: room.id,
        roomNumber: room.room_number,
        position: { x: room.position.x * width, y: room.position.y * height },
      })),
      hallways: payload.hallways.map((hallway) => ({
        id: hallway.id,
        points: hallway.points.map((point) => ({ x: point.x * width, y: point.y * height })),
      })),
      warnings: payload.warnings,
    };
  } catch (error) {
    return {
      rooms: [],
      hallways: [],
      warnings: [error instanceof Error
        ? `Automatic suggestions unavailable: ${error.message}`
        : "Automatic suggestions unavailable."],
    };
  }
}
