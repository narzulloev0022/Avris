"""AI-ассистент пациента — чат с LLM поверх жёстких медицинских гардрейлов.

Ассистент информационный: НИКОГДА не ставит диагнозы и не назначает лечение,
при красных флагах направляет в неотложку. Контекст модели по-прежнему шлёт
клиент (последние сообщения), но сам разговор сохраняется на сервере — см.
patient_conversations.py: пациент должен иметь возможность вернуться к тому,
что рассказал о своём здоровье.

Лимиты держит СЕРВЕР, и их два: месячный — то, что тариф продаёт, дневной —
предохранитель от всплеска внутри месяца. Оба тарифные, оба живут в
patient_subscription.py.

История граблей на этом месте. Сначала кап был общий на всех
(ASSISTANT_DAILY_CAP=20) — бесплатный пользователь получал в 6 раз больше
обещанного, а тарифную границу «рисовал» только клиент. Потом кап стал
тарифным, но остался только дневным — и обнулялся каждые сутки, так что
месяц не был ограничен ничем: при выборе дневного капа тариф уходил в
глубокий минус. Отсюда месячный потолок.
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from llm import _llm_call
from models import AssistantUsage, PatientAccount
from patient_auth import get_current_patient
from patient_conversations import (KIND_ASSISTANT, append_turn, owned_conversation,
                                   start_conversation)
from patient_subscription import (FREE, assistant_daily_cap,
                                  assistant_monthly_cap, month_total,
                                  resolve_tier)
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
    # Продолжаем существующий разговор; null — начать новый.
    conversation_id: Optional[int] = None


class AssistantResponse(BaseModel):
    reply: str
    # Остаток тарифа — меньший из дневного и месячного, тот, что кончится раньше.
    remaining: Optional[int] = None
    tier: Optional[str] = None       # чтобы клиент не гадал, чей это лимит
    conversation_id: Optional[int] = None


def _usage_row(db: Session, account_id: int) -> Optional[AssistantUsage]:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (db.query(AssistantUsage)
            .filter(AssistantUsage.patient_account_id == account_id,
                    AssistantUsage.day == day)
            .first())


def _reserve(db: Session, account: PatientAccount) -> int:
    """Занять слот ДО вызова модели; вернуть остаток. 429 при исчерпании.

    Слот занимается до, а не после ответа: вызов модели длится секунды, и
    параллельные запросы одного аккаунта успевали проскочить проверку все
    разом — free получал больше своих трёх вопросов. Если модель не ответит,
    слот возвращает [_release], так что упавший запрос ничего не стоит
    пациенту.

    Потолка два, и месячный проверяется первым: если кончился он, «попробуйте
    завтра» было бы враньём — ждать придётся до первого числа. Дневной ловит
    всплеск внутри месяца.

    Лимиты берутся из тарифа на каждый запрос: подписка могла истечь между
    вопросами, и тогда остаток честно пересчитывается по free.
    """
    daily_cap = assistant_daily_cap(account)
    monthly_cap = assistant_monthly_cap(account)
    # Free упёрся в тарифную границу — ему есть куда расти, платному остаётся
    # только подождать.
    is_free = resolve_tier(account) == FREE

    # Считаем месяц до создания сегодняшней строки, чтобы пустая строка не
    # участвовала в сумме даже нулём.
    used_month = month_total(db, AssistantUsage, account.id)
    if used_month >= monthly_cap:
        raise HTTPException(status_code=429, detail=(
            "Бесплатные вопросы AI-помощнику на этот месяц закончились — "
            "оформите Plus, чтобы продолжить" if is_free else
            "Лимит AI-помощника на этот месяц исчерпан — "
            "обновится первого числа"))

    row = _usage_row(db, account.id)
    if row is None:
        row = AssistantUsage(patient_account_id=account.id,
                             day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                             count=0)
        db.add(row)
    if row.count >= daily_cap:
        raise HTTPException(status_code=429, detail=(
            "Бесплатный лимит AI-помощника на сегодня исчерпан — "
            "оформите Plus, чтобы спрашивать чаще" if is_free else
            "Дневной лимит AI-помощника исчерпан — попробуйте завтра"))
    row.count += 1
    db.commit()
    # Наружу отдаём тот остаток, который кончится раньше, — обещать больший из
    # двух значит соврать на следующем же вопросе.
    return max(0, min(daily_cap - row.count, monthly_cap - used_month - 1))


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
        text = await _llm_call(_SYSTEM_PROMPT, user_msg, max_tokens=700)
        reply = (text or "").strip()
        if not reply:
            raise HTTPException(status_code=502, detail="Пустой ответ модели — повторите попытку")
    except Exception:
        _release(db, account.id)
        raise

    # Разговор сохраняем ПОСЛЕ успешного ответа: оборванный запрос не должен
    # оставлять в истории вопрос пациента, на который никто не ответил.
    stored = (owned_conversation(db, payload.conversation_id, account)
              if payload.conversation_id else start_conversation(db, account, KIND_ASSISTANT))
    append_turn(db, stored, "user", payload.messages[-1].text.strip())
    append_turn(db, stored, "assistant", reply)
    db.commit()

    return AssistantResponse(reply=reply, remaining=remaining, tier=resolve_tier(account),
                             conversation_id=stored.id)
