from datetime import datetime
from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import get_db
from models import NightRound, User
from auth import get_current_user

router = APIRouter(prefix="/api/night-rounds", tags=["night-rounds"])


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
    db.commit()
    db.refresh(nr)
    return nr


@router.get("/", response_model=List[NightRoundResponse])
def list_rounds(
    patient_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(NightRound).filter(NightRound.doctor_id == current_user.id)
    if patient_id is not None:
        q = q.filter(NightRound.patient_id == patient_id)
    return q.order_by(NightRound.created_at.desc()).all()


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
