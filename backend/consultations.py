from datetime import datetime
from io import BytesIO
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import get_db
from models import Consultation, Patient, User
from auth import get_current_user
from pdf_export import render_consultation_pdf

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
    # Accuracy tracking. Set by the frontend at save time:
    #   None  → SOAP wasn't AI-generated (manual entry) — don't count
    #   False → AI-generated, doctor saved without edits → accurate
    #   True  → AI-generated, doctor edited at least one field → edited
    soap_was_edited: Optional[bool] = None


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
    data = payload.model_dump()
    was_edited = data.pop("soap_was_edited", None)
    c = Consultation(doctor_id=current_user.id, **data)
    db.add(c)
    # Bump accuracy counters only when the frontend explicitly tagged this save.
    # None means the SOAP was hand-typed without Claude — irrelevant to accuracy.
    if was_edited is True:
        current_user.soap_edited_count = (current_user.soap_edited_count or 0) + 1
    elif was_edited is False:
        current_user.soap_accurate_count = (current_user.soap_accurate_count or 0) + 1
    db.commit()
    db.refresh(c)
    return c


@router.get("/", response_model=List[ConsultationResponse])
def list_consultations(
    patient_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Consultation).filter(Consultation.doctor_id == current_user.id)
    if patient_id is not None:
        q = q.filter(Consultation.patient_id == patient_id)
    return q.order_by(Consultation.created_at.desc()).all()


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


@router.get("/{cid}/pdf")
def consultation_pdf(
    cid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Consultation).filter(Consultation.id == cid).first()
    if not c:
        raise HTTPException(status_code=404, detail="Консультация не найдена")
    if c.doctor_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой консультации")
    patient = None
    if c.patient_id:
        patient = db.query(Patient).filter(Patient.id == c.patient_id).first()
    pdf_bytes = render_consultation_pdf(c, patient, current_user)
    fname = f"avris-consultation-{cid}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
