from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import get_db
from models import Consultation, User
from auth import get_current_user

router = APIRouter(prefix="/api/consultations", tags=["consultations"])


class ConsultationCreate(BaseModel):
    patient_id: Optional[int] = None
    transcript: Optional[str] = None
    soap_s: Optional[str] = None
    soap_o: Optional[str] = None
    soap_a: Optional[str] = None
    soap_p: Optional[str] = None
    language: str = "ru"
    duration_seconds: Optional[int] = None


class ConsultationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: Optional[int] = None
    doctor_id: int
    transcript: Optional[str] = None
    soap_s: Optional[str] = None
    soap_o: Optional[str] = None
    soap_a: Optional[str] = None
    soap_p: Optional[str] = None
    language: str
    duration_seconds: Optional[int] = None
    created_at: datetime


@router.post("/", response_model=ConsultationResponse, status_code=status.HTTP_201_CREATED)
def create_consultation(
    payload: ConsultationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = Consultation(doctor_id=current_user.id, **payload.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.get("/", response_model=List[ConsultationResponse])
def list_consultations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Consultation)
        .filter(Consultation.doctor_id == current_user.id)
        .order_by(Consultation.created_at.desc())
        .all()
    )


@router.get("/{cid}", response_model=ConsultationResponse)
def get_consultation(
    cid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Consultation).filter(Consultation.id == cid).first()
    if not c:
        raise HTTPException(status_code=404, detail="Консультация не найдена")
    if c.doctor_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой консультации")
    return c
