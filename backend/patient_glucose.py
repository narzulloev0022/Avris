"""Диабет-контроль — дневник сахара крови (тариф Pro).

Что это: журнал измерений с пометкой обстоятельств (натощак, до/после еды,
на ночь), сводка за период и честные флаги. Что это НЕ: подбор дозы,
рекомендация препарата и вообще любое лечение. Дозу инсулина назначает
врач, и приложение не должно даже намекать на её изменение — ошибка здесь
стоит человеку сознания, а не неудобства.

Целевые диапазоны ниже — общие ориентиры, а не персональная цель: врач
ставит её индивидуально (беременность, возраст, стаж диабета меняют всё).
В приложении это сказано прямо, а не мелким шрифтом.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import GlucoseReading, PatientAccount
from patient_auth import get_current_patient
from patient_subscription import FEATURE_DIABETES, require_feature
from rate_limit import limiter

router = APIRouter(prefix="/api/patient/glucose", tags=["patient"])

CONTEXTS = ("fasting", "before_meal", "after_meal", "bedtime", "random")

# Физиологически возможные границы. Всё за ними — опечатка (ввели мг/дл или
# промахнулись по клавише), и попасть в статистику как факт это не должно.
MIN_MMOL = 1.0
MAX_MMOL = 35.0

# Гипогликемия — единственный порог, который здесь абсолютен: ниже 3.9 нужно
# действовать сейчас, а не обсуждать на следующем приёме.
LOW_MMOL = 3.9
VERY_LOW_MMOL = 3.0

# Общие ориентиры цели по обстоятельствам измерения (ммоль/л).
TARGETS = {
    "fasting": (4.0, 7.0),
    "before_meal": (4.0, 7.0),
    "after_meal": (4.0, 10.0),
    "bedtime": (5.0, 8.5),
    "random": (4.0, 10.0),
}

# Сколько подряд высоких измерений считаем поводом сказать врачу. Одно
# высокое — обычный день; три подряд — устойчивая картина.
HIGH_STREAK = 3

# Меньше этого числа измерений — статистика есть, а выводов нет. Честнее
# сказать «мало данных», чем показать средний сахар по двум точкам.
MIN_FOR_SUMMARY = 3


def classify(mmol: float, context: str) -> str:
    """low | in_range | high — относительно общего ориентира для обстоятельств."""
    if mmol < LOW_MMOL:
        return "low"
    low, high = TARGETS.get(context, TARGETS["random"])
    if mmol > high:
        return "high"
    if mmol < low:
        return "low"
    return "in_range"


def summarize(readings: List[dict]) -> dict:
    """Сводка по списку измерений (новые в начале).

    Чистая функция: на вход — [{"mmol": float, "context": str, "taken_at": dt}].
    Здесь легко соврать незаметно, поэтому логика вынесена и покрыта тестами.
    """
    if not readings:
        return {"count": 0, "average": None, "min": None, "max": None,
                "in_range_percent": None, "lows": 0, "highs": 0,
                "enough_data": False, "flags": []}

    values = [r["mmol"] for r in readings]
    marks = [classify(r["mmol"], r.get("context") or "random") for r in readings]
    lows = marks.count("low")
    highs = marks.count("high")
    in_range = marks.count("in_range")
    enough = len(readings) >= MIN_FOR_SUMMARY

    flags = []
    if any(v < VERY_LOW_MMOL for v in values):
        flags.append("very_low")
    elif lows:
        flags.append("low")
    # Подряд идущие высокие — считаем от новых к старым, ровно как пришли.
    streak = 0
    for mark in marks:
        if mark == "high":
            streak += 1
            if streak >= HIGH_STREAK:
                flags.append("high_streak")
                break
        else:
            break
    if not enough:
        flags.append("not_enough_data")

    return {
        "count": len(readings),
        "average": round(sum(values) / len(values), 1),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
        # Процент «в цели» считаем только когда измерений достаточно: по двум
        # точкам он выглядит как знание, а является совпадением.
        "in_range_percent": round(in_range * 100 / len(readings)) if enough else None,
        "lows": lows,
        "highs": highs,
        "enough_data": enough,
        "flags": flags,
    }


# ---------- Schemas ----------

class ReadingIn(BaseModel):
    mmol: float = Field(ge=MIN_MMOL, le=MAX_MMOL)
    context: str = "random"
    taken_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=300)


class ReadingOut(BaseModel):
    id: int
    taken_at: datetime
    mmol: float
    context: str
    mark: str
    note: Optional[str] = None


class GlucoseOut(BaseModel):
    days: int
    readings: List[ReadingOut]
    count: int
    average: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    in_range_percent: Optional[int] = None
    lows: int = 0
    highs: int = 0
    enough_data: bool = False
    flags: List[str] = []


# ---------- Endpoints ----------

def _owned(db: Session, rid: int, account: PatientAccount) -> GlucoseReading:
    row = db.query(GlucoseReading).filter(
        GlucoseReading.id == rid,
        GlucoseReading.patient_account_id == account.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Измерение не найдено")
    return row


@router.get("", response_model=GlucoseOut)
def get_readings(
    days: int = 14,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Измерения за период и сводка по ним."""
    require_feature(current, FEATURE_DIABETES)
    days = max(1, min(days, 90))
    since = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(GlucoseReading)
            .filter(GlucoseReading.patient_account_id == current.id,
                    GlucoseReading.taken_at >= since)
            .order_by(GlucoseReading.taken_at.desc())
            .all())
    stats = summarize([{"mmol": r.mmol, "context": r.context, "taken_at": r.taken_at}
                       for r in rows])
    return GlucoseOut(
        days=days,
        readings=[ReadingOut(id=r.id, taken_at=r.taken_at, mmol=r.mmol, context=r.context,
                             mark=classify(r.mmol, r.context), note=r.note)
                  for r in rows],
        **stats,
    )


@router.post("", response_model=ReadingOut, status_code=201)
@limiter.limit("60/minute")
def add_reading(
    request: Request,
    payload: ReadingIn,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    require_feature(current, FEATURE_DIABETES)
    context = payload.context if payload.context in CONTEXTS else "random"
    row = GlucoseReading(
        patient_account_id=current.id,
        taken_at=payload.taken_at or datetime.utcnow(),
        mmol=round(payload.mmol, 1),
        context=context,
        note=(payload.note or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ReadingOut(id=row.id, taken_at=row.taken_at, mmol=row.mmol, context=row.context,
                      mark=classify(row.mmol, row.context), note=row.note)


@router.delete("/{rid}", status_code=204)
def delete_reading(
    rid: int,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    require_feature(current, FEATURE_DIABETES)
    db.delete(_owned(db, rid, current))
    db.commit()
