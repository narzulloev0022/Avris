"""Глубокая проверка живости — для нас, не для пользователя.

Публичный `/api/health` отвечает «сервер поднялся». Этого мало: приложение
может исправно отвечать, пока база недоступна, ключ распознавания истёк или
имя модели не задано — врач в такой момент видит 503 на каждом действии, а
мы ничего не знаем.

Здесь проверяется то, без чего приём не состоится. Наружу отдаются только
да/нет: список того, что настроено, — уже карта для того, кто ищет дыру,
поэтому эндпоинт закрыт общим секретом `HEALTH_TOKEN`.

Дорогих вызовов тут нет намеренно. Проба раз в пять минут, умноженная на
месяц, — это счёт за воздух; живость ключей проверяется настоящим вызовом
отдельно и редко (`?probe=live`).
"""
import os
import time

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import text

from database import SessionLocal

router = APIRouter(prefix="/api/health", tags=["health"])

HEALTH_TOKEN = os.getenv("HEALTH_TOKEN", "")


def _db_ok() -> tuple[bool, float]:
    """Круг до базы и обратно. Не `SELECT 1` на соединении из пула — берём
    сессию так же, как её берут эндпоинты, иначе проверка мимо реальности."""
    started = time.perf_counter()
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return True, (time.perf_counter() - started) * 1000
    except Exception:
        return False, (time.perf_counter() - started) * 1000
    finally:
        db.close()


async def _openai_live() -> bool:
    """Список моделей — бесплатный вызов, которого достаточно, чтобы понять,
    что ключ жив и не отозван."""
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.openai.com/v1/models",
                            headers={"Authorization": f"Bearer {key}"})
        return r.status_code == 200
    except httpx.HTTPError:
        return False


async def _llm_live() -> bool:
    """Самый дешёвый настоящий вызов: один токен на выходе. Нужен, потому что
    отозванный ключ и исчерпанный баланс выглядят снаружи одинаково — как
    работающий сервис, пока врач не нажмёт кнопку."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("ANTHROPIC_MODEL", "")
    if not key or not model:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": 1,
                      "messages": [{"role": "user", "content": "ping"}]},
            )
        return r.status_code == 200
    except httpx.HTTPError:
        return False


@router.get("/deep")
async def deep_health(
    x_health_token: str = Header(default=""),
    probe: str = Query(default="", pattern="^(live)?$"),
):
    """Что должно быть настроено, чтобы приём состоялся.

    `?probe=live` дополнительно дёргает внешние API. Дороже и медленнее —
    вызывать раз в час, а не раз в пять минут.
    """
    if not HEALTH_TOKEN:
        raise HTTPException(503, "HEALTH_TOKEN is not configured")
    if x_health_token != HEALTH_TOKEN:
        raise HTTPException(403, "Bad health token")

    db_ok, db_ms = _db_ok()
    checks = {
        "db": db_ok,
        "stt_key": bool(os.getenv("OPENAI_API_KEY")),
        "llm_key": bool(os.getenv("ANTHROPIC_API_KEY")),
        "llm_model": bool(os.getenv("ANTHROPIC_MODEL")),
        "mail_key": bool(os.getenv("RESEND_API_KEY")),
        "jwt_secret": os.getenv("SECRET_KEY", "") not in ("", "dev-secret-change-me"),
    }
    if probe == "live":
        checks["stt_live"] = await _openai_live()
        checks["llm_live"] = await _llm_live()

    failed = sorted(k for k, v in checks.items() if not v)
    body = {"ok": not failed, "failed": failed, "checks": checks,
            "db_ms": round(db_ms, 1)}
    if failed:
        # 503, а не 200 с флагом: наблюдатель снаружи смотрит на код ответа,
        # и «ok: false» с кодом 200 он пропустит.
        raise HTTPException(503, body)
    return body
