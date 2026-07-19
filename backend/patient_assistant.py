"""AI-ассистент пациента — чат с Claude поверх жёстких медицинских гардрейлов.

Ассистент информационный: НИКОГДА не ставит диагнозы и не назначает лечение,
при красных флагах направляет в неотложку. История диалога живёт на клиенте
(stateless сервер): приложение шлёт последние сообщения. Серверный дневной кап
(ASSISTANT_DAILY_CAP) — предохранитель затрат, тарифную границу Free держит
клиентский paywall.
"""
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from llm import _claude_call
from models import AssistantUsage, PatientAccount
from patient_auth import get_current_patient
from rate_limit import limiter

router = APIRouter(prefix="/api/patient/assistant", tags=["patient"])

DAILY_CAP = int(os.getenv("ASSISTANT_DAILY_CAP", "20"))
MAX_HISTORY = 20
MAX_MSG_CHARS = 2000

_LANG_NAME = {"ru": "русском", "tj": "таджикском", "tg": "таджикском", "en": "английском"}

_SYSTEM_PROMPT = """Ты — AI-помощник пациента в приложении Avris (Таджикистан/Центральная Азия).
Твоя роль — информационная поддержка о здоровье: помочь описать симптомы, подготовиться
к визиту, объяснить общие понятия простым языком.

ЖЁСТКИЕ ПРАВИЛА:
- НИКОГДА не ставь диагнозы и не предполагай их («вероятно, у вас…» — запрещено).
  Диагноз ставит только врач.
- НИКОГДА не назначай лекарства и дозировки, не отменяй назначенное врачом.
- Красные флаги (боль в груди, затруднённое дыхание, признаки инсульта, сильное
  кровотечение, потеря сознания, мысли о самоповреждении) → первым делом посоветуй
  НЕМЕДЛЕННО звонить в скорую помощь (в Таджикистане — 103) или обратиться в неотложку.
- При любых заметных или длящихся симптомах мягко направляй к врачу.
- Отвечай тепло, кратко (2-5 предложений), без медицинского жаргона; термин
  неизбежен — поясни в скобках. Обращайся на «вы».
- Можешь задавать уточняющие вопросы о симптомах (когда началось, где, как сильно) —
  это помощь в подготовке к визиту, а не диагностика.
- Не выдумывай факты о состоянии пациента, которых он не сообщал."""


class AssistantMessage(BaseModel):
    role: str  # user | assistant
    text: str = Field(min_length=1, max_length=MAX_MSG_CHARS)


class AssistantRequest(BaseModel):
    messages: List[AssistantMessage] = Field(min_length=1, max_length=MAX_HISTORY)
    language: str = "ru"


class AssistantResponse(BaseModel):
    reply: str
    remaining: Optional[int] = None  # до серверного капа (не тарифного)


def _bump_usage(db: Session, account_id: int) -> int:
    """Инкремент дневного счётчика; вернуть остаток. 429 при превышении капа."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = (db.query(AssistantUsage)
           .filter(AssistantUsage.patient_account_id == account_id,
                   AssistantUsage.day == day)
           .first())
    if row is None:
        row = AssistantUsage(patient_account_id=account_id, day=day, count=0)
        db.add(row)
    if row.count >= DAILY_CAP:
        raise HTTPException(status_code=429,
                            detail="Дневной лимит AI-помощника исчерпан — попробуйте завтра")
    row.count += 1
    db.commit()
    return DAILY_CAP - row.count


@router.post("", response_model=AssistantResponse)
@limiter.limit("10/minute")
async def assistant_chat(
    request: Request,
    payload: AssistantRequest,
    db: Session = Depends(get_db),
    account: PatientAccount = Depends(get_current_patient),
):
    if payload.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="Последнее сообщение должно быть от пациента")

    remaining = _bump_usage(db, account.id)

    lang_name = _LANG_NAME.get(payload.language, "русском")
    convo = "\n".join(
        f"{'Пациент' if m.role == 'user' else 'Помощник'}: {m.text.strip()}"
        for m in payload.messages
    )
    user_msg = (f"Язык ответа: {lang_name}.\n"
                f"{'Имя пациента: ' + account.full_name if account.full_name else ''}\n\n"
                f"Диалог:\n{convo}\n\nПомощник:")
    text = await _claude_call(_SYSTEM_PROMPT, user_msg, max_tokens=700)
    reply = (text or "").strip()
    if not reply:
        raise HTTPException(status_code=502, detail="Пустой ответ модели — повторите попытку")
    return AssistantResponse(reply=reply, remaining=remaining)
