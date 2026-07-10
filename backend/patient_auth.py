"""Patient door authentication — passwordless OTP login + JWT with aud="patient".

The patient app and the doctor cabinet share one backend but are two separate
identity spaces. Patient JWTs always carry ``aud="patient"``; doctor tokens
never do. python-jose only *rejects* a foreign ``aud`` claim — it does NOT
require one — so decode_patient_token() checks the claim explicitly instead
of trusting the ``audience=`` argument alone. Never relax that check.

OTP delivery: email goes through Resend (same as the doctor flow). Phone has
no SMS provider yet — with PATIENT_DEV_OTP set (demo/dev) the stored code is
that fixed value; without it phone contacts get 503 until an SMS gateway for
Tajikistan is wired up.
"""
import os
import re
from datetime import datetime, timedelta
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit import audit
from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    REFRESH_TOKEN_EXPIRE_MINUTES,
    RESEND_COOLDOWN_SECONDS,
    SECRET_KEY,
    _check_code,
    _delete_code,
    _generate_code,
    _store_code,
    oauth2_scheme,
)
from database import get_db
from email_service import send_verification_code
from models import PatientAccount, PatientRefreshToken
from patient_ids import new_avris_patient_id
from rate_limit import limiter

PATIENT_AUDIENCE = "patient"
OTP_PURPOSE = "patient_otp"
OTP_TTL_MINUTES = 15
PATIENT_DEV_OTP = os.getenv("PATIENT_DEV_OTP", "")

_PHONE_RE = re.compile(r"^\+?\d{7,15}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter(prefix="/api/patient/auth", tags=["patient-auth"])


# ---------- tokens ----------

def create_patient_access_token(account_id: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    # jti makes every token unique even within the same second (and leaves the
    # door open for access-token revocation later).
    payload = {"sub": str(account_id), "exp": expire, "type": "access",
               "aud": PATIENT_AUDIENCE, "jti": str(uuid.uuid4())}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_patient_refresh_token(account_id: int, db: Session) -> str:
    db.query(PatientRefreshToken).filter(
        PatientRefreshToken.expires_at < datetime.utcnow()
    ).delete()
    jti = str(uuid.uuid4())
    expire = datetime.utcnow() + timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES)
    db.add(PatientRefreshToken(jti=jti, patient_account_id=account_id, expires_at=expire))
    db.commit()
    payload = {"sub": str(account_id), "exp": expire, "type": "refresh",
               "jti": jti, "aud": PATIENT_AUDIENCE}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_patient_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM],
                             audience=PATIENT_AUDIENCE)
        # Explicit aud check: python-jose skips aud validation entirely when the
        # claim is absent, so a doctor token would otherwise slip through.
        if payload.get("aud") != PATIENT_AUDIENCE or payload.get("type") != "access":
            return None
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (JWTError, ValueError):
        return None


def decode_patient_refresh_token(token: str) -> Optional[tuple]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM],
                             audience=PATIENT_AUDIENCE)
        if payload.get("aud") != PATIENT_AUDIENCE or payload.get("type") != "refresh":
            return None
        sub, jti = payload.get("sub"), payload.get("jti")
        if sub is None or not jti:
            return None
        return int(sub), jti
    except (JWTError, ValueError):
        return None


def get_current_patient(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> PatientAccount:
    creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Недействительные учётные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise creds_exc
    account_id = decode_patient_token(token)
    if account_id is None:
        raise creds_exc
    account = db.query(PatientAccount).filter(PatientAccount.id == account_id).first()
    if not account or not account.is_active:
        raise creds_exc
    return account


# ---------- schemas ----------

class RequestOtpBody(BaseModel):
    contact: str  # phone (+992...) or email


class VerifyOtpBody(BaseModel):
    contact: str
    code: str


class PatientAccountOut(BaseModel):
    id: int
    avris_patient_id: str
    phone: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    consent_doctors_at: Optional[datetime] = None
    language_pref: str = "ru"

    model_config = ConfigDict(from_attributes=True)


class PatientTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    is_new: bool
    account: PatientAccountOut


class PatientRefreshBody(BaseModel):
    refresh_token: str


class PatientRefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# ---------- helpers ----------

def _normalize_contact(raw: str) -> tuple:
    """Returns (kind, normalized) where kind is 'email'|'phone'. 400 otherwise."""
    contact = raw.strip()
    if "@" in contact:
        contact = contact.lower()
        if not _EMAIL_RE.fullmatch(contact):
            raise HTTPException(status_code=400, detail="Некорректный email")
        return "email", contact
    contact = re.sub(r"[\s\-()]", "", contact)
    if not _PHONE_RE.fullmatch(contact):
        raise HTTPException(status_code=400, detail="Некорректный номер телефона")
    return "phone", contact


def _create_account(db: Session, kind: str, contact: str) -> PatientAccount:
    """Insert with a fresh Avris Patient ID; retry on the (astronomically rare)
    ID collision — uniqueness is enforced by the DB constraint."""
    for _ in range(3):
        account = PatientAccount(
            avris_patient_id=new_avris_patient_id(),
            phone=contact if kind == "phone" else None,
            email=contact if kind == "email" else None,
        )
        db.add(account)
        try:
            db.commit()
            return account
        except IntegrityError:
            db.rollback()
    raise HTTPException(status_code=500, detail="Не удалось создать аккаунт, попробуйте ещё раз")


def _find_account(db: Session, kind: str, contact: str) -> Optional[PatientAccount]:
    field = PatientAccount.phone if kind == "phone" else PatientAccount.email
    return db.query(PatientAccount).filter(field == contact).first()


# ---------- endpoints ----------

@router.post("/request-otp")
@limiter.limit("5/minute")
def request_otp(request: Request, body: RequestOtpBody, db: Session = Depends(get_db)):
    kind, contact = _normalize_contact(body.contact)

    if kind == "phone":
        if not PATIENT_DEV_OTP:
            raise HTTPException(
                status_code=503,
                detail="SMS-доставка кодов пока не подключена — используйте email",
            )
        code = PATIENT_DEV_OTP
    else:
        code = _generate_code()

    _store_code(db, OTP_PURPOSE, contact, code, ttl_minutes=OTP_TTL_MINUTES,
                resend_cooldown_seconds=RESEND_COOLDOWN_SECONDS)

    if kind == "email":
        if not send_verification_code(contact, code):
            raise HTTPException(status_code=503, detail="Не удалось отправить код на email")

    return {"message": "Код отправлен", "contact": contact}


@router.post("/verify-otp", response_model=PatientTokenResponse)
@limiter.limit("10/minute")
def verify_otp(request: Request, body: VerifyOtpBody, db: Session = Depends(get_db)):
    kind, contact = _normalize_contact(body.contact)
    _check_code(db, OTP_PURPOSE, contact, body.code)
    _delete_code(db, OTP_PURPOSE, contact)

    account = _find_account(db, kind, contact)
    is_new = account is None
    if is_new:
        account = _create_account(db, kind, contact)

    audit(db, action="login", entity="patient_account", user_id=None,
          entity_id=account.id, meta={"door": "patient", "new": is_new, "via": kind})

    return PatientTokenResponse(
        access_token=create_patient_access_token(account.id),
        refresh_token=create_patient_refresh_token(account.id, db),
        is_new=is_new,
        account=PatientAccountOut.model_validate(account),
    )


@router.post("/refresh", response_model=PatientRefreshResponse)
@limiter.limit("20/minute")
def refresh(request: Request, body: PatientRefreshBody, db: Session = Depends(get_db)):
    decoded = decode_patient_refresh_token(body.refresh_token)
    if decoded is None:
        raise HTTPException(status_code=401, detail="Недействительный refresh-токен")
    account_id, jti = decoded
    row = db.query(PatientRefreshToken).filter(PatientRefreshToken.jti == jti).first()
    if (row is None or row.revoked or row.expires_at < datetime.utcnow()
            or row.patient_account_id != account_id):
        raise HTTPException(status_code=401, detail="Недействительный refresh-токен")
    account = db.query(PatientAccount).filter(PatientAccount.id == account_id).first()
    if not account or not account.is_active:
        raise HTTPException(status_code=401, detail="Аккаунт недоступен")
    # Rotation: the presented token dies the moment a new one is issued
    # (same policy as the doctor door).
    row.revoked = True
    db.commit()
    return PatientRefreshResponse(
        access_token=create_patient_access_token(account.id),
        refresh_token=create_patient_refresh_token(account.id, db),
    )


@router.get("/me", response_model=PatientAccountOut)
def me(current: PatientAccount = Depends(get_current_patient)):
    return PatientAccountOut.model_validate(current)
