"""Linking — the junction where the patient door meets the doctor door.

Flow: the consented patient issues a one-time 6-digit code (TTL 5 min,
DB row = source of truth); the doctor previews the profile by code and
confirms — which creates a doctor-scoped ``patients`` row prefilled from
the account profile and bridges the two via ``patient_links``.

Name fallback (no code): link by Avris Patient ID + exact full-name
confirmation typed by the doctor. Weaker than a live code by design —
consent is still mandatory and every link is audited.

The Lab-Connect pattern (bare UUID, auth = knowing the token) is NOT used
here: a medical-record link demands consent + short TTL + single use.
"""
import secrets
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from jose import jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit import audit
from auth import ALGORITHM, SECRET_KEY, get_current_user
from database import get_db
from models import Patient, PatientAccount, PatientLink, PatientLinkCode, User
from patient_auth import get_current_patient
from rate_limit import limiter

LINK_CODE_TTL_MINUTES = 5
LINK_AUDIENCE = "patient-link"

patient_router = APIRouter(prefix="/api/patient", tags=["patient"])
doctor_router = APIRouter(prefix="/api/patient-links", tags=["patient-links"])


# ---------- schemas ----------

class LinkCodeOut(BaseModel):
    code: str
    qr_payload: str
    expires_at: datetime


class LinkPreviewOut(BaseModel):
    full_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    blood_type: Optional[str] = None
    allergies: List[str] = []


class LinkConfirmBody(BaseModel):
    """Either a live 6-digit code (primary) or Avris ID + exact name (fallback)."""
    code: Optional[str] = None
    avris_patient_id: Optional[str] = None
    full_name: Optional[str] = None


class LinkedPatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    blood_type: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    bmi: Optional[str] = None
    allergies: List[str] = []
    diagnoses: List[str] = []
    medications: List[str] = []


class LinkResultOut(BaseModel):
    created: bool
    link_id: int
    patient: LinkedPatientOut


# ---------- helpers ----------

def _age_from_dob(dob: Optional[date]) -> Optional[int]:
    if dob is None:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


_GENDER_RU = {"female": "Ж", "male": "М"}


def _initials(full_name: Optional[str]) -> Optional[str]:
    if not full_name:
        return None
    parts = full_name.split()
    return "".join(p[0] for p in parts[:2]).upper() or None


def _bmi(height: Optional[float], weight: Optional[float]) -> Optional[str]:
    if not height or not weight:
        return None
    return f"{weight / (height / 100) ** 2:.1f}"


def _resolve_code_row(db: Session, code: str) -> PatientLinkCode:
    """404 unknown, 410 expired/used — the row (not the QR) is authoritative."""
    row = db.query(PatientLinkCode).filter(PatientLinkCode.code == code).first()
    if not row:
        raise HTTPException(status_code=404, detail="Код не найден")
    if row.used_at is not None:
        raise HTTPException(status_code=410, detail="Код уже использован")
    if datetime.utcnow() > row.expires_at:
        raise HTTPException(status_code=410, detail="Код истёк — попросите пациента показать новый")
    return row


def _account_or_403(db: Session, account_id: int) -> PatientAccount:
    account = db.query(PatientAccount).filter(PatientAccount.id == account_id).first()
    if not account or not account.is_active:
        raise HTTPException(status_code=404, detail="Аккаунт пациента не найден")
    if account.consent_doctors_at is None:
        raise HTTPException(status_code=403, detail="Пациент не дал согласие на доступ врачей")
    return account


def _prefill_patient(account: PatientAccount, doctor_id: int) -> Patient:
    """Doctor-scoped row born from the patient's own profile. RU fields only —
    the cabinet's *_en variants stay empty until the doctor fills them."""
    return Patient(
        doctor_id=doctor_id,
        full_name=account.full_name or "Пациент Avris",
        age=_age_from_dob(account.date_of_birth),
        gender=_GENDER_RU.get(account.gender or "", account.gender),
        blood_type=account.blood_type,
        height=account.height,
        weight=account.weight,
        bmi=_bmi(account.height, account.weight),
        initials=_initials(account.full_name),
        patient_type="outpatient",
        allergies=list(account.allergies or []),
        diagnoses=list(account.chronic_conditions or []),
        medications=list(account.medications or []),
    )


def _link(db: Session, doctor: User, account: PatientAccount, method: str) -> LinkResultOut:
    existing = db.query(PatientLink).filter(
        PatientLink.patient_account_id == account.id,
        PatientLink.doctor_id == doctor.id,
    ).first()
    if existing:
        patient = db.query(Patient).filter(Patient.id == existing.patient_id).first()
        return LinkResultOut(created=False, link_id=existing.id,
                             patient=LinkedPatientOut.model_validate(patient))

    patient = _prefill_patient(account, doctor.id)
    db.add(patient)
    db.flush()
    link = PatientLink(patient_account_id=account.id, patient_id=patient.id,
                       doctor_id=doctor.id, method=method)
    db.add(link)
    try:
        db.commit()
    except IntegrityError:
        # Two receptions confirming simultaneously — return the winner's link.
        db.rollback()
        existing = db.query(PatientLink).filter(
            PatientLink.patient_account_id == account.id,
            PatientLink.doctor_id == doctor.id,
        ).first()
        patient = db.query(Patient).filter(Patient.id == existing.patient_id).first()
        return LinkResultOut(created=False, link_id=existing.id,
                             patient=LinkedPatientOut.model_validate(patient))

    audit(db, action="create", entity="patient_link", user_id=doctor.id,
          entity_id=link.id, meta={"method": method, "patient_id": patient.id})
    return LinkResultOut(created=True, link_id=link.id,
                         patient=LinkedPatientOut.model_validate(patient))


# ---------- patient side ----------

@patient_router.post("/link-code", response_model=LinkCodeOut)
@limiter.limit("10/minute")
def issue_link_code(
    request: Request,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    if current.consent_doctors_at is None:
        raise HTTPException(
            status_code=403,
            detail="Сначала дайте согласие на доступ врачей (в настройках профиля)",
        )
    # Lazy purge, then insert with retry on the rare 6-digit collision.
    db.query(PatientLinkCode).filter(
        PatientLinkCode.expires_at < datetime.utcnow()
    ).delete(synchronize_session=False)
    expires = datetime.utcnow() + timedelta(minutes=LINK_CODE_TTL_MINUTES)
    for _ in range(5):
        code = f"{secrets.randbelow(1_000_000):06d}"
        db.add(PatientLinkCode(code=code, patient_account_id=current.id, expires_at=expires))
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
    else:
        raise HTTPException(status_code=500, detail="Не удалось выдать код, попробуйте ещё раз")

    # QR transport for a future camera scan in the cabinet; the DB row stays
    # the source of truth, the signature only prevents QR forgery offline.
    qr_payload = jwt.encode(
        {"sub": str(current.id), "code": code, "type": "link",
         "aud": LINK_AUDIENCE, "exp": expires},
        SECRET_KEY, algorithm=ALGORITHM,
    )
    return LinkCodeOut(code=code, qr_payload=qr_payload, expires_at=expires)


# ---------- doctor side ----------

@doctor_router.get("/preview", response_model=LinkPreviewOut)
@limiter.limit("30/minute")
def preview_by_code(
    request: Request,
    code: str = Query(min_length=6, max_length=6),
    doctor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _resolve_code_row(db, code)
    account = _account_or_403(db, row.patient_account_id)
    return LinkPreviewOut(
        full_name=account.full_name,
        date_of_birth=account.date_of_birth,
        gender=account.gender,
        blood_type=account.blood_type,
        allergies=list(account.allergies or []),
    )


@doctor_router.post("", response_model=LinkResultOut, status_code=201)
@limiter.limit("20/minute")
def confirm_link(
    request: Request,
    body: LinkConfirmBody,
    response: Response,
    doctor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.code:
        row = _resolve_code_row(db, body.code.strip())
        account = _account_or_403(db, row.patient_account_id)
        row.used_at = datetime.utcnow()
        db.commit()
        result = _link(db, doctor, account, method="qr")
    elif body.avris_patient_id and body.full_name:
        account = db.query(PatientAccount).filter(
            PatientAccount.avris_patient_id == body.avris_patient_id.strip().upper()
        ).first()
        # Name mismatch and unknown ID are the same 404 on purpose — the
        # fallback must not leak which Avris IDs exist.
        if not account or not account.full_name or \
                account.full_name.strip().casefold() != body.full_name.strip().casefold():
            raise HTTPException(status_code=404, detail="Пациент не найден или имя не совпадает")
        if account.consent_doctors_at is None:
            raise HTTPException(status_code=403, detail="Пациент не дал согласие на доступ врачей")
        result = _link(db, doctor, account, method="name")
    else:
        raise HTTPException(status_code=422, detail="Нужен код или Avris ID + ФИО")

    if not result.created:
        response.status_code = 200  # идемпотентный повтор — не «создано»
    return result
