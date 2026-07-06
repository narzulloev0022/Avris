import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
import base64
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from audit import audit
from database import get_db
from models import AuthCode, RefreshToken, User
from schemas import (
    UserCreate, UserLogin, UserResponse, Token, RegisterResponse,
    VerifyEmailRequest, ResendCodeRequest,
    ForgotPasswordRequest, ResetPasswordRequest,
    UpdateProfileRequest, MessageResponse,
    RefreshRequest, RefreshResponse,
)
from email_service import send_password_reset_code, send_verification_code
from rate_limit import limiter

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60          # short-lived access token (M2)
REFRESH_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # refresh token: 7 days
RESET_CODE_TTL_MINUTES = 15

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8080")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

MAILRU_CLIENT_ID = os.getenv("MAILRU_CLIENT_ID", "")
MAILRU_CLIENT_SECRET = os.getenv("MAILRU_CLIENT_SECRET", "")
MAILRU_REDIRECT_URI = os.getenv("MAILRU_REDIRECT_URI", "http://localhost:8000/api/auth/mailru/callback")
MAILRU_AUTH_URL = "https://oauth.mail.ru/login"
MAILRU_TOKEN_URL = "https://oauth.mail.ru/token"
MAILRU_USERINFO_URL = "https://oauth.mail.ru/userinfo"

OAUTH_STATE_TTL_MINUTES = 10

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# OTP/state storage lives in the auth_codes table (see models.AuthCode) so it
# survives restarts and works across multiple instances. The attempts counter
# increments on every wrong code; after MAX_OTP_ATTEMPTS the row is deleted and
# the user must request a new code — the single mitigation against
# brute-forcing 6-digit codes.
RESEND_COOLDOWN_SECONDS = 60
MAX_OTP_ATTEMPTS = 5

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------- helpers ----------

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_access_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "exp": expire, "type": "access"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: int, db: Session) -> str:
    # Lazy purge keeps refresh_tokens small without a cron job.
    db.query(RefreshToken).filter(RefreshToken.expires_at < datetime.utcnow()).delete()
    jti = str(uuid.uuid4())
    expire = datetime.utcnow() + timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES)
    db.add(RefreshToken(jti=jti, user_id=user_id, expires_at=expire))
    db.commit()
    payload = {"sub": str(user_id), "exp": expire, "type": "refresh", "jti": jti}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[int]:
    # Accepts access tokens (and legacy tokens without a "type" claim, so JWTs
    # issued before the access/refresh split keep working until they expire).
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") == "refresh":
            return None  # refresh tokens are not valid for API auth
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (JWTError, ValueError):
        return None


def decode_refresh_token(token: str) -> Optional[tuple]:
    """Returns (user_id, jti) or None. jti is mandatory: legacy refresh JWTs
    issued before revocation support are rejected — those users simply
    re-login once."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        sub, jti = payload.get("sub"), payload.get("jti")
        if sub is None or not jti:
            return None
        return int(sub), jti
    except (JWTError, ValueError):
        return None


def _active_refresh_row(db: Session, jti: str) -> Optional[RefreshToken]:
    row = db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
    if not row or row.revoked or row.expires_at < datetime.utcnow():
        return None
    return row


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise creds_exc
    user_id = decode_token(token)
    if user_id is None:
        raise creds_exc
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise creds_exc
    return user


# ---------- endpoints ----------

def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _purge_expired_codes(db: Session) -> None:
    # Lazy cleanup — called on every store, keeps auth_codes small without a cron.
    db.query(AuthCode).filter(AuthCode.expires_at < datetime.utcnow()).delete(
        synchronize_session=False
    )


def _store_code(db: Session, purpose: str, key: str, code: str,
                ttl_minutes: int, resend_cooldown_seconds: Optional[int] = None) -> None:
    _purge_expired_codes(db)
    row = db.query(AuthCode).filter(
        AuthCode.purpose == purpose, AuthCode.key == key
    ).first()
    if not row:
        row = AuthCode(purpose=purpose, key=key)
        db.add(row)
    row.code_hash = _hash_code(code)
    row.attempts = 0
    row.expires_at = datetime.utcnow() + timedelta(minutes=ttl_minutes)
    row.resend_after = (
        datetime.utcnow() + timedelta(seconds=resend_cooldown_seconds)
        if resend_cooldown_seconds else None
    )
    db.commit()


def _check_code(db: Session, purpose: str, key: str, code: str) -> None:
    """Validate an OTP without consuming it. Raises HTTPException on failure;
    the caller deletes the row via _delete_code() once the whole flow succeeds
    (mirrors the old dict semantics: a 404 later must not burn the code)."""
    row = db.query(AuthCode).filter(
        AuthCode.purpose == purpose, AuthCode.key == key
    ).first()
    if not row:
        raise HTTPException(status_code=400, detail="Код не найден или устарел")
    if datetime.utcnow() > row.expires_at:
        db.delete(row)
        db.commit()
        raise HTTPException(status_code=400, detail="Код истёк, запросите новый")
    if not secrets.compare_digest(row.code_hash, _hash_code(code.strip())):
        row.attempts += 1
        if row.attempts >= MAX_OTP_ATTEMPTS:
            db.delete(row)
            db.commit()
            raise HTTPException(status_code=429, detail="Код аннулирован. Запросите новый.")
        db.commit()
        raise HTTPException(status_code=400, detail="Неверный код")


def _delete_code(db: Session, purpose: str, key: str) -> None:
    db.query(AuthCode).filter(
        AuthCode.purpose == purpose, AuthCode.key == key
    ).delete(synchronize_session=False)
    db.commit()


def _set_verify_code(db: Session, email: str) -> str:
    code = _generate_code()
    _store_code(db, "verify", email, code, ttl_minutes=15,
                resend_cooldown_seconds=RESEND_COOLDOWN_SECONDS)
    return code


def _issue_oauth_state(db: Session) -> str:
    state = secrets.token_urlsafe(16)
    _purge_expired_codes(db)
    db.add(AuthCode(
        purpose="oauth", key=state, code_hash="",
        expires_at=datetime.utcnow() + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
    ))
    db.commit()
    return state


def _consume_oauth_state(db: Session, state: str) -> bool:
    row = db.query(AuthCode).filter(
        AuthCode.purpose == "oauth", AuthCode.key == state
    ).first()
    if not row:
        return False
    expired = datetime.utcnow() > row.expires_at
    db.delete(row)
    db.commit()
    return not expired


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def register(request: Request, payload: UserCreate, db: Session = Depends(get_db)):
    email = payload.email.lower()
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if existing.is_verified:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Пользователь с таким email уже существует",
            )
        # Unverified — allow re-registration: update password + resend code
        existing.password_hash = hash_password(payload.password)
        if payload.full_name:
            existing.full_name = payload.full_name
        db.commit()
        user = existing
    else:
        # First registered user becomes admin and is auto-approved
        is_first = db.query(User).count() == 0
        user = User(
            email=email,
            password_hash=hash_password(payload.password),
            full_name=payload.full_name or email.split("@")[0],
            specialty=payload.specialty,
            is_verified=False,
            is_admin=is_first,
            is_approved=is_first,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    code = _set_verify_code(db, email)
    send_verification_code(email, code, user.full_name)
    return RegisterResponse(
        message="Код отправлен на email",
        requires_verification=True,
        email=email,
    )


@router.post("/verify-email", response_model=Token)
@limiter.limit("10/minute")
def verify_email(request: Request, payload: VerifyEmailRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()
    _check_code(db, "verify", email, payload.code)
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.is_verified = True
    db.commit()
    db.refresh(user)
    _delete_code(db, "verify", email)
    # Notify admins of pending doctor (only if user not auto-approved)
    if not user.is_approved:
        try:
            from email_service import send_admin_new_doctor_alert
            admins = db.query(User).filter(User.is_admin.is_(True)).all()
            for adm in admins:
                send_admin_new_doctor_alert(
                    adm.email, user.full_name, user.email,
                    user.specialty or "", user.hospital_name or "",
                )
        except Exception:
            pass
    token = create_access_token(user.id)
    refresh = create_refresh_token(user.id, db)
    return Token(access_token=token, refresh_token=refresh, user=UserResponse.model_validate(user))


@router.post("/refresh", response_model=RefreshResponse)
@limiter.limit("30/minute")
def refresh_token(request: Request, payload: RefreshRequest, db: Session = Depends(get_db)):
    decoded = decode_refresh_token(payload.refresh_token)
    if decoded is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный refresh-токен",
        )
    uid, jti = decoded
    row = _active_refresh_row(db, jti)
    if row is None or row.user_id != uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный refresh-токен",
        )
    user = db.query(User).filter(User.id == uid).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь недоступен",
        )
    # Rotation: the presented token dies the moment a new one is issued,
    # so a leaked refresh token can't be replayed after its legit use.
    row.revoked = True
    db.commit()
    return RefreshResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id, db),
    )


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


@router.post("/logout", response_model=MessageResponse)
@limiter.limit("30/minute")
def logout(request: Request, payload: LogoutRequest, db: Session = Depends(get_db)):
    """Idempotent; no access-token auth required — logout must work exactly
    when the access token has already expired."""
    decoded = decode_refresh_token(payload.refresh_token or "")
    if decoded:
        uid, jti = decoded
        row = db.query(RefreshToken).filter(
            RefreshToken.jti == jti, RefreshToken.user_id == uid
        ).first()
        if row and not row.revoked:
            row.revoked = True
            db.commit()
            audit(db, action="logout", entity="user", user_id=uid)
    return MessageResponse(message="Выход выполнен")


@router.post("/resend-code", response_model=MessageResponse)
@limiter.limit("5/minute")
def resend_code(request: Request, payload: ResendCodeRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.is_verified:
        raise HTTPException(status_code=400, detail="Email уже подтверждён")
    existing_row = db.query(AuthCode).filter(
        AuthCode.purpose == "verify", AuthCode.key == email
    ).first()
    if existing_row and existing_row.resend_after and datetime.utcnow() < existing_row.resend_after:
        wait = int((existing_row.resend_after - datetime.utcnow()).total_seconds())
        raise HTTPException(
            status_code=429,
            detail=f"Подождите {wait} секунд перед повторной отправкой",
        )
    code = _set_verify_code(db, email)
    send_verification_code(email, code, user.full_name)
    return MessageResponse(message="Новый код отправлен на email")


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
def login(request: Request, payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аккаунт деактивирован",
        )
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email не подтверждён",
        )
    token = create_access_token(user.id)
    refresh = create_refresh_token(user.id, db)
    audit(db, action="login", entity="user", user_id=user.id)
    return Token(access_token=token, refresh_token=refresh, user=UserResponse.model_validate(user))


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("5/minute")
def forgot_password(request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь с таким email не найден")
    code = _generate_code()
    _store_code(db, "reset", email, code, ttl_minutes=RESET_CODE_TTL_MINUTES)
    send_password_reset_code(email, code, user.full_name)
    return MessageResponse(message="Код отправлен на email")


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit("10/minute")
def reset_password(request: Request, payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()
    _check_code(db, "reset", email, payload.code)
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.password_hash = hash_password(payload.new_password)
    # Пароль сменён — убиваем все выданные refresh-сессии пользователя.
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False)
    ).update({RefreshToken.revoked: True})
    db.commit()
    _delete_code(db, "reset", email)
    audit(db, action="password_reset", entity="user", user_id=user.id)
    return MessageResponse(message="Пароль успешно изменён")


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return current_user


AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
AVATAR_ALLOWED_PREFIXES = ("image/jpeg", "image/png", "image/webp")


@router.post("/avatar", response_model=UserResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ct = (file.content_type or "").lower()
    if not any(ct.startswith(p) for p in AVATAR_ALLOWED_PREFIXES):
        raise HTTPException(status_code=400, detail="Допустимы только JPEG, PNG, WEBP")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(data) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой (макс 2 МБ)")
    # Pilot-grade storage: base64 data URI inline in the row
    b64 = base64.b64encode(data).decode("ascii")
    current_user.avatar_url = f"data:{ct};base64,{b64}"
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/profile", response_model=UserResponse)
def update_profile(
    payload: UpdateProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(current_user, k, v)
    # Auto-derive full_name from parts if user supplied first/last but no full_name
    if (payload.first_name or payload.last_name) and not payload.full_name:
        parts = [current_user.last_name, current_user.first_name, current_user.patronymic]
        derived = " ".join([p for p in parts if p])
        if derived:
            current_user.full_name = derived
    # Mark profile complete when last_name + first_name + specialty are filled
    if current_user.last_name and current_user.first_name and current_user.specialty:
        current_user.profile_completed = True
    db.commit()
    db.refresh(current_user)
    return current_user


# ---------- OAuth ----------

def _upsert_oauth_user(db: Session, email: str, full_name: str) -> User:
    email = email.lower()
    user = db.query(User).filter(User.email == email).first()
    if user:
        if not user.is_verified:
            user.is_verified = True
            db.commit()
        return user
    is_first = db.query(User).count() == 0
    user = User(
        email=email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        full_name=full_name or email.split("@")[0],
        is_verified=True,
        is_admin=is_first,
        is_approved=is_first,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    if not user.is_approved:
        try:
            from email_service import send_admin_new_doctor_alert
            admins = db.query(User).filter(User.is_admin.is_(True)).all()
            for adm in admins:
                send_admin_new_doctor_alert(
                    adm.email, user.full_name, user.email, "", "",
                )
        except Exception:
            pass
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return current_user


@router.get("/admin/pending-doctors")
def admin_pending_doctors(current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    items = db.query(User).filter(User.is_approved.is_(False), User.is_active.is_(True)).order_by(User.created_at.desc()).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "specialty": u.specialty,
            "hospital_name": u.hospital_name,
            "department": u.department,
            "license_number": u.license_number,
            "phone": u.phone,
            "is_verified": u.is_verified,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in items
    ]


@router.post("/admin/approve/{user_id}")
def admin_approve(user_id: int, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    target.is_approved = True
    target.rejection_reason = None
    db.commit()
    audit(db, action="approve", entity="user", user_id=current_user.id, entity_id=target.id)
    try:
        from email_service import send_doctor_approved
        send_doctor_approved(target.email, target.full_name)
    except Exception:
        pass
    return {"ok": True, "id": target.id, "is_approved": True}


class _RejectBody(BaseModel):
    reason: Optional[str] = None


@router.post("/admin/reject/{user_id}")
def admin_reject(user_id: int, payload: _RejectBody, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    target.is_approved = False
    target.is_active = False
    target.rejection_reason = payload.reason or ""
    db.commit()
    audit(db, action="reject", entity="user", user_id=current_user.id, entity_id=target.id)
    try:
        from email_service import send_doctor_rejected
        send_doctor_rejected(target.email, target.full_name, payload.reason or "")
    except Exception:
        pass
    return {"ok": True, "id": target.id, "is_approved": False}


def _redirect_with_token(token: str) -> RedirectResponse:
    # The SPA lives at /app now (the root is the marketing waitlist), so OAuth
    # must land the user inside the app where the ?token= handler runs.
    app_url = os.getenv("APP_URL", FRONTEND_URL.rstrip("/") + "/app")
    sep = "&" if "?" in app_url else "?"
    return RedirectResponse(f"{app_url}{sep}token={token}")


@router.get("/google")
def google_login(db: Session = Depends(get_db)):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")
    state = _issue_oauth_state(db)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return RedirectResponse(GOOGLE_AUTH_URL + "?" + urlencode(params))


@router.get("/google/callback")
async def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    if not _consume_oauth_state(db, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    async with httpx.AsyncClient(timeout=10) as client:
        tr = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        if tr.status_code != 200:
            raise HTTPException(status_code=400, detail="Google token exchange failed")
        access_token = tr.json().get("access_token")
        ur = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": "Bearer " + access_token},
        )
        if ur.status_code != 200:
            raise HTTPException(status_code=400, detail="Google userinfo failed")
        info = ur.json()
    email = (info.get("email") or "").lower()
    name = info.get("name") or ""
    if not email:
        raise HTTPException(status_code=400, detail="Google did not return email")
    user = _upsert_oauth_user(db, email, name)
    return _redirect_with_token(create_access_token(user.id))


@router.get("/mailru")
def mailru_login(db: Session = Depends(get_db)):
    if not MAILRU_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Mail.ru OAuth not configured")
    state = _issue_oauth_state(db)
    params = {
        "client_id": MAILRU_CLIENT_ID,
        "redirect_uri": MAILRU_REDIRECT_URI,
        "response_type": "code",
        "scope": "userinfo",
        "state": state,
    }
    return RedirectResponse(MAILRU_AUTH_URL + "?" + urlencode(params))


@router.get("/mailru/callback")
async def mailru_callback(code: str, state: str, db: Session = Depends(get_db)):
    if not _consume_oauth_state(db, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    async with httpx.AsyncClient(timeout=10) as client:
        tr = await client.post(MAILRU_TOKEN_URL, data={
            "code": code,
            "client_id": MAILRU_CLIENT_ID,
            "client_secret": MAILRU_CLIENT_SECRET,
            "redirect_uri": MAILRU_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        if tr.status_code != 200:
            raise HTTPException(status_code=400, detail="Mail.ru token exchange failed")
        access_token = tr.json().get("access_token")
        ur = await client.get(
            MAILRU_USERINFO_URL,
            params={"access_token": access_token},
        )
        if ur.status_code != 200:
            raise HTTPException(status_code=400, detail="Mail.ru userinfo failed")
        info = ur.json()
    email = (info.get("email") or "").lower()
    name = info.get("name") or (info.get("first_name", "") + " " + info.get("last_name", "")).strip()
    if not email:
        raise HTTPException(status_code=400, detail="Mail.ru did not return email")
    user = _upsert_oauth_user(db, email, name)
    return _redirect_with_token(create_access_token(user.id))
