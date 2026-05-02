import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from database import get_db
from models import User
from schemas import (
    UserCreate, UserLogin, UserResponse, Token,
    ForgotPasswordRequest, ResetPasswordRequest, MessageResponse,
)
from email_service import send_password_reset_code

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
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

APPLE_CLIENT_ID = os.getenv("APPLE_CLIENT_ID", "")
APPLE_TEAM_ID = os.getenv("APPLE_TEAM_ID", "")
APPLE_KEY_ID = os.getenv("APPLE_KEY_ID", "")
APPLE_PRIVATE_KEY = os.getenv("APPLE_PRIVATE_KEY", "").replace("\\n", "\n")
APPLE_REDIRECT_URI = os.getenv("APPLE_REDIRECT_URI", "http://localhost:8000/api/auth/apple/callback")
APPLE_AUTH_URL = "https://appleid.apple.com/auth/authorize"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"

_oauth_states: set[str] = set()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# In-memory store for password reset codes: { email: (code, expires_at) }
_reset_codes: dict[str, tuple[str, datetime]] = {}

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------- helpers ----------

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_access_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (JWTError, ValueError):
        return None


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

@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email.lower()).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким email уже существует",
        )
    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        specialty=payload.specialty,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id)
    return Token(access_token=token, user=UserResponse.model_validate(user))


@router.post("/login", response_model=Token)
def login(payload: UserLogin, db: Session = Depends(get_db)):
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
    token = create_access_token(user.id)
    return Token(access_token=token, user=UserResponse.model_validate(user))


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()
    user = db.query(User).filter(User.email == email).first()
    # Always return same message to avoid leaking which emails are registered
    generic_msg = MessageResponse(message="Если email зарегистрирован, код отправлен на почту")
    if not user:
        return generic_msg
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.utcnow() + timedelta(minutes=RESET_CODE_TTL_MINUTES)
    _reset_codes[email] = (code, expires_at)
    send_password_reset_code(email, code, user.full_name)
    return generic_msg


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()
    entry = _reset_codes.get(email)
    if not entry:
        raise HTTPException(status_code=400, detail="Код не найден или устарел")
    code, expires_at = entry
    if datetime.utcnow() > expires_at:
        _reset_codes.pop(email, None)
        raise HTTPException(status_code=400, detail="Код истёк, запросите новый")
    if not secrets.compare_digest(code, payload.code):
        raise HTTPException(status_code=400, detail="Неверный код")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    _reset_codes.pop(email, None)
    return MessageResponse(message="Пароль успешно изменён")


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
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
    user = User(
        email=email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        full_name=full_name or email.split("@")[0],
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _redirect_with_token(token: str) -> RedirectResponse:
    sep = "&" if "?" in FRONTEND_URL else "?"
    return RedirectResponse(f"{FRONTEND_URL}{sep}token={token}")


@router.get("/google")
def google_login():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")
    state = secrets.token_urlsafe(16)
    _oauth_states.add(state)
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
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    _oauth_states.discard(state)
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


def _apple_client_secret() -> str:
    """Apple requires a JWT signed ES256 with the .p8 private key as client_secret."""
    if not (APPLE_TEAM_ID and APPLE_KEY_ID and APPLE_PRIVATE_KEY and APPLE_CLIENT_ID):
        raise HTTPException(status_code=503, detail="Apple OAuth not fully configured")
    now = int(datetime.utcnow().timestamp())
    payload = {
        "iss": APPLE_TEAM_ID,
        "iat": now,
        "exp": now + 60 * 60,
        "aud": "https://appleid.apple.com",
        "sub": APPLE_CLIENT_ID,
    }
    return jwt.encode(
        payload,
        APPLE_PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": APPLE_KEY_ID, "alg": "ES256"},
    )


@router.get("/apple")
def apple_login():
    if not APPLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Apple OAuth not configured")
    state = secrets.token_urlsafe(16)
    _oauth_states.add(state)
    params = {
        "client_id": APPLE_CLIENT_ID,
        "redirect_uri": APPLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "name email",
        "response_mode": "form_post",
        "state": state,
    }
    return RedirectResponse(APPLE_AUTH_URL + "?" + urlencode(params))


@router.api_route("/apple/callback", methods=["GET", "POST"])
async def apple_callback(request: Request, db: Session = Depends(get_db)):
    # Apple sends form_post on success when scope=email is requested
    if request.method == "POST":
        form = await request.form()
        code = form.get("code")
        state = form.get("state")
    else:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
    if not code or not state or state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid Apple OAuth callback")
    _oauth_states.discard(state)

    client_secret = _apple_client_secret()
    async with httpx.AsyncClient(timeout=10) as client:
        tr = await client.post(APPLE_TOKEN_URL, data={
            "code": code,
            "client_id": APPLE_CLIENT_ID,
            "client_secret": client_secret,
            "redirect_uri": APPLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        if tr.status_code != 200:
            raise HTTPException(status_code=400, detail="Apple token exchange failed")
        token_data = tr.json()
    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="Apple did not return id_token")
    # We just received id_token directly from Apple over TLS — decode without re-verifying signature
    claims = jwt.decode(id_token, options={"verify_signature": False, "verify_aud": False})
    email = (claims.get("email") or "").lower()
    if not email:
        raise HTTPException(status_code=400, detail="Apple did not return email (revoke and re-auth)")
    name = email.split("@")[0]
    user = _upsert_oauth_user(db, email, name)
    return _redirect_with_token(create_access_token(user.id))


@router.get("/mailru")
def mailru_login():
    if not MAILRU_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Mail.ru OAuth not configured")
    state = secrets.token_urlsafe(16)
    _oauth_states.add(state)
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
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    _oauth_states.discard(state)
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
