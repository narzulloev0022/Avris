import json
import logging
from datetime import datetime
from io import BytesIO
from typing import List, Optional, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import get_db
from models import LabOrder, User, Patient
from auth import get_current_user
from llm import _claude_call, ANTHROPIC_API_KEY
from pdf_export import render_lab_order_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lab-orders", tags=["lab-orders"])


# ---------- Schemas ----------

class LabOrderCreate(BaseModel):
    patient_id: Optional[int] = None
    tests: List[str] = []


class LabOrderResultsRequest(BaseModel):
    results: dict[str, Any]
    patient_context: Optional[dict[str, Any]] = None


class LabOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: Optional[int] = None
    doctor_id: int
    qr_token: str
    tests: List[str]
    status: str
    results: Optional[dict[str, Any]] = None
    ai_comment: Optional[str] = None
    created_at: datetime
    received_at: Optional[datetime] = None


class LabOrderPublic(BaseModel):
    """Slim view for the lab portal — no auth needed."""
    id: int
    qr_token: str
    tests: List[str]
    status: str
    results: Optional[dict[str, Any]] = None
    created_at: datetime
    received_at: Optional[datetime] = None
    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_ward: Optional[str] = None
    doctor_name: Optional[str] = None


# ---------- Helpers ----------

def _owned_order(db: Session, oid: int, user: User) -> LabOrder:
    o = db.query(LabOrder).filter(LabOrder.id == oid).first()
    if not o:
        raise HTTPException(status_code=404, detail="Направление не найдено")
    if o.doctor_id != user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому направлению")
    return o


def _build_public(db: Session, o: LabOrder) -> LabOrderPublic:
    pat_name = pat_age = pat_ward = None
    if o.patient_id:
        p = db.query(Patient).filter(Patient.id == o.patient_id).first()
        if p:
            pat_name = p.full_name
            pat_age = p.age
            pat_ward = p.ward
    doc = db.query(User).filter(User.id == o.doctor_id).first()
    doc_name = doc.full_name if doc else None
    return LabOrderPublic(
        id=o.id,
        qr_token=o.qr_token,
        tests=list(o.tests or []),
        status=o.status,
        results=o.results,
        created_at=o.created_at,
        received_at=o.received_at,
        patient_name=pat_name,
        patient_age=pat_age,
        patient_ward=pat_ward,
        doctor_name=doc_name,
    )


async def _generate_ai_comment(results: dict, patient_context: Optional[dict]) -> str:
    if not ANTHROPIC_API_KEY:
        n = len(results) if isinstance(results, dict) else 0
        return (
            f"Получены результаты по {n} показателям. "
            "Подключите ANTHROPIC_API_KEY для автоматической клинической интерпретации."
        )
    try:
        system_prompt = (
            "Ты медицинский AI-ассистент. Дай краткий клинический комментарий "
            "к лабораторным результатам пациента: отметь отклонения от нормы и возможные интерпретации. "
            "Не делай окончательных диагнозов — формулируй как наблюдения и рекомендации. "
            "Ответ на русском, 2–4 предложения."
        )
        user_msg = "Результаты анализов: " + json.dumps(results, ensure_ascii=False)
        if patient_context:
            user_msg += "\n\nКонтекст пациента: " + json.dumps(patient_context, ensure_ascii=False)
        return await _claude_call(system_prompt, user_msg, max_tokens=400)
    except Exception as e:
        logger.warning("AI comment generation failed: %s", e)
        n = len(results) if isinstance(results, dict) else 0
        return f"Получены результаты по {n} показателям. AI-анализ временно недоступен."


# ---------- Endpoints (auth-gated) ----------

@router.post("/", response_model=LabOrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: LabOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    o = LabOrder(
        doctor_id=current_user.id,
        patient_id=payload.patient_id,
        tests=list(payload.tests or []),
        qr_token=str(uuid4()),
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


@router.get("/", response_model=List[LabOrderResponse])
def list_orders(
    patient_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(LabOrder).filter(LabOrder.doctor_id == current_user.id)
    if patient_id is not None:
        q = q.filter(LabOrder.patient_id == patient_id)
    return q.order_by(LabOrder.created_at.desc()).all()


@router.get("/{oid}", response_model=LabOrderResponse)
def get_order(
    oid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _owned_order(db, oid, current_user)


@router.get("/{oid}/pdf")
def lab_order_pdf(
    oid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    o = _owned_order(db, oid, current_user)
    patient = None
    if o.patient_id:
        patient = db.query(Patient).filter(Patient.id == o.patient_id).first()
    pdf_bytes = render_lab_order_pdf(o, patient, current_user)
    fname = f"avris-lab-order-{oid}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------- Public endpoints (lab portal — no auth) ----------

@router.get("/by-token/{qr_token}", response_model=LabOrderPublic)
def get_by_token(qr_token: str, db: Session = Depends(get_db)):
    o = db.query(LabOrder).filter(LabOrder.qr_token == qr_token).first()
    if not o:
        raise HTTPException(status_code=404, detail="Направление не найдено")
    return _build_public(db, o)


@router.put("/{oid}/results", response_model=LabOrderResponse)
async def upload_results(
    oid: int,
    payload: LabOrderResultsRequest,
    db: Session = Depends(get_db),
):
    o = db.query(LabOrder).filter(LabOrder.id == oid).first()
    if not o:
        raise HTTPException(status_code=404, detail="Направление не найдено")
    o.results = payload.results
    o.status = "received"
    o.received_at = datetime.utcnow()
    o.ai_comment = await _generate_ai_comment(payload.results, payload.patient_context)
    db.commit()
    db.refresh(o)
    return o
