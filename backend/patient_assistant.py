"""AI-ассистент пациента — чат с Claude поверх жёстких медицинских гардрейлов.

Ассистент информационный: НИКОГДА не ставит диагнозы и не назначает лечение,
при красных флагах направляет в неотложку. История диалога живёт на клиенте
(stateless сервер): приложение шлёт последние сообщения.

Дневной лимит держит СЕРВЕР и он тарифный: free — 3 вопроса (канон
Monetization.md), платные — fair-use потолок. Раньше кап был общий на всех
(ASSISTANT_DAILY_CAP=20), то есть бесплатный пользователь получал в 6 раз
больше обещанного, а тарифную границу «рисовал» только клиент.
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from llm import _claude_call
from models import AssistantUsage, PatientAccount
from patient_auth import get_current_patient
from patient_subscription import FREE, assistant_daily_cap, resolve_tier
from rate_limit import limiter

router = APIRouter(prefix="/api/patient/assistant", tags=["patient"])

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
    remaining: Optional[int] = None  # остаток дневного лимита текущего тарифа
    tier: Optional[str] = None       # чтобы клиент не гадал, чей это лимит


def _usage_row(db: Session, account_id: int) -> Optional[AssistantUsage]:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (db.query(AssistantUsage)
            .filter(AssistantUsage.patient_account_id == account_id,
                    AssistantUsage.day == day)
            .first())


def _reserve(db: Session, account: PatientAccount) -> int:
    """Занять слот ДО вызова модели; вернуть остаток. 429 при исчерпании.

    Слот занимается до, а не после ответа: вызов Claude длится секунды, и
    параллельные запросы одного аккаунта успевали проскочить проверку все
    разом — free получал больше своих трёх вопросов. Если модель не ответит,
    слот возвращает [_release], так что упавший запрос ничего не стоит
    пациенту.

    Лимит берётся из тарифа на каждый запрос: подписка могла истечь между
    вопросами, и тогда остаток честно пересчитывается по free.
    """
    cap = assistant_daily_cap(account)
    row = _usage_row(db, account.id)
    if row is None:
        row = AssistantUsage(patient_account_id=account.id,
                             day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                             count=0)
        db.add(row)
    if row.count >= cap:
        # Free упёрся в тарифную границу — ему есть куда расти, платному
        # остаётся только подождать сутки.
        if resolve_tier(account) == FREE:
            detail = ("Бесплатный лимит AI-помощника на сегодня исчерпан — "
                      "оформите Plus, чтобы спрашивать без ограничений")
        else:
            detail = "Дневной лимит AI-помощника исчерпан — попробуйте завтра"
        raise HTTPException(status_code=429, detail=detail)
    row.count += 1
    db.commit()
    return max(0, cap - row.count)


def _release(db: Session, account_id: int) -> None:
    """Вернуть слот, если модель так и не ответила."""
    row = _usage_row(db, account_id)
    if row is not None and row.count > 0:
        row.count -= 1
        db.commit()


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

    remaining = _reserve(db, account)

    lang_name = _LANG_NAME.get(payload.language, "русском")
    convo = "\n".join(
        f"{'Пациент' if m.role == 'user' else 'Помощник'}: {m.text.strip()}"
        for m in payload.messages
    )
    user_msg = (f"Язык ответа: {lang_name}.\n"
                f"{'Имя пациента: ' + account.full_name if account.full_name else ''}\n\n"
                f"Диалог:\n{convo}\n\nПомощник:")
    try:
        text = await _claude_call(_SYSTEM_PROMPT, user_msg, max_tokens=700)
        reply = (text or "").strip()
        if not reply:
            raise HTTPException(status_code=502, detail="Пустой ответ модели — повторите попытку")
    except Exception:
        _release(db, account.id)
        raise
    return AssistantResponse(reply=reply, remaining=remaining, tier=resolve_tier(account))
