from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.services.suggestions import FloorSuggestionSet, extract_suggestions


app = FastAPI(title="Contour ESIM suggestion service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/extract-suggestions", response_model=FloorSuggestionSet)
async def suggest_annotations(
    pdf: UploadFile = File(...),
    page: int = Form(1),
    level: str = Form(""),
):
    if not (pdf.filename or "").lower().endswith(".pdf") and pdf.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Suggestions require a PDF floorplan.")
    pdf_bytes = await pdf.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="The uploaded PDF is empty.")
    try:
        return await run_in_threadpool(extract_suggestions, pdf_bytes, page, level)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
