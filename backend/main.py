import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from dotenv import load_dotenv
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

load_dotenv()

# Логи приложения настраиваем ВСЕГДА, а не только под LOG_JSON=1.
#
# Раньше basicConfig стоял внутри условия, и в проде (где LOG_JSON не задан)
# корневой логгер оставался без обработчика: всё, что код пишет через
# logging.getLogger(...).info(), исчезало бесследно. В Railway не было видно
# ни «Sentry enabled», ни предупреждений вроде «сводка визита отброшена:
# запрещённая формулировка». Наружу пробивались только логи uvicorn.
#
# LOG_JSON=1 по-прежнему переключает формат на JSON для агрегаторов.
if os.getenv("LOG_JSON") == "1":
    class _JsonFormatter(logging.Formatter):
        def format(self, record):
            entry = {
                "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                entry["exc"] = self.formatException(record.exc_info)
            return json.dumps(entry, ensure_ascii=False)

    _handler = logging.StreamHandler()
    _handler.setFormatter(_JsonFormatter())
else:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)

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
from observability import init_sentry
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
from waitlist import router as waitlist_router
from patient_auth import router as patient_auth_router
from patient_api import router as patient_api_router
from patient_links import patient_router as patient_links_patient_router, doctor_router as patient_links_doctor_router
from patient_visits import router as patient_visits_router
from patient_labs import router as patient_labs_router
from patient_glucose import router as patient_glucose_router
from patient_health import router as patient_health_router
from patient_insights import router as patient_insights_router
from patient_monitoring import CHECK_INTERVAL_SECONDS as _MONITOR_INTERVAL
from patient_monitoring import router as patient_monitoring_router
from patient_monitoring import run_all as run_monitoring
from patient_nutrition import router as patient_nutrition_router
from patient_assistant import router as patient_assistant_router
from patient_conversations import router as patient_conversations_router
from patient_checkins import router as patient_checkins_router
from patient_goals import router as patient_goals_router
from patient_devices import router as patient_devices_router
from patient_intake import router as patient_intake_router
from patient_subscription import router as patient_subscription_router
from patient_export import router as patient_export_router
from epicrises import router as epicrises_router
from rate_limit import limiter

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8080")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "index.html"


def monitoring_interval() -> int:
    """Пауза между обходами мониторинга. Отдельной функцией, чтобы тест мог
    сократить её, не трогая продовое значение."""
    return _MONITOR_INTERVAL


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Отчёты об ошибках — до всего остального, чтобы падение на старте тоже
    # было видно. Без SENTRY_DSN это no-op.
    if init_sentry():
        logging.getLogger("avris").info("Sentry enabled")
    init_db()

    # Мониторинг показателей: обход раз в CHECK_INTERVAL_SECONDS прямо в
    # процессе приложения. Отдельный планировщик здесь был бы лишней
    # движущейся частью — обход занимает секунды и не держит соединений.
    # Задача снимается при остановке, иначе перезапуск копил бы обходы.
    monitor = asyncio.create_task(_monitoring_loop())
    try:
        yield
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor


async def _monitoring_loop():
    while True:
        # Первый сон до первой проверки: на старте сервера база ещё может
        # мигрироваться, а находки никуда не убегут.
        await asyncio.sleep(monitoring_interval())
        try:
            created = await asyncio.to_thread(run_monitoring)
            if created:
                logging.getLogger("avris").info("monitoring: %s new alerts", created)
        except Exception:
            logging.getLogger("avris").exception("monitoring sweep failed")


# Схема API в проде закрыта. /docs, /redoc и /openapi.json отдавали полную
# карту сервиса — сотню эндпоинтов с именами полей и схемами, включая
# админские и пациентские. Само по себе это не дыра, но это подробная
# инструкция для того, кто будет искать дыру. Открыть на время отладки:
# API_DOCS=1 в окружении.
_DOCS_OPEN = os.getenv("API_DOCS", "").strip() in {"1", "true", "yes"}

app = FastAPI(
    title="Avris AI Backend",
    version="0.1.0",
    description="Hyperion Labs · Avris AI — голосовая платформа медицинской документации",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_OPEN else None,
    redoc_url="/redoc" if _DOCS_OPEN else None,
    openapi_url="/openapi.json" if _DOCS_OPEN else None,
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
app.include_router(waitlist_router)
app.include_router(patient_auth_router)
app.include_router(patient_api_router)
app.include_router(patient_links_patient_router)
app.include_router(patient_links_doctor_router)
app.include_router(patient_visits_router)
app.include_router(patient_labs_router)
app.include_router(patient_nutrition_router)
app.include_router(patient_insights_router)
app.include_router(patient_glucose_router)
app.include_router(patient_monitoring_router)
app.include_router(patient_health_router)
app.include_router(patient_assistant_router)
app.include_router(patient_subscription_router)
app.include_router(patient_conversations_router)
app.include_router(patient_checkins_router)
app.include_router(patient_goals_router)
app.include_router(patient_devices_router)
app.include_router(patient_intake_router)
app.include_router(patient_export_router)
app.include_router(epicrises_router)


@app.get("/api/health")
def health():
    # pdf_font — ops-сигнал: "AvrisFont" = кириллический TTF зарегистрирован,
    # "Helvetica" = фолбэк, кириллица в PDF будет квадратами (нет шрифта в образе).
    import pdf_export
    pdf_export._register_fonts()
    return {"status": "ok", "service": "avris-backend", "version": "0.1.0",
            "pdf_font": pdf_export._FONT_NAME}


def _waitlist_live() -> bool:
    """Launch switch for the marketing landing. Until WAITLIST_LIVE=1 is set,
    the root keeps serving the product app and /waitlist redirects home —
    lets us finish the page in main without publishing it."""
    return os.getenv("WAITLIST_LIVE") == "1"


@app.get("/")
def serve_root():
    """Marketing waitlist is the public face (when live); the app lives at /app."""
    if _waitlist_live() and WAITLIST_HTML.exists():
        return FileResponse(WAITLIST_HTML)
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return {"message": "Avris backend running."}


@app.get("/app")
@app.get("/app/")
def serve_app():
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return {"message": "Frontend not found at " + str(INDEX_HTML)}


@app.get("/app/{sub_path:path}")
def serve_app_deep(sub_path: str):
    """Любой под-адрес /app/... отдаёт то же приложение.

    Без этого ссылка на конкретный экран возвращала 404: врач не мог ни
    переслать её коллеге, ни просто нажать F5 не на корне. Роутинг внутри
    SPA свой, серверу достаточно вернуть оболочку.
    """
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return {"message": "Frontend not found at " + str(INDEX_HTML)}


WAITLIST_HTML = PROJECT_ROOT / "marketing" / "waitlist.html"


@app.get("/waitlist")
def serve_waitlist():
    if _waitlist_live() and WAITLIST_HTML.exists():
        return FileResponse(WAITLIST_HTML)
    return RedirectResponse("/", status_code=307)


PRICING_HTML = PROJECT_ROOT / "marketing" / "pricing.html"


@app.get("/pricing")
@app.get("/tarify")
def serve_pricing():
    """Тарифы — публичная страница, живёт независимо от переключателя лендинга:
    ссылку на неё мы кладём в письма клиникам, и она не должна ломаться от
    того, вышел ли маркетинговый корень."""
    if PRICING_HTML.exists():
        return FileResponse(PRICING_HTML)
    return RedirectResponse("/", status_code=307)


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


SW_JS = PROJECT_ROOT / "sw.js"
MANIFEST_JSON = PROJECT_ROOT / "manifest.json"


@app.get("/sw.js")
def serve_sw():
    if SW_JS.exists():
        # no-cache: SW updates must not sit in the CDN for hours — the browser
        # revalidates on every page load and picks up new versions immediately.
        return FileResponse(SW_JS, media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})
    return {"message": "sw.js not found"}


@app.get("/manifest.json")
def serve_manifest():
    if MANIFEST_JSON.exists():
        return FileResponse(MANIFEST_JSON, media_type="application/manifest+json")
    return {"message": "manifest.json not found"}


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


NOT_FOUND_HTML = PROJECT_ROOT / "404.html"


@app.exception_handler(StarletteHTTPException)
async def not_found_page(request, exc: StarletteHTTPException):
    """Человеку — страница, машине — JSON.

    Раньше опечатка в адресе давала врачу голый {"detail":"Not Found"}: ни
    бренда, ни пути назад. Клиентам API это по-прежнему нужно в JSON, поэтому
    развилка идёт по префиксу пути, а не по заголовку Accept — заголовок
    браузера слишком часто врёт.
    """
    if (exc.status_code == 404
            and not request.url.path.startswith("/api/")
            and NOT_FOUND_HTML.exists()):
        return FileResponse(NOT_FOUND_HTML, status_code=404)
    return await http_exception_handler(request, exc)


# Mount any sibling static assets (favicons, future bundled JS) if present
if (PROJECT_ROOT / "assets").exists():
    app.mount("/assets", StaticFiles(directory=PROJECT_ROOT / "assets"), name="assets")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RAILWAY_ENVIRONMENT") is None and os.getenv("ENV", "dev") != "production"
    # 0.0.0.0 намеренно: в контейнере Railway нужно слушать все интерфейсы,
    # наружу порт открывает только edge-прокси Railway.
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload)  # nosec B104
