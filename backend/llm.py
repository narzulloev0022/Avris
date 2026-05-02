import os
import json
import re
import logging
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv

from auth import get_current_user
from models import User

load_dotenv()
logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_VERSION = "2023-06-01"

LANG_LABEL = {"ru": "русский", "tj": "тоҷикӣ", "en": "English"}

router = APIRouter(prefix="/api/llm", tags=["llm"])


class SoapRequest(BaseModel):
    transcript: str
    language: str = "ru"
    patient_context: Optional[dict[str, Any]] = None


class SoapResponse(BaseModel):
    subjective: str
    objective: str
    assessment: str
    plan: str


class LabsRequest(BaseModel):
    results: dict[str, Any]
    patient_context: Optional[dict[str, Any]] = None


class LabsResponse(BaseModel):
    comment: str


async def _claude_call(system_prompt: str, user_msg: str, max_tokens: int = 1024) -> str:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Anthropic API не настроен (ANTHROPIC_API_KEY)")
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_msg}],
    }
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            r = await client.post(ANTHROPIC_URL, headers=headers, json=body)
        except httpx.HTTPError as e:
            logger.error("Anthropic request failed: %s", e)
            raise HTTPException(status_code=502, detail="Claude недоступен")
    if r.status_code != 200:
        logger.warning("Anthropic %d: %s", r.status_code, r.text[:300])
        raise HTTPException(status_code=r.status_code, detail=f"Ошибка Claude: {r.text[:200]}")
    j = r.json()
    parts = j.get("content", []) or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def _extract_json(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise HTTPException(status_code=502, detail="Claude вернул не JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Claude вернул невалидный JSON")


@router.post("/generate-soap", response_model=SoapResponse)
async def generate_soap(req: SoapRequest, current_user: User = Depends(get_current_user)):
    if not (req.transcript or "").strip():
        raise HTTPException(status_code=400, detail="Пустой транскрипт")
    lang_label = LANG_LABEL.get(req.language, "русский")
    system_prompt = (
        "Ты медицинский AI-ассистент. На основе транскрипта приёма сгенерируй SOAP-документацию. "
        f"Язык ответа: {lang_label}. "
        "S — жалобы пациента (subjective). "
        "O — объективные данные осмотра (objective). "
        "A — оценка/диагноз (assessment). "
        "P — план лечения (plan). "
        'Верни строго валидный JSON: {"subjective":"...","objective":"...","assessment":"...","plan":"..."}. '
        "Без префиксов и дополнительного текста — только JSON-объект."
    )
    user_msg = f"Транскрипт приёма:\n\n{req.transcript}"
    if req.patient_context:
        user_msg += "\n\nКонтекст пациента: " + json.dumps(req.patient_context, ensure_ascii=False)
    text = await _claude_call(system_prompt, user_msg, max_tokens=1024)
    parsed = _extract_json(text)
    return SoapResponse(
        subjective=str(parsed.get("subjective", "")),
        objective=str(parsed.get("objective", "")),
        assessment=str(parsed.get("assessment", "")),
        plan=str(parsed.get("plan", "")),
    )


@router.post("/analyze-labs", response_model=LabsResponse)
async def analyze_labs(req: LabsRequest, current_user: User = Depends(get_current_user)):
    if not req.results:
        raise HTTPException(status_code=400, detail="Пустые результаты")
    system_prompt = (
        "Ты медицинский AI-ассистент. Дай краткий клинический комментарий "
        "к лабораторным результатам пациента: отметь отклонения от нормы и возможные интерпретации. "
        "Не делай окончательных диагнозов — формулируй как наблюдения и рекомендации. "
        "Ответ на русском, 2–4 предложения."
    )
    user_msg = "Результаты анализов: " + json.dumps(req.results, ensure_ascii=False)
    if req.patient_context:
        user_msg += "\n\nКонтекст пациента: " + json.dumps(req.patient_context, ensure_ascii=False)
    text = await _claude_call(system_prompt, user_msg, max_tokens=400)
    return LabsResponse(comment=text)
