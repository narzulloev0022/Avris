"""Пред-визитное интервью: AI собирает жалобы пациента до приёма.

Пациент приходит в кабинет и половину забывает — волнуется, торопится, не
знает, что важно. Врач тратит первые минуты приёма на то, чтобы вытащить из
него базовое: когда началось, где болит, что уже принимал.

Здесь эта работа делается заранее и в спокойной обстановке: AI задаёт по
одному короткому вопросу, пациент отвечает голосом или текстом, а на выходе
получается заметка на 3-5 строк, которую врач читает за десять секунд перед
приёмом. Это не триаж и не диагностика — это сбор анамнеза со слов пациента.

Жёсткая граница: интервью НИКОГДА не называет диагноз и не советует лечение,
даже если пациент прямо просит. Всё, что оно делает — переспрашивает и
записывает. При красных флагах разговор прекращается и пациента отправляют
в скорую, а не «в заметку для врача на следующей неделе».

Фича бесплатная во всех тарифах: подготовленный пациент — это ценность для
врача, то есть флайуил платформы, а не апселл.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from llm import LIGHT, _llm_call
from models import IntakeUsage, PatientAccount
from patient_auth import get_current_patient
from patient_conversations import (KIND_INTAKE, append_turn, owned_conversation,
                                   start_conversation)
from rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patient/intake", tags=["patient"])

# Сколько реплик уходит модели. Не то же самое, что потолок запроса: длинный
# разговор урезается (см. _context), а не отвергается.
MAX_TURNS = 24
# Жёсткий потолок запроса — защита от мусорного тела, а не рабочий предел.
MAX_MESSAGES = 200
MAX_MSG_CHARS = 1000

# Кап отдельный от ассистента: интервью — часть ядра (оно ведёт к визиту),
# а не платная фича, но и жечь его в цикле нельзя. Считается в своей таблице
# (IntakeUsage) — префикс в ключе дня не влезал в VARCHAR(10) и уронил бы
# Postgres в проде.
DAILY_CAP = int(os.getenv("INTAKE_DAILY_CAP", "12"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")
# Подсказка Whisper: пациент описывает симптомы своими словами, а не ведёт
# клинический диалог — регистр другой, чем на приёме у врача.
WHISPER_PROMPT = "Пациент рассказывает о своих симптомах и жалобах."
MAX_AUDIO_BYTES = 25 * 1024 * 1024

_LANG_NAME = {"ru": "русском", "tj": "таджикском", "tg": "таджикском", "en": "английском"}

_SYSTEM_PROMPT = """Ты помогаешь пациенту подготовиться к приёму у врача в приложении Avris.
Твоя единственная задача — собрать со слов пациента то, что врачу полезно знать
ДО приёма, и в конце составить короткую заметку для врача.

ЖЁСТКИЕ ПРАВИЛА:
- НИКОГДА не называй диагноз и не предполагай его («похоже на…», «возможно, у вас…» — запрещено).
- НИКОГДА не назначай лечение, лекарства, дозировки и обследования.
- Не оценивай, опасно это или нет. Не успокаивай словами «ничего страшного».
- Красные флаги (боль в груди, затруднённое дыхание, признаки инсульта, сильное
  кровотечение, потеря сознания, судороги, мысли о самоповреждении, температура
  выше 39 у ребёнка) → немедленно скажи звонить в скорую (в Таджикистане 103) и
  заверши интервью: verdict="emergency".
- Задавай РОВНО ОДИН короткий вопрос за раз, простым языком, без медицинского жаргона.
- Не задавай вопросов, ответ на которые уже прозвучал.

ЧТО НУЖНО ВЫЯСНИТЬ (по одному вопросу, максимум 6 вопросов всего):
1) что беспокоит и где именно;
2) когда началось и как менялось;
3) характер и сила (по ощущениям пациента), что усиливает или облегчает;
4) что уже принимал по этому поводу;
5) температура, если уместно;
6) что пациент хочет спросить у врача.

Когда данных достаточно (или пациент говорит, что это всё) — заверши интервью.

ФОРМАТ ОТВЕТА. Всегда возвращай СТРОГО JSON без markdown:
{
  "reply": "твой следующий вопрос пациенту — или прощание, если интервью завершено",
  "done": false,
  "verdict": "ok",
  "note": null
}
Когда интервью завершено: "done": true, а "note" — заметка для врача от лица
пациента, 3-5 коротких строк, каждая с новой строки, БЕЗ выводов и диагнозов,
только факты со слов пациента и его вопросы. Держись 3-5 строк; жёсткий
потолок — 1000 символов.
При красных флагах: "done": true, "verdict": "emergency", "note" — те же факты,
"reply" — призыв немедленно обратиться в скорую."""


class IntakeMessage(BaseModel):
    role: str  # user | assistant
    text: str = Field(min_length=1, max_length=MAX_MSG_CHARS)


class IntakeRequest(BaseModel):
    messages: List[IntakeMessage] = Field(default_factory=list, max_length=MAX_MESSAGES)
    language: str = "ru"
    # Продолжаем существующий разговор; null — начать новый.
    conversation_id: Optional[int] = None


class IntakeResponse(BaseModel):
    reply: str
    done: bool = False
    verdict: str = "ok"  # ok | emergency
    note: Optional[str] = None
    conversation_id: Optional[int] = None


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _bump_usage(db: Session, account_id: int) -> None:
    """Дневной предохранитель. Считается отдельной строкой от ассистента:
    один и тот же счётчик на две разные фичи означал бы, что подготовка к
    приёму съедает лимит вопросов о здоровье."""
    day = _today_key()
    row = (db.query(IntakeUsage)
           .filter(IntakeUsage.patient_account_id == account_id,
                   IntakeUsage.day == day)
           .first())
    if row is None:
        row = IntakeUsage(patient_account_id=account_id, day=day, count=0)
        db.add(row)
    if row.count >= DAILY_CAP:
        raise HTTPException(status_code=429,
                            detail="Слишком много подготовок за сегодня — попробуйте завтра")
    row.count += 1
    db.commit()


def _release(db: Session, account_id: int) -> None:
    row = (db.query(IntakeUsage)
           .filter(IntakeUsage.patient_account_id == account_id,
                   IntakeUsage.day == _today_key())
           .first())
    if row is not None and row.count > 0:
        row.count -= 1
        db.commit()


def _context(messages: List[IntakeMessage]) -> List[IntakeMessage]:
    """Что уходит модели из длинного разговора.

    Раньше список был ограничен на входе, и 25-е сообщение отвергалось с 422:
    интервью вставало намертво, а пациент видел только «не получилось» — при
    том, что он как раз добросовестно всё рассказывал.

    Урезаем середину, а не хвост: первые реплики — это главная жалоба, ради
    которой всё и затевалось, и терять их нельзя. Последние нужны, чтобы
    модель не переспрашивала то, что только что услышала.
    """
    if len(messages) <= MAX_TURNS:
        return messages
    head = messages[:2]
    return head + messages[-(MAX_TURNS - len(head)):]


def _parse(raw: str) -> IntakeResponse:
    """Терпимый разбор ответа модели.

    Если модель ответила не JSON — считаем весь текст очередным вопросом:
    сломанный формат не должен обрывать пациенту разговор.
    """
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and str(obj.get("reply") or "").strip():
                note = str(obj.get("note") or "").strip() or None
                return IntakeResponse(
                    reply=str(obj["reply"]).strip(),
                    done=bool(obj.get("done")),
                    verdict="emergency" if obj.get("verdict") == "emergency" else "ok",
                    note=note[:1000] if note else None,
                )
        except (ValueError, TypeError):
            pass
    text = (raw or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="Пустой ответ модели — повторите попытку")
    return IntakeResponse(reply=text)


@router.post("", response_model=IntakeResponse)
@limiter.limit("20/minute")
async def intake_turn(
    request: Request,
    payload: IntakeRequest,
    db: Session = Depends(get_db),
    account: PatientAccount = Depends(get_current_patient),
):
    """Один шаг интервью. Пустой список сообщений = начало разговора."""
    if payload.messages and payload.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="Последнее сообщение должно быть от пациента")

    _bump_usage(db, account.id)

    lang_name = _LANG_NAME.get(payload.language, "русском")
    if payload.messages:
        convo = "\n".join(
            f"{'Пациент' if m.role == 'user' else 'Помощник'}: {m.text.strip()}"
            for m in _context(payload.messages)
        )
    else:
        convo = "(разговор ещё не начался — задай первый вопрос)"
    user_msg = f"Язык ответа: {lang_name}.\n\nДиалог:\n{convo}"

    try:
        # Интервью задаёт вопросы и складывает ответы пациента в структуру.
        # Выводов не делает — их делает врач, читая собранное.
        raw = await _llm_call(_SYSTEM_PROMPT, user_msg, max_tokens=700, tier=LIGHT)
    except Exception:
        _release(db, account.id)
        raise
    result = _parse(raw)

    # Разговор сохраняем ПОСЛЕ успешного ответа: обрыв связи не должен
    # оставлять в истории вопрос пациента без реакции.
    convo = (owned_conversation(db, payload.conversation_id, account)
             if payload.conversation_id else start_conversation(db, account, KIND_INTAKE))
    if payload.messages:
        append_turn(db, convo, "user", payload.messages[-1].text.strip())
    append_turn(db, convo, "assistant", result.reply)
    db.commit()
    result.conversation_id = convo.id
    return result


@router.post("/transcribe")
@limiter.limit("30/minute")
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    account: PatientAccount = Depends(get_current_patient),
):
    """Распознавание речи для пациента.

    Отдельно от `/api/stt/transcribe`: тот эндпоинт doctor-scoped и требует
    врачебный токен, а рассказывать о своих симптомах голосом должен уметь
    пациент. Аудио никуда не сохраняется — только проксируется в Whisper.
    """
    if not OPENAI_API_KEY:
        logger.error("Patient transcription refused: OPENAI_API_KEY is not configured")
        raise HTTPException(status_code=503,
                            detail="Распознавание речи временно недоступно — попробуйте позже")

    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Пустая запись")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Запись слишком длинная")

    files = {"file": (file.filename or "audio.m4a", audio, file.content_type or "audio/m4a")}
    data = {"model": WHISPER_MODEL, "prompt": WHISPER_PROMPT, "response_format": "json"}
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            r = await client.post(OPENAI_TRANSCRIBE_URL, headers=headers, files=files, data=data)
        except httpx.HTTPError as e:
            logger.error("Whisper request failed: %s", e)
            raise HTTPException(status_code=502, detail="Не удалось распознать речь")

    if r.status_code != 200:
        # Тело ответа не логируем: в нём может быть речь пациента.
        logger.warning("Whisper returned %d for patient transcribe", r.status_code)
        raise HTTPException(status_code=502, detail="Не удалось распознать речь")

    return {"text": (r.json().get("text") or "").strip()}
