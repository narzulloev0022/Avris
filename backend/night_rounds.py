from datetime import datetime
from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from audit import audit
from database import get_db
from models import NightRound, Patient, User
from auth import get_current_user

router = APIRouter(prefix="/api/night-rounds", tags=["night-rounds"])

# Витальные обхода → карта пациента: ICU-монитор читает last-value из
# Patient.vitals, так голосовой обход становится живым источником данных.
_VITALS_MAP = {"pulse": "ЧСС", "temp": "T°C", "spo2": "SpO₂"}  # bp — отдельно (строка "150/90")
_VITALS_CAP = 7  # столько точек держит спарклайн


def _merge_round_vitals(patient: Patient, nr_vitals) -> bool:
    """Дописать значения обхода в массивы Patient.vitals (cap последних 7)."""
    if not isinstance(nr_vitals, dict):
        return False
    cur = dict(patient.vitals) if isinstance(patient.vitals, dict) else {}
    changed = False

    def _push(key, val):
        nonlocal changed
        try:
            num = float(val)
        except (TypeError, ValueError):
            return
        arr = list(cur.get(key) or [])
        arr.append(int(num) if num == int(num) else num)
        cur[key] = arr[-_VITALS_CAP:]
        changed = True

    for src, dst in _VITALS_MAP.items():
        if nr_vitals.get(src) is not None:
            _push(dst, nr_vitals[src])
    bp = nr_vitals.get("bp")
    if bp is not None:
        _push("АД", str(bp).split("/")[0].strip())  # храним систолическое, как в остальных данных
    if changed:
        patient.vitals = cur  # reassign — SQLAlchemy не трекает мутации JSON
    return changed


class NightRoundCreate(BaseModel):
    patient_id: Optional[int] = None
    ward: Optional[str] = None
    vitals: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    plan: Optional[str] = None
    transcript: Optional[str] = None
    status: Optional[str] = None
    language: str = "ru"


class NightRoundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int
    patient_id: Optional[int] = None
    ward: Optional[str] = None
    vitals: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    plan: Optional[str] = None
    transcript: Optional[str] = None
    status: Optional[str] = None
    language: str
    created_at: datetime


@router.post("/", response_model=NightRoundResponse, status_code=status.HTTP_201_CREATED)
def create_round(
    payload: NightRoundCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    nr = NightRound(doctor_id=current_user.id, **payload.model_dump())
    db.add(nr)
    # Обход с витальными обновляет карту пациента (строго своего — чужой
    # patient_id молча игнорируем, обход при этом сохраняется как есть).
    if nr.patient_id and nr.vitals:
        p = db.query(Patient).filter(
            Patient.id == nr.patient_id,
            Patient.doctor_id == current_user.id,
        ).first()
        if p:
            _merge_round_vitals(p, nr.vitals)
    db.commit()
    db.refresh(nr)
    audit(db, action="create", entity="night_round", user_id=current_user.id,
          entity_id=nr.id, meta={"patient_id": nr.patient_id, "ward": nr.ward})
    return nr


@router.get("/", response_model=List[NightRoundResponse])
def list_rounds(
    response: Response,
    patient_id: Optional[int] = None,
    limit: Optional[int] = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(NightRound).filter(NightRound.doctor_id == current_user.id)
    if patient_id is not None:
        q = q.filter(NightRound.patient_id == patient_id)
    response.headers["X-Total-Count"] = str(q.count())
    q = q.order_by(NightRound.created_at.desc()).offset(offset)
    if limit is not None:
        q = q.limit(limit)
    return q.all()


@router.get("/{rid}", response_model=NightRoundResponse)
def get_round(
    rid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    r = db.query(NightRound).filter(NightRound.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Обход не найден")
    if r.doctor_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет доступа")
    return r
