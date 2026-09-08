"""Согласия пациента, зафиксированные врачом.

Форма в кабинете врача раньше ничего не сохраняла: показывала «Сохранено»
и закрывалась. Здесь она получает настоящее хранилище.

Два правила, ради которых модуль вообще существует отдельно:

1. Виды согласия раздельны. Запись приёма и использование данных для
   обучения моделей — разные решения пациента.
2. Повтор не создаёт дубликат. Врач нажимает «Сохранить», ответ теряется
   в сети, врач нажимает снова — вернуться должна та же запись.
"""
import hashlib
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from access import require_own_patient
from audit import audit
from auth import get_current_user
from database import get_db
from models import Consent, User
from rate_limit import limiter

router = APIRouter(prefix="/api/consents", tags=["consents"])

KINDS = ("recording", "training")


class ConsentIn(BaseModel):
    patient_id: int
    kind: str = Field(pattern="^(recording|training)$")
    granted: bool = True
    text_version: Optional[str] = Field(default=None, max_length=16)
    # Текст, который врач показал пациенту. Сам текст не храним — храним его
    # отпечаток: формулировка меняется, а доказывать придётся согласие
    # на ту, что была на экране в тот день.
    text: Optional[str] = None
    client_id: Optional[str] = Field(default=None, max_length=64)


class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    kind: str
    granted: bool
    text_version: Optional[str] = None
    text_hash: Optional[str] = None
    created_at: datetime


def _hash(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


@router.post("", response_model=ConsentOut, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=ConsentOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
def create_consent(
    request: Request,
    payload: ConsentIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_own_patient(db, payload.patient_id, current_user)

    if payload.client_id:
        # Идемпотентность по ключу врача: тот же ключ — та же запись.
        # Ключ проверяем вместе с doctor_id, чтобы чужой идентификатор
        # не мог указать на чужое согласие.
        existing = (
            db.query(Consent)
            .filter(Consent.doctor_id == current_user.id,
                    Consent.client_id == payload.client_id)
            .first()
        )
        if existing:
            return existing

    c = Consent(
        patient_id=payload.patient_id,
        doctor_id=current_user.id,
        kind=payload.kind,
        granted=payload.granted,
        text_version=payload.text_version,
        text_hash=_hash(payload.text),
        client_id=payload.client_id,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    audit(db, action="create", entity="consent", user_id=current_user.id,
          entity_id=c.id, meta={"patient_id": c.patient_id, "kind": c.kind,
                                "granted": c.granted})
    return c


@router.get("", response_model=List[ConsentOut])
@router.get("/", response_model=List[ConsentOut])
def list_consents(
    patient_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Согласия по карте — свежие сверху.

    Отзыв согласия — это новая запись с granted=false, а не правка старой:
    история решений пациента должна читаться целиком.
    """
    require_own_patient(db, patient_id, current_user)
    return (
        db.query(Consent)
        .filter(Consent.patient_id == patient_id, Consent.doctor_id == current_user.id)
        .order_by(Consent.created_at.desc(), Consent.id.desc())
        .all()
    )
