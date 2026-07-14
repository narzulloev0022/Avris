"""Patient door API — the patient's own profile and onboarding consent.

Every route here is self-scoped: the resource is always the token owner
(``get_current_patient``), no patient id ever appears in a path or query.
That is the structural guarantee that patient A cannot address patient B.

Consent (``consent_doctors_at``) is the regulatory anchor set once at
onboarding — it is what later allows a network doctor to see the profile
when linking. It never moves once set; linking (T5) must refuse while NULL.
"""
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from audit import audit
from database import get_db
from models import PatientAccount, PatientPreVisitNote
from patient_auth import PatientAccountOut, get_current_patient
from rate_limit import limiter

# Bump when the consent text shown at onboarding changes materially, so old
# acceptances remain distinguishable from new ones.
CURRENT_CONSENT_VERSION = "1.0"

router = APIRouter(prefix="/api/patient", tags=["patient"])


# ---------- schemas ----------

class PatientProfileOut(PatientAccountOut):
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    blood_type: Optional[str] = None
    chronic_conditions: List[str] = []
    allergies: List[str] = []
    medications: List[str] = []


class PatientProfileUpdate(BaseModel):
    """Partial update — only the fields present in the request are written."""
    full_name: Optional[str] = Field(None, max_length=120)
    date_of_birth: Optional[date] = None
    gender: Optional[str] = Field(None, max_length=16)
    height: Optional[float] = Field(None, gt=0, lt=300)
    weight: Optional[float] = Field(None, gt=0, lt=500)
    blood_type: Optional[str] = Field(None, max_length=16)
    chronic_conditions: Optional[List[str]] = None
    allergies: Optional[List[str]] = None
    medications: Optional[List[str]] = None
    language_pref: Optional[str] = Field(None, pattern=r"^(ru|tj|en)$")

    @field_validator("date_of_birth")
    @classmethod
    def dob_not_in_future(cls, v):
        if v is not None and v > date.today():
            raise ValueError("Дата рождения не может быть в будущем")
        return v


class EmergencyProfileOut(BaseModel):
    """Deliberately minimal — ONLY what an emergency responder needs, small
    enough to cache offline on the phone. Any field beyond these five is a
    privacy leak (see test_returns_only_emergency_fields)."""
    avris_patient_id: str
    full_name: Optional[str] = None
    blood_type: Optional[str] = None
    allergies: List[str] = []
    chronic_conditions: List[str] = []


# ---------- endpoints ----------

@router.get("/profile", response_model=PatientProfileOut)
def get_profile(current: PatientAccount = Depends(get_current_patient)):
    return PatientProfileOut.model_validate(current)


@router.get("/emergency", response_model=EmergencyProfileOut)
def emergency_profile(current: PatientAccount = Depends(get_current_patient)):
    """Minimal emergency card for offline caching — same self-scoped auth as
    /profile (a patient reads their own data; consent gates DOCTOR access, not
    self-read). Built explicitly, not from the full account, so no extra field
    can ever ride along."""
    return EmergencyProfileOut(
        avris_patient_id=current.avris_patient_id,
        full_name=current.full_name,
        blood_type=current.blood_type,
        allergies=list(current.allergies or []),
        chronic_conditions=list(current.chronic_conditions or []),
    )


@router.put("/profile", response_model=PatientProfileOut)
@limiter.limit("30/minute")
def update_profile(
    request: Request,
    body: PatientProfileUpdate,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    changed = body.model_dump(exclude_unset=True)
    for field, value in changed.items():
        setattr(current, field, value)
    db.commit()
    db.refresh(current)
    # PHI-free: field names only, never values.
    audit(db, action="update", entity="patient_account", user_id=None,
          entity_id=current.id, meta={"door": "patient", "fields": sorted(changed)})
    return PatientProfileOut.model_validate(current)


class ConsentBody(BaseModel):
    version: Optional[str] = None  # which consent text the client displayed


@router.post("/consent", response_model=PatientProfileOut)
@limiter.limit("10/minute")
def give_consent(
    request: Request,
    body: Optional[ConsentBody] = None,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Onboarding consent to doctor access. Idempotent: the first timestamp
    AND version are the legally meaningful ones and are never overwritten."""
    if current.consent_doctors_at is None:
        current.consent_doctors_at = datetime.utcnow()
        current.consent_version = (body.version if body else None) or CURRENT_CONSENT_VERSION
        db.commit()
        db.refresh(current)
        audit(db, action="consent", entity="patient_account", user_id=None,
              entity_id=current.id, meta={"door": "patient", "version": current.consent_version})
    return PatientProfileOut.model_validate(current)


# ---------- pre-visit note ----------

class PreVisitNoteBody(BaseModel):
    note_text: str = Field(min_length=1, max_length=300)

    @field_validator("note_text")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Заметка не может быть пустой")
        return v


class PreVisitNoteOut(BaseModel):
    note_text: str
    created_at: datetime


@router.post("/pre-visit-note", response_model=PreVisitNoteOut)
@limiter.limit("20/minute")
def upsert_pre_visit_note(
    request: Request,
    body: PreVisitNoteBody,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Create-or-update the patient's single ACTIVE pre-visit note (self-scoped,
    like everything in this file). No consent check here on purpose: the note is
    invisible to anyone until a doctor confirms a link, and confirm_link already
    enforces the consent + PatientLink gate — consent is reused there, never
    duplicated. If the current note was already seen by a doctor it is history,
    so a new POST starts a fresh note instead of reviving the old one."""
    note = db.query(PatientPreVisitNote).filter(
        PatientPreVisitNote.patient_account_id == current.id,
        PatientPreVisitNote.seen_at.is_(None),
    ).first()
    if note is None:
        note = PatientPreVisitNote(patient_account_id=current.id, note_text=body.note_text)
        db.add(note)
    else:
        note.note_text = body.note_text
        note.created_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    audit(db, action="upsert", entity="patient_previsit_note", user_id=None,
          entity_id=note.id, meta={"door": "patient"})
    return PreVisitNoteOut(note_text=note.note_text, created_at=note.created_at)
