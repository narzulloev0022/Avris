from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    full_name: Optional[str] = None
    specialty: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    patronymic: Optional[str] = None
    date_of_birth: Optional[date] = None
    phone: Optional[str] = None
    specialty: Optional[str] = None
    hospital_name: Optional[str] = None
    hospital_address: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    experience_years: Optional[int] = None
    license_number: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str
    language_pref: str
    theme_pref: str
    is_active: bool
    is_verified: bool
    profile_completed: bool
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class RegisterResponse(BaseModel):
    message: str
    requires_verification: bool = True
    email: EmailStr


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=8)


class ResendCodeRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str = Field(min_length=6)


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    patronymic: Optional[str] = None
    date_of_birth: Optional[date] = None
    phone: Optional[str] = None
    specialty: Optional[str] = None
    hospital_name: Optional[str] = None
    hospital_address: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    experience_years: Optional[int] = None
    license_number: Optional[str] = None
    avatar_url: Optional[str] = None
    language_pref: Optional[str] = None
    theme_pref: Optional[str] = None


class MessageResponse(BaseModel):
    message: str
