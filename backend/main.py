import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from database import init_db
from auth import router as auth_router
from stt import router as stt_router
from llm import router as llm_router
from consultations import router as consultations_router
from patients import router as patients_router
from lab_orders import router as lab_orders_router

load_dotenv()

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8080")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Avris AI Backend",
    version="0.1.0",
    description="Hyperion Labs · Avris AI — голосовая платформа медицинской документации",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "https://theavris.ai",
        "https://www.theavris.ai",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(stt_router)
app.include_router(llm_router)
app.include_router(consultations_router)
app.include_router(patients_router)
app.include_router(lab_orders_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "avris-backend", "version": "0.1.0"}


@app.get("/")
def serve_frontend():
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return {"message": "Avris backend running. Frontend not found at " + str(INDEX_HTML)}


LAB_HTML = PROJECT_ROOT / "lab.html"


@app.get("/lab")
@app.get("/lab.html")
def serve_lab():
    if LAB_HTML.exists():
        return FileResponse(LAB_HTML)
    return {"message": "Lab portal not found"}


# Mount any sibling static assets (favicons, future bundled JS) if present
if (PROJECT_ROOT / "assets").exists():
    app.mount("/assets", StaticFiles(directory=PROJECT_ROOT / "assets"), name="assets")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
