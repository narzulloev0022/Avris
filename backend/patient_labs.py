"""Patient door — read-only access to the patient's own lab results.

Mirrors patient_visits.py exactly: access is strictly through PatientLink
ownership (a link only exists after consent), scoping to lab_orders attached
to the patient's linked doctor-scoped records. Read-only — a patient never
creates or mutates a lab order. Scan files are streamed from the DB BLOB,
same shape as the doctor's lab portal (lab_orders.py:download_file).

There is deliberately no patient prescription endpoint: the schema has no
Prescription entity — prescriptions live in a consultation's SOAP "P" field
and in patients.medications. Exposing them is a product decision, not a port.
"""
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import get_db
from models import LabFile, LabOrder, PatientAccount, User
from patient_auth import get_current_patient
from patient_visits import _linked_patient_ids

router = APIRouter(prefix="/api/patient/labs", tags=["patient"])


# ---------- schemas ----------

class LabFileMetaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    result_type: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime


class LabListItem(BaseModel):
    id: int
    date: datetime
    status: str
    tests: List[Any] = []
    doctor_name: Optional[str] = None
    file_count: int
    received_at: Optional[datetime] = None


class LabDetailOut(BaseModel):
    id: int
    date: datetime
    status: str
    tests: List[Any] = []
    results: Optional[Any] = None
    ai_comment: Optional[str] = None
    doctor_name: Optional[str] = None
    received_at: Optional[datetime] = None
    files: List[LabFileMetaOut] = []


# ---------- helpers ----------

def _owned_order(db: Session, oid: int, account: PatientAccount) -> LabOrder:
    patient_ids = _linked_patient_ids(db, account.id)
    order = db.query(LabOrder).filter(
        LabOrder.id == oid,
        LabOrder.patient_id.in_(patient_ids) if patient_ids else False,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Анализ не найден")
    return order


# ---------- endpoints ----------

@router.get("", response_model=List[LabListItem])
def list_labs(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    patient_ids = _linked_patient_ids(db, current.id)
    if not patient_ids:
        return []
    orders = (
        db.query(LabOrder)
        .filter(LabOrder.patient_id.in_(patient_ids))
        .order_by(LabOrder.created_at.desc())
        .all()
    )
    items = []
    for o in orders:
        doctor = db.query(User).filter(User.id == o.doctor_id).first()
        file_count = db.query(LabFile).filter(LabFile.lab_order_id == o.id).count()
        items.append(LabListItem(
            id=o.id, date=o.created_at, status=o.status, tests=o.tests or [],
            doctor_name=doctor.full_name if doctor else None,
            file_count=file_count, received_at=o.received_at,
        ))
    return items


@router.get("/{oid}", response_model=LabDetailOut)
def lab_detail(
    oid: int,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    o = _owned_order(db, oid, current)
    doctor = db.query(User).filter(User.id == o.doctor_id).first()
    files = (
        db.query(LabFile)
        .filter(LabFile.lab_order_id == o.id)
        .order_by(LabFile.uploaded_at.desc())
        .all()
    )
    return LabDetailOut(
        id=o.id, date=o.created_at, status=o.status, tests=o.tests or [],
        results=o.results, ai_comment=o.ai_comment,
        doctor_name=doctor.full_name if doctor else None,
        received_at=o.received_at,
        files=[LabFileMetaOut.model_validate(f) for f in files],
    )


@router.get("/{oid}/files/{fid}")
def download_lab_file(
    oid: int,
    fid: int,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    _owned_order(db, oid, current)  # ownership gate
    rec = db.query(LabFile).filter(LabFile.id == fid, LabFile.lab_order_id == oid).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Файл не найден")
    safe_name = rec.filename.replace('"', "")
    return Response(
        content=rec.data,
        media_type=rec.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )
