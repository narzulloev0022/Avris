"""Сводка раздела «Здоровье» — одна строка для карточки на главной.

Существует ради одного: карточка на главной должна нести смысл, а не быть
подписанным прямоугольником. «Здоровье» с цифрами дня отвечает на вопрос
«что со мной сейчас», ярлык «Здоровье ›» — ни на что.

Один запрос вместо трёх: главная не должна дёргать питание, сахар и
мониторинг по отдельности только чтобы показать три числа.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import (GlucoseReading, HealthAlert, MonitoringDigest, NutritionEntry,
                    PatientAccount)
from patient_auth import get_current_patient
from patient_glucose import classify, summarize
from patient_subscription import (FEATURE_DIABETES, FEATURE_MONITORING,
                                  FEATURE_NUTRITION, has_feature)

router = APIRouter(prefix="/api/patient/health", tags=["patient"])

# Насколько давним может быть измерение, чтобы его ещё имело смысл показывать
# как «сейчас». Позавчерашний сахар на главной — не состояние, а архив.
GLUCOSE_FRESH_HOURS = 36

# За какой срок считаем «время в цели». Две недели — тот же период, что и на
# экране диабет-контроля: две разные цифры под одним названием хуже, чем
# отсутствие цифры.
IN_RANGE_DAYS = 14


class TopAlertOut(BaseModel):
    """Самая важная активная находка — та, что поедет на главную.

    Счётчик «2 находки» на главной ничего не сообщает: чтобы узнать, что
    именно заметили, надо провалиться на два экрана вглубь. Поэтому сводка
    несёт саму находку — текст соберёт клиент, как и на экране мониторинга.
    """
    id: int
    kind: str
    severity: str
    details: Dict[str, Any] = {}
    created_at: datetime


class HealthSummaryOut(BaseModel):
    """Каждое поле необязательно: чего пациент не ведёт, того на главной и нет.

    Ноль калорий и «нет измерений» — разные вещи, поэтому ``kcal_today``
    остаётся None, пока за день не появилось ни одной записи.
    """
    kcal_today: Optional[int] = None
    glucose_mmol: Optional[float] = None
    glucose_mark: Optional[str] = None
    # Доля измерений в целевом диапазоне за две недели. Международно принятая
    # метрика диабета со своей опубликованной целью — в отличие от «балла
    # здоровья», её есть чем подкрепить. None — измерений слишком мало.
    in_range_percent: Optional[int] = None
    active_alerts: int = 0
    top_alert: Optional[TopAlertOut] = None
    # Одна спокойная фраза обо всех находках сразу, написанная моделью.
    # None — фразы пока нет, клиент показывает текст правила.
    alert_digest: Optional[str] = None
    has_nutrition: bool = False
    has_diabetes: bool = False
    has_monitoring: bool = False


@router.get("/summary", response_model=HealthSummaryOut)
def health_summary(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Три числа для карточки «Здоровье». Тариф не гейтит запрос целиком:
    права проверяются по каждой части отдельно, иначе один общий 402 скрыл бы
    и то, что пациенту доступно."""
    out = HealthSummaryOut(
        has_nutrition=has_feature(current, FEATURE_NUTRITION),
        has_diabetes=has_feature(current, FEATURE_DIABETES),
        has_monitoring=has_feature(current, FEATURE_MONITORING),
    )
    now = datetime.utcnow()

    if out.has_nutrition:
        start = datetime(now.year, now.month, now.day)
        total = (db.query(func.sum(NutritionEntry.kcal))
                 .filter(NutritionEntry.patient_account_id == current.id,
                         NutritionEntry.eaten_at >= start)
                 .scalar())
        if total:
            out.kcal_today = int(total)

    if out.has_diabetes:
        last = (db.query(GlucoseReading)
                .filter(GlucoseReading.patient_account_id == current.id,
                        GlucoseReading.taken_at >= now - timedelta(hours=GLUCOSE_FRESH_HOURS))
                .order_by(GlucoseReading.taken_at.desc())
                .first())
        if last:
            out.glucose_mmol = last.mmol
            out.glucose_mark = classify(last.mmol, last.context)
        period = (db.query(GlucoseReading)
                  .filter(GlucoseReading.patient_account_id == current.id,
                          GlucoseReading.taken_at >= now - timedelta(days=IN_RANGE_DAYS))
                  .order_by(GlucoseReading.taken_at.desc())
                  .all())
        out.in_range_percent = summarize(
            [{"mmol": r.mmol, "context": r.context, "taken_at": r.taken_at} for r in period]
        )["in_range_percent"]

    if out.has_monitoring:
        active = (db.query(HealthAlert)
                  .filter(HealthAlert.patient_account_id == current.id,
                          HealthAlert.acknowledged_at.is_(None))
                  .all())
        out.active_alerts = len(active)
        if active:
            # Сначала по срочности, потом по свежести: две находки на главной
            # не поместятся, и показать надо ту, ради которой стоит идти к
            # врачу, а не ту, что просто пришла последней.
            rank = {"urgent": 0, "attention": 1, "info": 2}
            top = sorted(active, key=lambda a: (rank.get(a.severity, 3), -a.created_at.timestamp()))[0]
            out.top_alert = TopAlertOut(
                id=top.id, kind=top.kind, severity=top.severity,
                details=dict(top.details or {}), created_at=top.created_at)
            # Только чтение кэша: главная не должна ждать модель.
            digest = (db.query(MonitoringDigest)
                      .filter(MonitoringDigest.patient_id == current.id).first())
            if digest is not None:
                out.alert_digest = digest.text
    return out
