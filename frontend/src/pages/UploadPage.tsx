import { useCallback, useState } from "react";
import type { DragEvent } from "react";
import { uploadFloorplan } from "../api/client";

interface UploadPageProps {
  onUploaded: (floorplanId: string) => void;
  initialBuilding?: string;
  initialFloor?: number;
}

type FileSlot = "base" | "dept" | "type";
type Status = "idle" | "uploading" | "done" | "error";

export function UploadPage({ onUploaded, initialBuilding = "WEH", initialFloor = 1 }: UploadPageProps) {
  const [building, setBuilding] = useState(initialBuilding);
  const [floor, setFloor] = useState(initialFloor);
  const [files, setFiles] = useState<Partial<Record<FileSlot, File>>>({});
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [dragOverSlot, setDragOverSlot] = useState<FileSlot | null>(null);
  const [uploadedFloorplanId, setUploadedFloorplanId] = useState<string | null>(null);

  const setSlotFile = useCallback((slot: FileSlot, file: File | undefined) => {
    setFiles((prev) => ({ ...prev, [slot]: file }));
  }, []);

  function handleDrop(slot: FileSlot, evt: DragEvent<HTMLDivElement>) {
    evt.preventDefault();
    setDragOverSlot(null);
    const file = evt.dataTransfer.files[0];
    if (file) setSlotFile(slot, file);
  }

  async function handleSubmit() {
    if (!files.base) {
      setMessage("A base PDF is required.");
      return;
    }
    setStatus("uploading");
    setMessage(null);
    try {
      const result = await uploadFloorplan(building, floor, {
        base: files.base,
        dept: files.dept,
        type: files.type,
      });
      setStatus("done");
      setMessage(
        `Extracted ${result.room_count} rooms and ${result.raw_geometry_count} wall/door segments. ` +
          (result.corridor_graph === "auto_generated"
            ? "Room and door anchors were updated. Use the Graph Editor to build and store the public passage lines with Gemini before routing."
            : "The existing graph was preserved and its room metadata was updated."),
      );
      setUploadedFloorplanId(result.floorplan_id);
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Upload failed.");
    }
  }

  function renderDropzone(slot: FileSlot, title: string, required: boolean) {
    const file = files[slot];
    return (
      <div
        className={`dropzone ${dragOverSlot === slot ? "drag-over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOverSlot(slot);
        }}
        onDragLeave={() => setDragOverSlot(null)}
        onDrop={(e) => handleDrop(slot, e)}
      >
        <p className="dropzone-title">
          {title} <span className="required-tag">{required ? "required" : "optional"}</span>
        </p>
        <p className="dropzone-file">{file ? file.name : "Drag a PDF here, or click to choose"}</p>
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setSlotFile(slot, e.target.files?.[0])}
        />
      </div>
    );
  }

  return (
    <div className="upload-page">
      <h2>Upload or update a floor plan</h2>
      <p className="page-hint">
        Saved floors reopen automatically after a refresh. Uploading the same building and floor
        updates that floor in place. The organization overlay only enriches room metadata. The
        room-type overlay does that too, and if it's a colored space-type report (with a Public
        Corridor/Stairway/Elevator legend), it is also sent to Gemini as a second reference image
        — matched by room number, not by coordinates — so it can tell public circulation apart
        from private rooms when tracing passage centerlines.
      </p>
      <div className="upload-fields">
        <label>
          Building
          <input value={building} onChange={(e) => setBuilding(e.target.value)} />
        </label>
        <label>
          Floor
          <input type="number" value={floor} onChange={(e) => setFloor(Number(e.target.value))} />
        </label>
      </div>

      {renderDropzone("base", "Base PDF", true)}
      {renderDropzone("dept", "Occupying organization PDF (recommended)", false)}
      {renderDropzone("type", "Room-type / space-type PDF (recommended)", false)}

      <button type="button" onClick={handleSubmit} disabled={status === "uploading"}>
        {status === "uploading" ? "Updating..." : "Upload & update floor"}
      </button>

      {message && <p className={`upload-message ${status}`}>{message}</p>}
      {uploadedFloorplanId && (
        <button type="button" onClick={() => onUploaded(uploadedFloorplanId)}>
          Continue to graph editor →
        </button>
      )}
    </div>
  );
}
