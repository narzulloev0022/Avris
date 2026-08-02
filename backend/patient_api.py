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

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from audit import audit
from database import get_db
from models import (AssistantUsage, IntakeUsage, PatientAccount, PatientAvatar,
                    PatientConversation, PatientLink, PatientLinkCode, PatientMessage,
                    PatientPreVisitNote, PatientRefreshToken, VisitSummary)
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
    # Штамп фото профиля: None — фото нет. Само фото отдаётся отдельным
    # запросом, чтобы профиль оставался лёгким.
    avatar_updated_at: Optional[datetime] = None


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


# ---------- фото профиля ----------

# Клиент ужимает фото перед отправкой; лимит здесь — предохранитель от
# «сырых» 12-мегапиксельных снимков, а не рабочий размер.
MAX_AVATAR_BYTES = 2 * 1024 * 1024
ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/heic", "image/webp"}


@router.put("/profile/avatar", response_model=PatientProfileOut)
@limiter.limit("10/minute")
async def set_avatar(
    request: Request,
    file: UploadFile = File(...),
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Загрузить или заменить фото профиля."""
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(status_code=415, detail="Поддерживаются только фотографии")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Фото слишком большое")

    row = current.avatar
    if row is None:
        row = PatientAvatar(patient_id=current.id)
        db.add(row)
    row.content_type = "image/jpeg" if content_type == "image/jpg" else content_type
    row.size_bytes = len(data)
    row.data = data
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(current)
    # PHI-free: только факт и размер, никогда не содержимое.
    audit(db, action="update", entity="patient_avatar", user_id=None,
          entity_id=current.id, meta={"door": "patient", "size": len(data)})
    return PatientProfileOut.model_validate(current)


@router.get("/profile/avatar")
def get_avatar(current: PatientAccount = Depends(get_current_patient)):
    """Отдать фото. 404 — фото нет; клиент рисует заглушку."""
    row = current.avatar
    if row is None:
        raise HTTPException(status_code=404, detail="Фото не загружено")
    return Response(
        content=row.data,
        media_type=row.content_type,
        headers={
            # Медданные не должны оседать в общих кэшах; ETag по штампу
            # позволяет клиенту переспрашивать дёшево.
            "Cache-Control": "private, max-age=0, must-revalidate",
            "ETag": f'"{int(row.updated_at.timestamp())}"',
        },
    )


@router.delete("/profile/avatar", response_model=PatientProfileOut)
@limiter.limit("10/minute")
def delete_avatar(
    request: Request,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Убрать фото. Идемпотентно: удалять нечего — это не ошибка."""
    if current.avatar is not None:
        db.delete(current.avatar)
        db.commit()
        db.refresh(current)
        audit(db, action="delete", entity="patient_avatar", user_id=None,
              entity_id=current.id, meta={"door": "patient"})
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


@router.post("/consent/revoke", response_model=PatientProfileOut)
@limiter.limit("5/minute")
def revoke_consent(
    request: Request,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Отозвать согласие на доступ врачей.

    Обнулить один флаг мало: связи с врачами, созданные РАНЬШЕ, продолжали бы
    работать, и «отзыв» не отзывал бы ничего. Поэтому связи разрываются здесь
    же — после отзыва ни один врач не видит профиль, визиты и анализы, пока
    пациент не свяжется заново.

    Сами приёмы и анализы у врача остаются: это его медицинская документация
    о состоявшемся приёме, стирать её пациент не вправе.

    Связь гасится, а не удаляется: удалённая строка означала, что повторная
    привязка заведёт врачу ВТОРУЮ карточку того же человека и расщепит историю
    приёмов надвое. Погашенная связь невидима для всех читающих путей и
    оживает на прежней карточке, если пациент вернёт согласие.
    """
    now = datetime.utcnow()
    links = db.query(PatientLink).filter(
        PatientLink.patient_account_id == current.id,
        PatientLink.revoked_at.is_(None),
    ).all()
    removed = len(links)
    for link in links:
        link.revoked_at = now
    current.consent_doctors_at = None
    current.consent_version = None
    db.commit()
    db.refresh(current)
    audit(db, action="consent_revoke", entity="patient_account", user_id=None,
          entity_id=current.id, meta={"door": "patient", "links_removed": removed})
    return PatientProfileOut.model_validate(current)


@router.delete("/account", status_code=204)
@limiter.limit("3/minute")
def delete_account(
    request: Request,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Удалить аккаунт пациента со всем, что к нему привязано.

    Уходит каскадом: связи с врачами, разговоры с AI и их сообщения,
    пред-визитные заметки, сводки визитов, счётчики. Восстановления нет —
    именно это и обещает экран.

    Что НЕ удаляется: записи приёмов и анализы в кабинете врача. Это его
    медицинская документация о состоявшемся приёме; пациент вправе закрыть к
    ней доступ (отзыв согласия), но не стереть её у врача.
    """
    account_id = current.id

    # Удаляем детей руками, а не полагаемся на ondelete="CASCADE".
    #
    # Каскад в FK — это каскад БАЗЫ, а SQLite по умолчанию внешние ключи вовсе
    # не проверяет (PRAGMA foreign_keys = 0). Проверено: после db.delete(account)
    # пред-визитная заметка оставалась в таблице. То есть экран обещал «стереть
    # всё без возможности восстановления», а разговоры с AI, заметки и сводки
    # визитов оставались лежать. Явные удаления работают на любой базе.
    conversation_ids = [
        cid for (cid,) in db.query(PatientConversation.id)
        .filter(PatientConversation.patient_account_id == account_id)
    ]
    if conversation_ids:
        db.query(PatientMessage).filter(
            PatientMessage.conversation_id.in_(conversation_ids)
        ).delete(synchronize_session=False)
    for model in (PatientConversation, PatientPreVisitNote, VisitSummary, PatientLink,
                  PatientLinkCode, PatientRefreshToken, AssistantUsage, IntakeUsage):
        db.query(model).filter(model.patient_account_id == account_id).delete(
            synchronize_session=False)

    db.delete(current)
    db.commit()
    audit(db, action="delete", entity="patient_account", user_id=None,
          entity_id=account_id, meta={"door": "patient"})
    return Response(status_code=204)


# ---------- pre-visit note ----------

class PreVisitNoteBody(BaseModel):
    note_text: str = Field(min_length=1, max_length=1000)

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


class PreVisitNoteStateOut(BaseModel):
    """Активная заметка или её отсутствие. Пациент пишет заметку дома, а
    открывает приложение снова уже в клинике — он должен видеть, что именно
    написал и дошло ли это до врача."""
    note: Optional[PreVisitNoteOut] = None


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


@router.get("/pre-visit-note", response_model=PreVisitNoteStateOut)
def get_pre_visit_note(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Активная (ещё не показанная врачу) заметка пациента.

    Прочитанная врачом заметка сюда не попадает: она уже сыграла свою роль и
    стала историей, а пациенту важно знать, что перед СЛЕДУЮЩИМ приёмом поле
    снова пустое.
    """
    note = db.query(PatientPreVisitNote).filter(
        PatientPreVisitNote.patient_account_id == current.id,
        PatientPreVisitNote.seen_at.is_(None),
    ).first()
    if note is None:
        return PreVisitNoteStateOut()
    return PreVisitNoteStateOut(
        note=PreVisitNoteOut(note_text=note.note_text, created_at=note.created_at))


@router.delete("/pre-visit-note", status_code=204)
def delete_pre_visit_note(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Передумал — заметка не уйдёт врачу. Удаляем только активную: то, что
    врач уже прочитал, стереть нельзя, это часть приёма."""
    note = db.query(PatientPreVisitNote).filter(
        PatientPreVisitNote.patient_account_id == current.id,
        PatientPreVisitNote.seen_at.is_(None),
    ).first()
    if note is not None:
        db.delete(note)
        db.commit()
        audit(db, action="delete", entity="patient_previsit_note", user_id=None,
              entity_id=note.id, meta={"door": "patient"})
    return Response(status_code=204)
