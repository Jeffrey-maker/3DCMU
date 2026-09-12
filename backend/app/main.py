from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import floorplans, route

app = FastAPI(title="CMU Wayfinding API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(floorplans.router)
app.include_router(route.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
