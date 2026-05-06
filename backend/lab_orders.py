import json
import logging
from datetime import datetime
from io import BytesIO
from typing import List, Optional, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import get_db
from models import LabOrder, LabFile, User, Patient
from auth import get_current_user
from llm import _claude_call, ANTHROPIC_API_KEY
from pdf_export import render_lab_order_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lab-orders", tags=["lab-orders"])


# ---------- Schemas ----------

class LabOrderCreate(BaseModel):
    patient_id: Optional[int] = None
    tests: List[str] = []


class LabOrderTestsUpdate(BaseModel):
    tests: List[str]


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


@router.put("/{oid}/tests", response_model=LabOrderResponse)
def update_order_tests(
    oid: int,
    payload: LabOrderTestsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replace the tests list on a pending order. Used when the doctor finalizes
    the test selection in the Lab Connect modal — the order is auto-created on
    open with the default checkboxes, then synced with the full selection on
    Print so the lab portal sees every requested test."""
    o = _owned_order(db, oid, current_user)
    if o.status != "pending":
        raise HTTPException(status_code=409, detail="Направление уже обработано — изменение списка анализов невозможно")
    o.tests = list(payload.tests or [])
    db.commit()
    db.refresh(o)
    return o


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


# ---------- File upload (lab portal) + read (doctor) ----------

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_RESULT_TYPES = {"lab", "ecg", "xray", "us", "mri", "ct", "endo", "other"}
ALLOWED_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".dcm"}
ALLOWED_MIMES = {
    "application/pdf",
    "image/jpeg", "image/jpg", "image/png",
    "application/dicom", "application/octet-stream",
}


class LabFileMeta(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    content_type: str
    result_type: str
    size_bytes: int
    uploaded_at: datetime


def _ext_ok(name: str) -> bool:
    name = (name or "").lower()
    return any(name.endswith(ext) for ext in ALLOWED_EXTS)


@router.post("/by-token/{qr_token}/files", response_model=LabFileMeta)
async def upload_file_by_token(
    qr_token: str,
    result_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Lab tech uploads a result file. Auth = knowledge of the QR token."""
    if result_type not in ALLOWED_RESULT_TYPES:
        raise HTTPException(400, f"Invalid result_type. Allowed: {sorted(ALLOWED_RESULT_TYPES)}")
    o = db.query(LabOrder).filter(LabOrder.qr_token == qr_token).first()
    if not o:
        raise HTTPException(404, "Направление не найдено")

    if not _ext_ok(file.filename or ""):
        raise HTTPException(415, f"Unsupported file extension. Allowed: {sorted(ALLOWED_EXTS)}")
    if file.content_type and file.content_type not in ALLOWED_MIMES and not file.content_type.startswith("image/"):
        raise HTTPException(415, f"Unsupported content type: {file.content_type}")

    body = await file.read()
    if len(body) == 0:
        raise HTTPException(400, "Empty file")
    if len(body) > MAX_FILE_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_FILE_BYTES // (1024*1024)} MB)")

    rec = LabFile(
        lab_order_id=o.id,
        filename=file.filename or "upload.bin",
        content_type=file.content_type or "application/octet-stream",
        result_type=result_type,
        size_bytes=len(body),
        data=body,
    )
    db.add(rec)
    # Treat any uploaded file as evidence the order has been processed.
    if o.status == "pending":
        o.status = "received"
        o.received_at = datetime.utcnow()
    db.commit()
    db.refresh(rec)
    return rec


@router.get("/{oid}/files", response_model=List[LabFileMeta])
def list_files(
    oid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_order(db, oid, current_user)
    rows = (
        db.query(LabFile)
        .filter(LabFile.lab_order_id == oid)
        .order_by(LabFile.uploaded_at.desc())
        .all()
    )
    return rows


@router.get("/by-token/{qr_token}/files", response_model=List[LabFileMeta])
def list_files_by_token(qr_token: str, db: Session = Depends(get_db)):
    """Lab tech sees what was already uploaded for this order."""
    o = db.query(LabOrder).filter(LabOrder.qr_token == qr_token).first()
    if not o:
        raise HTTPException(404, "Направление не найдено")
    rows = (
        db.query(LabFile)
        .filter(LabFile.lab_order_id == o.id)
        .order_by(LabFile.uploaded_at.desc())
        .all()
    )
    return rows


@router.get("/{oid}/files/{fid}")
def download_file(
    oid: int,
    fid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_order(db, oid, current_user)
    rec = db.query(LabFile).filter(LabFile.id == fid, LabFile.lab_order_id == oid).first()
    if not rec:
        raise HTTPException(404, "Файл не найден")
    safe_name = rec.filename.replace('"', "")
    return Response(
        content=rec.data,
        media_type=rec.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@router.delete("/{oid}/files/{fid}", status_code=204)
def delete_file(
    oid: int,
    fid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_order(db, oid, current_user)
    rec = db.query(LabFile).filter(LabFile.id == fid, LabFile.lab_order_id == oid).first()
    if not rec:
        raise HTTPException(404, "Файл не найден")
    db.delete(rec)
    db.commit()
    return Response(status_code=204)
