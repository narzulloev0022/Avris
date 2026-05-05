import logging
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import Notification, Patient, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/notifications", tags=["notifications"])


# ---------- Schemas ----------

class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    type: str
    title: str
    message: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    is_read: bool
    created_at: datetime


class CallDoctorRequest(BaseModel):
    patient_id: int
    reason: Optional[str] = None  # short reason code: "deterioration" | "respiratory" | "arrhythmia" | "bleeding" | "other"
    note: Optional[str] = None    # free-text additional context


class CallDoctorResponse(BaseModel):
    status: str
    notified_doctor: dict[str, Any]
    notification_id: int


# ---------- List / mark read ----------

@router.get("/", response_model=List[NotificationOut])
def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Notification).filter(Notification.doctor_id == current_user.id)
    if unread_only:
        q = q.filter(Notification.is_read.is_(False))
    return q.order_by(Notification.created_at.desc()).limit(min(limit, 200)).all()


@router.get("/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    n = (
        db.query(Notification)
        .filter(Notification.doctor_id == current_user.id, Notification.is_read.is_(False))
        .count()
    )
    return {"unread": n}


@router.put("/{nid}/read", response_model=NotificationOut)
def mark_read(
    nid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    n = db.query(Notification).filter(
        Notification.id == nid, Notification.doctor_id == current_user.id
    ).first()
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    n.is_read = True
    db.commit()
    db.refresh(n)
    return n


@router.put("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    upd = (
        db.query(Notification)
        .filter(Notification.doctor_id == current_user.id, Notification.is_read.is_(False))
        .update({Notification.is_read: True}, synchronize_session=False)
    )
    db.commit()
    return {"status": "ok", "marked": upd}


# ---------- Call doctor ----------

REASON_LABELS = {
    "deterioration": "Ухудшение состояния",
    "respiratory":   "Остановка дыхания / гипоксия",
    "arrhythmia":    "Аритмия",
    "bleeding":      "Кровотечение",
    "other":         "Другое",
}


@router.post("/call-doctor", response_model=CallDoctorResponse)
def call_doctor(
    payload: CallDoctorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger an urgent call from any logged-in doctor about a patient.

    Resolves the patient's attending doctor (patients.doctor_id), creates
    a Notification row in their inbox, and best-effort sends an email
    via Resend if it's configured. Returns the notification id and a
    summary of who was paged so the caller can show a confirmation toast.
    """
    pat = db.query(Patient).filter(Patient.id == payload.patient_id).first()
    if not pat:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пациент не найден")

    # The attending doctor is the patient's owner. Fall back to the caller
    # if for some reason the FK is dangling (shouldn't happen with FK in DB).
    target = db.query(User).filter(User.id == pat.doctor_id).first() or current_user

    reason_label = REASON_LABELS.get((payload.reason or "other").lower(), payload.reason or "Другое")
    ward_txt = pat.ward or "—"
    title = f"🚨 Срочный вызов — {pat.full_name}"
    msg_lines = [
        f"Палата: {ward_txt}",
        f"Причина: {reason_label}",
    ]
    if payload.note:
        msg_lines.append(f"Комментарий: {payload.note}")
    msg_lines.append(f"Вызов от: {current_user.full_name or current_user.email}")
    message = "\n".join(msg_lines)

    notif = Notification(
        doctor_id=target.id,
        type="call",
        title=title,
        message=message,
        payload={
            "patient_id": pat.id,
            "patient_name": pat.full_name,
            "ward": ward_txt,
            "reason": payload.reason,
            "reason_label": reason_label,
            "note": payload.note,
            "from_user_id": current_user.id,
            "from_full_name": current_user.full_name,
        },
        is_read=False,
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)

    # Best-effort email — do not block the call on Resend failures.
    try:
        from email_service import send_call_doctor_email
        send_call_doctor_email(
            to=target.email,
            doctor_name=target.full_name or target.email,
            patient_name=pat.full_name,
            ward=ward_txt,
            reason=reason_label,
            note=payload.note,
            caller=current_user.full_name or current_user.email,
        )
    except Exception as e:  # pragma: no cover — email is fire-and-forget
        logger.warning("call-doctor email failed: %s", e)

    return CallDoctorResponse(
        status="ok",
        notified_doctor={
            "id": target.id,
            "full_name": target.full_name,
            "email": target.email,
        },
        notification_id=notif.id,
    )
