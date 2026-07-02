import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

load_dotenv()

# Refuse to boot in production with a default/missing SECRET_KEY — the
# default value is public knowledge from the source tree, so any deploy
# that forgets to set it would let anyone forge admin JWTs.
_secret = os.getenv("SECRET_KEY", "dev-secret-change-me")
if _secret == "dev-secret-change-me" and os.getenv("RAILWAY_ENVIRONMENT"):
    raise RuntimeError(
        "SECRET_KEY env var must be set in production. "
        "Refusing to boot with the public default."
    )

from database import init_db
from auth import router as auth_router
from stt import router as stt_router
from llm import router as llm_router
from consultations import router as consultations_router
from patients import router as patients_router
from lab_orders import router as lab_orders_router
from night_rounds import router as night_rounds_router
from admin import router as admin_router
from notifications import router as notifications_router
from icd10 import router as icd10_router
from drugs import router as drugs_router
from stats import router as stats_router
from rate_limit import limiter

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

# Rate limiting — slowapi reads the limiter off app.state on each request and
# turns RateLimitExceeded into a 429 with a Retry-After header.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Origin allow-list — prod is strictly theavris.ai. Local dev adds its origin
# only via the FRONTEND_URL env var (e.g. http://localhost:8080), never in prod.
_allowed = ["https://theavris.ai"]
_extra = os.getenv("FRONTEND_URL")
if _extra and _extra not in _allowed:
    _allowed.append(_extra)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],  # pagination total for list endpoints
)


# Security headers on every response (defence-in-depth for medical data).
# HTTPS redirect is handled by Cloudflare (301), so no HTTPSRedirectMiddleware
# here — adding it behind the CF proxy would risk a redirect loop.
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://api.anthropic.com https://api.openai.com; "
        "media-src 'self' blob:; "
        "frame-ancestors 'none';"
    )
    return response

app.include_router(auth_router)
app.include_router(stt_router)
app.include_router(llm_router)
app.include_router(consultations_router)
app.include_router(patients_router)
app.include_router(lab_orders_router)
app.include_router(night_rounds_router)
app.include_router(admin_router)
app.include_router(notifications_router)
app.include_router(icd10_router)
app.include_router(drugs_router)
app.include_router(stats_router)


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


ADMIN_HTML = PROJECT_ROOT / "admin.html"


@app.get("/admin")
@app.get("/admin.html")
def serve_admin():
    if ADMIN_HTML.exists():
        return FileResponse(ADMIN_HTML)
    return {"message": "Admin panel not found"}


STYLES_CSS = PROJECT_ROOT / "styles.css"
APP_JS = PROJECT_ROOT / "app.js"


@app.get("/styles.css")
def serve_styles():
    if STYLES_CSS.exists():
        return FileResponse(STYLES_CSS, media_type="text/css")
    return {"message": "styles.css not found"}


@app.get("/app.js")
def serve_app_js():
    if APP_JS.exists():
        return FileResponse(APP_JS, media_type="application/javascript")
    return {"message": "app.js not found"}


# Mount any sibling static assets (favicons, future bundled JS) if present
if (PROJECT_ROOT / "assets").exists():
    app.mount("/assets", StaticFiles(directory=PROJECT_ROOT / "assets"), name="assets")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RAILWAY_ENVIRONMENT") is None and os.getenv("ENV", "dev") != "production"
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload)
