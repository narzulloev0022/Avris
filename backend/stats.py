"""Dashboard stats — single round-trip for every Dashboard widget.

Returned by GET /api/stats/dashboard. The frontend calls this on init,
after saveConsult, after savePatForm, and after fetchPatients to keep
every card live without a per-widget fetch fan-out.
"""
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import Consultation, LabOrder, Patient, User
from auth import get_current_user

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Per-consultation manual-SOAP-entry baseline. Real-world doctor surveys put
# manual SOAP charting at 12-20 minutes per encounter; we use the round-number
# midpoint and let the marketing copy say "≈15 минут / приём".
TIME_SAVED_PER_CONSULTATION_MIN = 15


class ActivityItem(BaseModel):
    type: str  # "soap" | "lab" | "patient"
    title: str
    patient: Optional[str] = None
    timestamp: datetime


class DashboardStats(BaseModel):
    active_patients: int
    critical_patients: int
    avg_score: Optional[int] = None
    scored_patients: int

    consultations_today: int
    consultations_24h: int

    soap_accurate: int
    soap_edited: int
    soap_total: int
    accuracy_pct: Optional[int] = None  # null when no AI saves yet

    time_saved_minutes: int  # consultations_today × 15
    time_saved_per_consultation_min: int = TIME_SAVED_PER_CONSULTATION_MIN

    recent_activity: list[ActivityItem]


@router.get("/dashboard", response_model=DashboardStats)
def dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    me = current_user.id
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    day_ago = now - timedelta(hours=24)

    # ----- Patient-derived metrics -----
    pats = db.query(Patient).filter(Patient.doctor_id == me, Patient.is_active.is_(True)).all()
    active_patients = len(pats)
    scored = [p.avris_score for p in pats if p.avris_score is not None]
    scored_patients = len(scored)
    critical_patients = sum(1 for s in scored if s < 40)
    avg_score = round(sum(scored) / len(scored)) if scored else None

    # ----- Consultation counts -----
    consultations_today = (
        db.query(func.count(Consultation.id))
        .filter(Consultation.doctor_id == me, Consultation.created_at >= today_start)
        .scalar() or 0
    )
    consultations_24h = (
        db.query(func.count(Consultation.id))
        .filter(Consultation.doctor_id == me, Consultation.created_at >= day_ago)
        .scalar() or 0
    )

    # ----- AI accuracy -----
    soap_accurate = current_user.soap_accurate_count or 0
    soap_edited = current_user.soap_edited_count or 0
    soap_total = soap_accurate + soap_edited
    accuracy_pct = round(soap_accurate / soap_total * 100) if soap_total > 0 else None

    # ----- Activity feed: last 5 across consultations + labs + patients -----
    recent: list[ActivityItem] = []

    last_consults = (
        db.query(Consultation)
        .filter(Consultation.doctor_id == me)
        .order_by(Consultation.created_at.desc())
        .limit(5)
        .all()
    )
    pat_name_by_id = {p.id: p.full_name for p in pats}
    for c in last_consults:
        recent.append(ActivityItem(
            type="soap",
            title="SOAP",
            patient=pat_name_by_id.get(c.patient_id) if c.patient_id else None,
            timestamp=c.created_at,
        ))

    last_labs = (
        db.query(LabOrder)
        .filter(LabOrder.doctor_id == me, LabOrder.status == "received", LabOrder.received_at.isnot(None))
        .order_by(LabOrder.received_at.desc())
        .limit(3)
        .all()
    )
    for lo in last_labs:
        recent.append(ActivityItem(
            type="lab",
            title="Lab results",
            patient=pat_name_by_id.get(lo.patient_id) if lo.patient_id else None,
            timestamp=lo.received_at or lo.created_at,
        ))

    last_pats = sorted(pats, key=lambda p: p.created_at, reverse=True)[:3]
    for p in last_pats:
        recent.append(ActivityItem(
            type="patient",
            title="New patient",
            patient=p.full_name,
            timestamp=p.created_at,
        ))

    recent.sort(key=lambda a: a.timestamp, reverse=True)
    recent = recent[:5]

    return DashboardStats(
        active_patients=active_patients,
        critical_patients=critical_patients,
        avg_score=avg_score,
        scored_patients=scored_patients,
        consultations_today=consultations_today,
        consultations_24h=consultations_24h,
        soap_accurate=soap_accurate,
        soap_edited=soap_edited,
        soap_total=soap_total,
        accuracy_pct=accuracy_pct,
        time_saved_minutes=consultations_today * TIME_SAVED_PER_CONSULTATION_MIN,
        time_saved_per_consultation_min=TIME_SAVED_PER_CONSULTATION_MIN,
        recent_activity=recent,
    )
