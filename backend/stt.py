import os
import logging
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Request
import httpx
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import User, TrainingPair
from schemas import TrainingPairCreate, TrainingPairResponse
from rate_limit import limiter

load_dotenv()
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

# Short prompt — biases Whisper toward the medical register without
# locking it into a specific language. Whisper-1 auto-detects per phrase,
# which handles RU/EN well; TJ falls back to Cyrillic via Russian.
WHISPER_PROMPT = "Medical appointment between doctor and patient."

# Hard cap on per-request audio size. OpenAI's own limit is 25 MB; we mirror
# that here so a runaway client (or a malicious one) can't push gigabyte
# uploads through our backend before OpenAI rejects them.
MAX_AUDIO_BYTES = 25 * 1024 * 1024

router = APIRouter(prefix="/api/stt", tags=["stt"])


@router.post("/transcribe")
@limiter.limit("30/minute")
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    language: str = Form("ru"),  # accepted for API compat; not forwarded — Whisper auto-detects
    current_user: User = Depends(get_current_user),
):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OpenAI Whisper не настроен (OPENAI_API_KEY)")

    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Пустой аудио файл")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Аудио файл слишком большой (максимум {MAX_AUDIO_BYTES // (1024*1024)} МБ)",
        )

    files = {"file": (file.filename or "audio.webm", audio, file.content_type or "audio/webm")}
    data = {
        "model": WHISPER_MODEL,
        "prompt": WHISPER_PROMPT,
        "response_format": "verbose_json",
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            r = await client.post(OPENAI_TRANSCRIBE_URL, headers=headers, files=files, data=data)
        except httpx.HTTPError as e:
            logger.error("Whisper request failed: %s", e)
            raise HTTPException(status_code=502, detail="Whisper недоступен")

    if r.status_code != 200:
        logger.warning("Whisper returned %d (тело ответа скрыто — возможны PHI)", r.status_code)
        raise HTTPException(status_code=r.status_code, detail=f"Ошибка Whisper ({r.status_code})")

    j = r.json()
    return {
        "text": j.get("text", ""),
        "duration": j.get("duration"),
        "language": j.get("language", language),
    }


@router.post("/training-pair", response_model=TrainingPairResponse, status_code=201)
@limiter.limit("60/minute")
def create_training_pair(
    request: Request,
    payload: TrainingPairCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Continuous Learning Pipeline — Data Collector.

    Stores a [raw + corrected transcript] pair for STT fine-tuning. Persisted
    only when the doctor has explicitly consented (opt-in); without consent the
    pair is discarded and we return 403. PHI cleaning and S3 audio linkage happen
    downstream in the nightly training pipeline. See STT/Continuous-Learning-Pipeline.md.
    """
    if not payload.consent:
        raise HTTPException(status_code=403, detail="Сбор данных требует согласия врача (consent)")

    pair = TrainingPair(
        session_id=payload.session_id,
        raw_transcript=payload.raw_transcript,
        corrected_transcript=payload.corrected_transcript,
        language=payload.language,
        specialty=current_user.specialty,
        consent=True,
    )
    db.add(pair)
    db.commit()
    db.refresh(pair)
    return pair
