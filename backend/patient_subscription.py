"""Тарифные планы пациентского приложения — единственный источник правды о правах.

Канон (KB → Patient App/Monetization.md, 21.07.2026): ядро приложения
(медкарта, визиты, анализы, QR, экстренная карточка, напоминания) бесплатно
ВСЕГДА — это флайуил, врач приводит пациента, CAC ≈ 0. Платное — только
AI-усиления:

  free  0            — AI-ассистент 3 вопроса/день (Haiku)
  plus  65 сомони    — 50 вопросов ассистенту в день, AI-разбор анализов
                       и визитов, экспорт медкарты в PDF
  pro   129 сомони   — всё из Plus + Sonnet-тяжёлое (питание по фото,
                       диабет-контроль), 24/7 мониторинг, приоритетная запись

Клиентский paywall рисует замки, но решает СЕРВЕР: приложение спрашивает
GET /api/patient/subscription и получает и тариф, и лимиты, и список фич.
Каталог цен тоже отдаётся с сервера (GET .../plans) — прайс меняется без
релиза в сторах; названия фич приходят кодами, тексты берутся из ARB клиента.

IAP ещё нет: право выдаётся вручную (``manual``) или dev-эндпоинтом под
env-гейтом. Когда появится StoreKit/Play Billing — валидация чека станет ещё
одним источником, а модель прав не изменится.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import AssistantUsage, PatientAccount
from patient_auth import get_current_patient
from rate_limit import limiter

router = APIRouter(prefix="/api/patient/subscription", tags=["patient"])

FREE = "free"
PLUS = "plus"
PRO = "pro"
TIER_ORDER = [FREE, PLUS, PRO]

# Фичи-коды. Клиент рисует их своими локализованными строками — сюда попадает
# только то, что backend реально гейтит или будет гейтить.
FEATURE_ASSISTANT = "ai_assistant"
FEATURE_LAB_BREAKDOWN = "ai_lab_breakdown"
FEATURE_VISIT_INSIGHTS = "ai_visit_insights"
FEATURE_PDF_EXPORT = "pdf_export"
FEATURE_NUTRITION = "nutrition_photo"
FEATURE_DIABETES = "diabetes_control"
FEATURE_MONITORING = "ai_monitoring_247"
FEATURE_PRIORITY_BOOKING = "priority_booking"
# Профили близких — аргумент Pro по канону. Право заведено заранее: сам
# мультипрофиль ещё не построен, поэтому в приложении фича не обещана —
# гейт готов, включать после реализации.
FEATURE_FAMILY_PROFILES = "family_profiles"


class TierSpec:
    """Права одного тарифа.

    ``assistant_daily`` — дневной кап ассистента. Слово «безлимит» из текстов
    убрано: обещать его нечестно, потому что потолок всё равно есть — без него
    один скрипт сжигает маржу тарифа за сутки. Пороги стоят заведомо выше
    живого использования (человек не задаёт 50 вопросов в день), и в
    приложении названы прямо.
    """

    def __init__(self, code: str, assistant_daily: int, features: List[str],
                 price_tjs: int, price_usd: float):
        self.code = code
        self.assistant_daily = assistant_daily
        self.features = features
        self.price_tjs = price_tjs
        self.price_usd = price_usd


def _cap(env_name: str, default: int) -> int:
    try:
        return int(os.getenv(env_name, str(default)))
    except ValueError:
        return default


TIERS = {
    FREE: TierSpec(
        FREE,
        assistant_daily=_cap("ASSISTANT_FREE_DAILY_CAP", 3),
        features=[FEATURE_ASSISTANT],
        price_tjs=0,
        price_usd=0.0,
    ),
    PLUS: TierSpec(
        PLUS,
        assistant_daily=_cap("ASSISTANT_PLUS_DAILY_CAP", 50),
        features=[
            FEATURE_ASSISTANT,
            FEATURE_LAB_BREAKDOWN,
            FEATURE_VISIT_INSIGHTS,
            FEATURE_PDF_EXPORT,
        ],
        price_tjs=65,
        price_usd=6.99,
    ),
    PRO: TierSpec(
        PRO,
        assistant_daily=_cap("ASSISTANT_PRO_DAILY_CAP", 200),
        features=[
            FEATURE_ASSISTANT,
            FEATURE_LAB_BREAKDOWN,
            FEATURE_VISIT_INSIGHTS,
            FEATURE_PDF_EXPORT,
            FEATURE_NUTRITION,
            FEATURE_DIABETES,
            FEATURE_MONITORING,
            FEATURE_PRIORITY_BOOKING,
            FEATURE_FAMILY_PROFILES,
        ],
        price_tjs=129,
        price_usd=13.99,
    ),
}


def resolve_tier(account: PatientAccount) -> str:
    """Действующий тариф аккаунта.

    Истечение считается на чтении, а не фоновым джобом: платный тариф с
    прошедшим ``subscription_expires_at`` немедленно ведёт себя как free.
    Неизвестное значение в колонке тоже сваливается в free — деньги мы уже
    взяли или нет, но лишних прав выдавать нельзя.
    """
    tier = (account.subscription_tier or FREE).strip().lower()
    if tier not in TIERS:
        return FREE
    if tier == FREE:
        return FREE
    expires = account.subscription_expires_at
    if expires is not None:
        # В БД лежит наивный UTC (как и остальные timestamps проекта).
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            return FREE
    return tier


def assistant_daily_cap(account: PatientAccount) -> int:
    return TIERS[resolve_tier(account)].assistant_daily


def has_feature(account: PatientAccount, feature: str) -> bool:
    return feature in TIERS[resolve_tier(account)].features


def require_feature(account: PatientAccount, feature: str) -> None:
    """Гейт платной фичи. 402 (а не 403) — это «нужна оплата», не «запрещено»:
    клиент по этому коду открывает экран тарифов, а не ошибку доступа."""
    if not has_feature(account, feature):
        raise HTTPException(
            status_code=402,
            detail="Функция доступна в платном тарифе — откройте «Тарифы» в приложении",
        )


def _min_tier_for(feature: str) -> str:
    for code in TIER_ORDER:
        if feature in TIERS[code].features:
            return code
    return PRO


# ---------- Schemas ----------

class SubscriptionOut(BaseModel):
    tier: str
    expires_at: Optional[datetime] = None
    source: Optional[str] = None
    features: List[str]
    assistant_daily_cap: int
    assistant_used_today: int
    assistant_remaining_today: int


class PlanOut(BaseModel):
    """Карточка тарифа для экрана «Тарифы». Тексты — на клиенте (ARB), сюда
    приходят только коды фич и цены, чтобы прайс менялся без релиза."""
    tier: str
    price_tjs: int
    price_usd: float
    features: List[str]
    assistant_daily_cap: int


class PlansOut(BaseModel):
    currency: str = "TJS"
    current_tier: str
    plans: List[PlanOut]


class DevSetRequest(BaseModel):
    tier: str
    days: int = 30


# ---------- Helpers ----------

def _used_today(db: Session, account_id: int) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = (db.query(AssistantUsage)
           .filter(AssistantUsage.patient_account_id == account_id,
                   AssistantUsage.day == day)
           .first())
    return row.count if row else 0


def _subscription_out(db: Session, account: PatientAccount) -> SubscriptionOut:
    tier = resolve_tier(account)
    spec = TIERS[tier]
    used = _used_today(db, account.id)
    return SubscriptionOut(
        tier=tier,
        # Срок отдаём только пока он действует — истёкший тариф уже free.
        expires_at=account.subscription_expires_at if tier != FREE else None,
        source=account.subscription_source if tier != FREE else None,
        features=spec.features,
        assistant_daily_cap=spec.assistant_daily,
        assistant_used_today=used,
        assistant_remaining_today=max(0, spec.assistant_daily - used),
    )


# ---------- Endpoints ----------

@router.get("", response_model=SubscriptionOut)
def get_subscription(
    db: Session = Depends(get_db),
    account: PatientAccount = Depends(get_current_patient),
):
    """Права текущего пациента. Клиент зовёт при старте и после покупки."""
    return _subscription_out(db, account)


@router.get("/plans", response_model=PlansOut)
def get_plans(
    db: Session = Depends(get_db),
    account: PatientAccount = Depends(get_current_patient),
):
    """Каталог платных тарифов. Free не карточка, а базовое состояние — на
    экране показываем только то, что можно купить."""
    return PlansOut(
        current_tier=resolve_tier(account),
        plans=[
            PlanOut(
                tier=code,
                price_tjs=TIERS[code].price_tjs,
                price_usd=TIERS[code].price_usd,
                features=TIERS[code].features,
                assistant_daily_cap=TIERS[code].assistant_daily,
            )
            for code in (PLUS, PRO)
        ],
    )


@router.post("/dev", response_model=SubscriptionOut)
@limiter.limit("20/minute")
def dev_set_subscription(
    request: Request,
    payload: DevSetRequest,
    db: Session = Depends(get_db),
    account: PatientAccount = Depends(get_current_patient),
):
    """Выдать себе тариф без покупки — ТОЛЬКО для разработки и демо.

    Гейт — ``PATIENT_DEV_SUBSCRIPTION=1``; без него эндпоинта как будто нет
    (404, а не 403: не подсказываем существование лазейки). Когда появится
    IAP, право будет приходить из валидации чека, а этот вход останется
    выключенным в проде.
    """
    if os.getenv("PATIENT_DEV_SUBSCRIPTION") != "1":
        raise HTTPException(status_code=404, detail="Not Found")

    tier = payload.tier.strip().lower()
    if tier not in TIERS:
        raise HTTPException(status_code=422, detail="Неизвестный тариф")

    account.subscription_tier = tier
    if tier == FREE:
        account.subscription_expires_at = None
        account.subscription_source = None
    else:
        days = max(1, min(payload.days, 365))
        account.subscription_expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)
        )
        account.subscription_source = "manual"
    db.commit()
    db.refresh(account)
    return _subscription_out(db, account)
