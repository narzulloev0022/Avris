import logging
import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import Base, engine, get_db, init_db
import models  # noqa: F401 — register tables on Base.metadata
from models import Consultation, LabOrder, NightRound, Patient, User

router = APIRouter(prefix="/api/admin", tags=["admin"])
log = logging.getLogger(__name__)


class ResetRequest(BaseModel):
    confirm: str


def _admin_key() -> str | None:
    """Prefer a dedicated reset key so we don't have to expose SECRET_KEY,
    but accept SECRET_KEY if no ADMIN_RESET_KEY is set."""
    return os.getenv("ADMIN_RESET_KEY") or os.getenv("SECRET_KEY")


@router.post("/reset-db")
def reset_db(
    payload: ResetRequest,
    x_admin_reset_key: str = Header(default=""),
):
    """Drop every table SQLAlchemy knows about and recreate the schema empty.

    Auth: header `X-Admin-Reset-Key` must match ADMIN_RESET_KEY (or SECRET_KEY).
    Safety: body must be `{"confirm": "DROP_ALL_DATA"}` to prevent fat-fingering.
    """
    expected = _admin_key()
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Reset endpoint disabled — neither ADMIN_RESET_KEY nor SECRET_KEY set",
        )
    if not secrets.compare_digest(x_admin_reset_key or "", expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid reset key")
    if payload.confirm != "DROP_ALL_DATA":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            'Missing confirmation: send {"confirm": "DROP_ALL_DATA"}',
        )

    tables = sorted(Base.metadata.tables.keys())
    log.warning("admin/reset-db invoked, dropping tables: %s", tables)

    Base.metadata.drop_all(bind=engine)
    init_db()  # recreates schema + runs the lightweight migrations

    return {
        "status": "ok",
        "dropped": tables,
        "recreated": sorted(Base.metadata.tables.keys()),
    }


@router.post("/cleanup-non-admins")
def cleanup_non_admins(
    payload: ResetRequest,
    x_admin_reset_key: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Delete every non-admin user and all rows they own (patients,
    consultations, lab orders incl. attached files via FK CASCADE,
    night rounds). The admin row itself is preserved.

    Auth and safety match /reset-db: X-Admin-Reset-Key + confirm body.
    """
    expected = _admin_key()
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Cleanup endpoint disabled — neither ADMIN_RESET_KEY nor SECRET_KEY set",
        )
    if not secrets.compare_digest(x_admin_reset_key or "", expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid reset key")
    if payload.confirm != "DROP_ALL_DATA":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            'Missing confirmation: send {"confirm": "DROP_ALL_DATA"}',
        )

    admin = db.query(User).filter(User.is_admin.is_(True)).order_by(User.id.asc()).first()
    if not admin:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No admin user exists — refusing to delete every user",
        )

    log.warning(
        "admin/cleanup-non-admins invoked, preserving admin id=%s email=%s",
        admin.id, admin.email,
    )

    # Order matters: child rows first to avoid FK violations even though
    # most relationships use ondelete=CASCADE.
    nr_deleted = (
        db.query(NightRound)
        .filter(NightRound.doctor_id != admin.id)
        .delete(synchronize_session=False)
    )
    lab_deleted = (
        db.query(LabOrder)
        .filter(LabOrder.doctor_id != admin.id)
        .delete(synchronize_session=False)
    )
    cons_deleted = (
        db.query(Consultation)
        .filter(Consultation.doctor_id != admin.id)
        .delete(synchronize_session=False)
    )
    pat_deleted = (
        db.query(Patient)
        .filter(Patient.doctor_id != admin.id)
        .delete(synchronize_session=False)
    )
    users_deleted = (
        db.query(User)
        .filter(User.is_admin.is_(False))
        .delete(synchronize_session=False)
    )
    db.commit()

    return {
        "status": "ok",
        "preserved_admin": {"id": admin.id, "email": admin.email},
        "deleted": {
            "users": users_deleted,
            "patients": pat_deleted,
            "consultations": cons_deleted,
            "lab_orders": lab_deleted,
            "night_rounds": nr_deleted,
        },
    }


@router.post("/cleanup-medical-data")
def cleanup_medical_data(
    payload: ResetRequest,
    x_admin_reset_key: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Wipe every patient / consultation / lab order / night round in the
    DB while leaving every User row (including non-admins) intact.

    Use case: an admin who registered before seed_demo_patients_for was
    removed from verify_email still has 6 demo rows in their patient
    list. cleanup-non-admins preserves admin's data, so it doesn't
    touch them. This endpoint targets exactly that scenario without
    having to drop the admin row and re-onboard.
    """
    expected = _admin_key()
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Cleanup endpoint disabled — neither ADMIN_RESET_KEY nor SECRET_KEY set",
        )
    if not secrets.compare_digest(x_admin_reset_key or "", expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid reset key")
    if payload.confirm != "DROP_ALL_DATA":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            'Missing confirmation: send {"confirm": "DROP_ALL_DATA"}',
        )

    log.warning("admin/cleanup-medical-data invoked, wiping all medical rows")

    nr_deleted = db.query(NightRound).delete(synchronize_session=False)
    lab_deleted = db.query(LabOrder).delete(synchronize_session=False)
    cons_deleted = db.query(Consultation).delete(synchronize_session=False)
    pat_deleted = db.query(Patient).delete(synchronize_session=False)
    db.commit()

    return {
        "status": "ok",
        "users_kept": db.query(User).count(),
        "deleted": {
            "patients": pat_deleted,
            "consultations": cons_deleted,
            "lab_orders": lab_deleted,
            "night_rounds": nr_deleted,
        },
    }
