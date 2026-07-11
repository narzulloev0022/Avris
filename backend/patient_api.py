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
from models import PatientAccount
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


# ---------- endpoints ----------

@router.get("/profile", response_model=PatientProfileOut)
def get_profile(current: PatientAccount = Depends(get_current_patient)):
    return PatientProfileOut.model_validate(current)


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
