"""Дневные цели и вода.

Цель ставит человек. Это не формальность: как только знаменатель придумывает
приложение, «осталось 300 ккал» становится требованием от программы, которая
о человеке ничего не знает. Своя цель — наоборот, то единственное, что делает
счётчик осмысленным: без неё «8412 шагов» это ни много ни мало.

Вода живёт здесь же, потому что это тот же механизм «сколько из скольких», и
потому что заводить ради счётчика стаканов отдельный модуль незачем.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import PatientAccount, PatientGoals, WaterIntake
from patient_auth import get_current_patient
from rate_limit import limiter

router = APIRouter(prefix="/api/patient", tags=["patient"])

# Границы правдоподобия: защита от промаха по клавиатуре, а не рекомендация.
LIMITS = {
    "steps": (500, 60000),
    "water_glasses": (1, 30),
    "kcal": (600, 8000),
}

# Сколько стаканов можно добавить за раз. Тап по стакану — одно действие.
MAX_GLASS_STEP = 5


class GoalsOut(BaseModel):
    steps: Optional[int] = None
    water_glasses: Optional[int] = None
    kcal: Optional[int] = None


class GoalsIn(BaseModel):
    """Каждое поле необязательно; null сбрасывает цель, и остаток пропадает."""
    steps: Optional[int] = None
    water_glasses: Optional[int] = None
    kcal: Optional[int] = None


class WaterOut(BaseModel):
    day: date
    glasses: int
    goal: Optional[int] = None


def _check(field: str, value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    low, high = LIMITS[field]
    if not (low <= value <= high):
        raise HTTPException(status_code=400, detail=f"Значение вне допустимого диапазона: {field}")
    return value


def _goals_row(db: Session, account_id: int) -> Optional[PatientGoals]:
    return db.query(PatientGoals).filter(
        PatientGoals.patient_account_id == account_id).first()


@router.get("/goals", response_model=GoalsOut)
def get_goals(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    row = _goals_row(db, current.id)
    if row is None:
        return GoalsOut()
    return GoalsOut(steps=row.steps, water_glasses=row.water_glasses, kcal=row.kcal)


@router.put("/goals", response_model=GoalsOut)
@limiter.limit("30/minute")
def set_goals(
    request: Request,
    body: GoalsIn,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Поставить или сбросить цели. Присланные поля перезаписываются целиком —
    в том числе в null: «убрать цель» такое же нормальное действие, как её
    поставить."""
    sent = body.model_dump(exclude_unset=True)
    row = _goals_row(db, current.id)
    if row is None:
        row = PatientGoals(patient_account_id=current.id)
        db.add(row)
    for field, value in sent.items():
        setattr(row, field, _check(field, value))
    row.updated_at = datetime.utcnow()
    db.commit()
    return GoalsOut(steps=row.steps, water_glasses=row.water_glasses, kcal=row.kcal)


@router.get("/water", response_model=WaterOut)
def get_water(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    today = date.today()
    row = (db.query(WaterIntake)
           .filter(WaterIntake.patient_account_id == current.id,
                   WaterIntake.day == today)
           .first())
    goals = _goals_row(db, current.id)
    return WaterOut(day=today, glasses=row.glasses if row else 0,
                    goal=goals.water_glasses if goals else None)


class WaterIn(BaseModel):
    # Отрицательное значение убирает стакан: налил лишний — исправил тем же
    # жестом, а не через настройки.
    delta: int = Field(..., ge=-MAX_GLASS_STEP, le=MAX_GLASS_STEP)


@router.post("/water", response_model=WaterOut)
@limiter.limit("60/minute")
def add_water(
    request: Request,
    body: WaterIn,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    today = date.today()
    row = (db.query(WaterIntake)
           .filter(WaterIntake.patient_account_id == current.id,
                   WaterIntake.day == today)
           .first())
    if row is None:
        row = WaterIntake(patient_account_id=current.id, day=today, glasses=0)
        db.add(row)
    # Ниже нуля не уходим: отрицательное число стаканов ничего не значит.
    row.glasses = max(0, min(LIMITS["water_glasses"][1], row.glasses + body.delta))
    row.updated_at = datetime.utcnow()
    db.commit()
    goals = _goals_row(db, current.id)
    return WaterOut(day=today, glasses=row.glasses,
                    goal=goals.water_glasses if goals else None)
