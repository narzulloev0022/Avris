"""Ежедневная отметка самочувствия.

Единственное на главном экране, что пациент делает сам и по своей воле:
всё остальное там либо чтение, либо реакция на событие. Между приёмами врач
о человеке не знает ничего, а «третий день слабость» — это первое, что он
спрашивает.

Шкала субъективная и принадлежит человеку. Поэтому сервер не делает из неё
никаких выводов: ни находок мониторинга, ни средних «баллов самочувствия».
Он только хранит и отдаёт — врачу и самому пациенту.
"""
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import PatientAccount, PatientCheckin, PatientLink
from patient_auth import get_current_patient
from rate_limit import limiter

router = APIRouter(prefix="/api/patient/checkins", tags=["patient"])

MIN_LEVEL, MAX_LEVEL = 1, 5

# Словами — теми же, что видит пациент на главном экране. Врач должен читать
# ту же шкалу, что человек выбирал пальцем, а не наш пересчёт в проценты.
LEVEL_RU = {1: "плохо", 2: "так себе", 3: "нормально", 4: "хорошо", 5: "отлично"}


class CheckinIn(BaseModel):
    level: int = Field(..., ge=MIN_LEVEL, le=MAX_LEVEL)
    note: Optional[str] = Field(None, max_length=280)
    # Отметить можно и вчерашний день — человек забыл, а не выпал из жизни.
    day: Optional[date] = None


class CheckinOut(BaseModel):
    day: date
    level: int
    note: Optional[str] = None


@router.get("", response_model=List[CheckinOut])
def history(
    days: int = 30,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    since = date.today() - timedelta(days=max(1, min(days, 365)))
    rows = (db.query(PatientCheckin)
            .filter(PatientCheckin.patient_account_id == current.id,
                    PatientCheckin.day >= since)
            .order_by(PatientCheckin.day.desc())
            .all())
    return [CheckinOut(day=r.day, level=r.level, note=r.note) for r in rows]


@router.put("", response_model=CheckinOut)
@limiter.limit("30/minute")
def upsert(
    request: Request,
    body: CheckinIn,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Отметить самочувствие. Повторная отметка меняет запись за тот день —
    дневник, который можно вести десять раз в сутки, перестают вести."""
    today = date.today()
    day = body.day or today
    if day > today:
        raise HTTPException(status_code=400, detail="Нельзя отметить будущий день")
    if day < today - timedelta(days=7):
        # Через неделю человек уже не помнит, как себя чувствовал; запись
        # задним числом на месяц — это выдумка, а не дневник.
        raise HTTPException(status_code=400, detail="Отметить можно только последнюю неделю")

    row = (db.query(PatientCheckin)
           .filter(PatientCheckin.patient_account_id == current.id,
                   PatientCheckin.day == day)
           .first())
    note = (body.note or "").strip() or None
    if row is None:
        row = PatientCheckin(patient_account_id=current.id, day=day,
                             level=body.level, note=note)
        db.add(row)
    else:
        row.level = body.level
        row.note = note
        row.updated_at = datetime.utcnow()
    db.commit()
    return CheckinOut(day=row.day, level=row.level, note=row.note)


# ---------- врачебная сторона ----------
#
# Карточка на главном экране обещает пациенту: «врач увидит это на приёме».
# Всё, что ниже, — механизм этого обещания. Читает врачебная половина
# (patients.py), поэтому знание об устройстве отметок остаётся здесь.

def linked_account_id(db: Session, patient_id: int, doctor_id: int) -> Optional[int]:
    """Аккаунт пациента, если он подключил приложение и связь жива.

    Отзыв согласия гасит связь (``revoked_at``), а не удаляет её — и врач
    вместе с ней теряет доступ к отметкам. Согласие здесь и есть право
    читать: отдельного гейта не нужно, но фильтр по revoked_at обязателен.
    """
    row = (db.query(PatientLink.patient_account_id)
           .filter(PatientLink.patient_id == patient_id,
                   PatientLink.doctor_id == doctor_id,
                   PatientLink.revoked_at.is_(None))
           .first())
    return row[0] if row else None


def recent_for_account(db: Session, account_id: int, days: int = 30) -> List[PatientCheckin]:
    since = date.today() - timedelta(days=max(1, min(days, 365)))
    return (db.query(PatientCheckin)
            .filter(PatientCheckin.patient_account_id == account_id,
                    PatientCheckin.day >= since)
            .order_by(PatientCheckin.day.desc())
            .all())


def history_block(rows: List[PatientCheckin]) -> str:
    """Блок отметок для сводки к приёму — как есть, без выводов.

    Ни среднего, ни динамики, ни «балла самочувствия»: шкала субъективная и
    принадлежит человеку, посчитанное по ней среднее выглядит как измерение,
    которым не является. Врачу нужен ряд дней и слова пациента — вывод он
    сделает сам.
    """
    if not rows:
        return ""
    lines = []
    for r in rows:
        line = f"{r.day.strftime('%d.%m')}: {LEVEL_RU.get(r.level, r.level)}"
        if r.note:
            line += f" — «{r.note}»"
        lines.append(line)
    return ("== САМООЦЕНКА САМОЧУВСТВИЯ (со слов пациента, между приёмами) ==\n"
            + "\n".join(lines))
