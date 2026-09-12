import { useCallback, useEffect, useState } from "react";
import { listFloorplans } from "./api/client";
import type { StoredFloorplan } from "./api/client";
import { UploadPage } from "./pages/UploadPage";
import { GraphEditorPage } from "./pages/GraphEditorPage";
import { RoutePage } from "./pages/RoutePage";

type View = "upload" | "editor" | "route";
const FLOORPLAN_KEY = "cmu-wayfinding-floorplan";
const VIEW_KEY = "cmu-wayfinding-view";

function storedView(): View {
  const value = localStorage.getItem(VIEW_KEY);
  return value === "editor" || value === "route" ? value : "upload";
}

function App() {
  const [view, setView] = useState<View>(storedView);
  const [floorplanId, setFloorplanId] = useState<string | null>(() => localStorage.getItem(FLOORPLAN_KEY));
  const [floorplans, setFloorplans] = useState<StoredFloorplan[]>([]);
  const [loadingFloors, setLoadingFloors] = useState(true);

  const refreshFloorplans = useCallback(async (preferredId?: string) => {
    try {
      const saved = await listFloorplans();
      setFloorplans(saved);
      const preferred = preferredId ?? localStorage.getItem(FLOORPLAN_KEY);
      const selected = saved.find((fp) => fp.floorplan_id === preferred) ?? saved[0] ?? null;
      setFloorplanId(selected?.floorplan_id ?? null);
      if (selected) {
        localStorage.setItem(FLOORPLAN_KEY, selected.floorplan_id);
        if (!preferred) setView("editor");
      }
      else {
        localStorage.removeItem(FLOORPLAN_KEY);
        setView("upload");
      }
    } finally {
      setLoadingFloors(false);
    }
  }, []);

  useEffect(() => {
    void refreshFloorplans();
  }, [refreshFloorplans]);

  useEffect(() => {
    localStorage.setItem(VIEW_KEY, view);
  }, [view]);

  function selectFloorplan(id: string) {
    setFloorplanId(id);
    localStorage.setItem(FLOORPLAN_KEY, id);
  }

  const selectedFloorplan = floorplans.find((fp) => fp.floorplan_id === floorplanId) ?? null;

  return (
    <div className={`app view-${view}`}>
      <header className="app-header">
        <div className="app-title-group">
          <h1>CMU Wayfinding</h1>
          {floorplans.length > 0 && (
            <label className="floorplan-selector">
              Saved floor
              <select value={floorplanId ?? ""} onChange={(event) => selectFloorplan(event.target.value)}>
                {floorplans.map((fp) => (
                  <option key={fp.floorplan_id} value={fp.floorplan_id}>
                    {fp.building} · floor {fp.floor}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        <nav>
          <button type="button" className={view === "upload" ? "active" : ""} onClick={() => setView("upload")}>
            Upload
          </button>
          <button
            type="button"
            className={view === "editor" ? "active" : ""}
            onClick={() => setView("editor")}
            disabled={!floorplanId}
          >
            Graph editor
          </button>
          <button
            type="button"
            className={view === "route" ? "active" : ""}
            onClick={() => setView("route")}
            disabled={!floorplanId}
          >
            Route
          </button>
        </nav>
      </header>

      <main className={loadingFloors ? "loading-floorplans" : ""}>
        {view === "upload" && (
          <UploadPage
            key={floorplanId ?? "new-floorplan"}
            initialBuilding={selectedFloorplan?.building}
            initialFloor={selectedFloorplan?.floor}
            onUploaded={(id) => {
              void refreshFloorplans(id).then(() => setView("editor"));
            }}
          />
        )}
        {view === "editor" && floorplanId && <GraphEditorPage floorplanId={floorplanId} />}
        {view === "route" && floorplanId && (
          <RoutePage key={floorplanId} floorplanId={floorplanId} />
        )}
      </main>
    </div>
  );
}

export default App;
