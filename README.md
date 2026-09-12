# 3DCMU — CMU Indoor Wayfinding

Room-to-room walking directions across CMU floor plans. Corridors, doors,
stairs and lifts are derived from the PDFs themselves; routing is plain
Dijkstra over the resulting graph.

## Requirements

- Python 3.11+
- Node 20+

## Run it

Two processes. Backend first.

### 1. Backend — http://localhost:8000

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --port 8000 --reload
```

### 2. Frontend — http://localhost:5173

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The frontend calls the backend at
`http://localhost:8000` (hardcoded in `src/api/client.ts`), so the backend
must be running first.

## Load a floor

The app starts empty — upload a floor before anything works.

On the **Upload** page, drop in the two PDFs for a floor:

| Slot | File | Required |
|---|---|---|
| Base PDF | `WEH-4-ESIM-Base.pdf` | yes |
| Room-type / space-type PDF | `WEH-4-ESIM-Type.pdf` | strongly recommended |
| Occupying organization PDF | `*-Dept.pdf` | optional |

Sample plans are in [`floor_plan/wean_hall/`](floor_plan/wean_hall/).

Uploading does everything else automatically (~3.5s for a large floor):
extracts rooms and their outlines, finds doors, marks stairs and lifts,
traces the corridor centerlines, repairs gaps in the network, and builds the
routing graph. Then go to **Route**, pick a start and destination, and press
*Find route*.

Or upload from the command line:

```bash
cd floor_plan/wean_hall
curl -X POST http://localhost:8000/api/floorplans/upload \
  -F "building=WEH" -F "floor=4" \
  -F "base=@WEH-4-ESIM-Base.pdf;type=application/pdf" \
  -F "type=@WEH-4-ESIM-Type.pdf;type=application/pdf"
```

Upload more than one floor to route between them: stairs and lifts are
matched across floors by room number (`4001A` on floor 4 is the same shaft as
`5001A` on floor 5), and the route is drawn on each floor's plan in walking
order.

**The space-type PDF matters.** With it, corridor detection is fully
deterministic — same result every run, no API calls. Without it the floor
still uploads and rooms/doors are still found, but there is nothing to say
which spaces are circulation, so passage tracing falls back to the optional
Gemini path below (and is noticeably worse).

## Optional: Gemini

Not needed when every floor has a space-type PDF. It is only used as a
fallback, to answer *which numbered spaces are corridors* — never to draw the
paths, which stay geometric either way.

Create `backend/.env` (git-ignored):

```ini
ENABLE_GEMINI_VISION=true
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-3.5-flash
GEMINI_THINKING_LEVEL=medium
```

The same file optionally configures K2 Horizon, which only rephrases the
generated directions into fluent prose; the deterministic turn-by-turn text
is always produced regardless.

```ini
ENABLE_K2_HORIZON=true
K2_HORIZON_ENDPOINT=https://provider.example/v1/chat/completions
K2_HORIZON_API_KEY=your-key
K2_HORIZON_MODEL=k2-horizon
```

## Tests

```bash
cd backend && source .venv/bin/activate && python -m pytest
```

```bash
cd frontend && npx tsc --noEmit -p tsconfig.app.json && npm run lint
```

## Where things live

```
backend/app/services/     extraction, corridor tracing, routing
backend/data/floorplans/  uploaded floors (regenerable; PDFs/PNGs git-ignored)
frontend/src/pages/       Upload, Graph Editor, Route
floor_plan/wean_hall/     sample source PDFs
```

Uploaded state under `backend/data/floorplans/` is disposable — delete it and
re-upload to rebuild from the source PDFs.
