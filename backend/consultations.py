from datetime import datetime
from io import BytesIO
from typing import Optional, List
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit import audit
from database import get_db
from access import require_own_patient
from models import Consent, Consultation, ConsultationVersion, Patient, User
from auth import get_current_user
from pdf_export import render_consultation_pdf

router = APIRouter(prefix="/api/consultations", tags=["consultations"])


class ConsultationCreate(BaseModel):
    patient_id: Optional[int] = None
    transcript: Optional[str] = None
    soap_s: Optional[str] = None
    soap_o: Optional[str] = None
    soap_a: Optional[str] = None
    soap_p: Optional[str] = None
    language: str = "ru"
    duration_seconds: Optional[int] = None
    # visit — амбулаторный приём (default), primary — первичный осмотр при
    # поступлении, daily — ежедневный дневник стационара.
    visit_type: str = "visit"
    # Accuracy tracking. Set by the frontend at save time:
    #   None  → SOAP wasn't AI-generated (manual entry) — don't count
    #   False → AI-generated, doctor saved without edits → accurate
    #   True  → AI-generated, doctor edited at least one field → edited
    soap_was_edited: Optional[bool] = None
    # Ключ, выданный клиентом в начале осмотра: делает повтор безопасным.
    client_id: Optional[str] = Field(default=None, max_length=64)
    # draft — врач ещё не заверил запись. Черновик существует, чтобы работа
    # не терялась, но документом не является и никуда, кроме рабочего стола
    # самого врача, не попадает.
    status: str = Field(default="confirmed", pattern="^(draft|confirmed)$")


class ConsultationPatch(BaseModel):
    """Правка записи. Пустые поля не трогаются."""
    transcript: Optional[str] = None
    soap_s: Optional[str] = None
    soap_o: Optional[str] = None
    soap_a: Optional[str] = None
    soap_p: Optional[str] = None
    visit_type: Optional[str] = None
    # confirmed — заверить черновик; обратно, из документа в черновик,
    # дороги нет: подпись не отзывают, её исправляют новой записью.
    status: Optional[str] = Field(default=None, pattern="^(confirmed)$")


class ConsultationVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    no: int
    soap_s: Optional[str] = None
    soap_o: Optional[str] = None
    soap_a: Optional[str] = None
    soap_p: Optional[str] = None
    created_at: datetime


class ConsultationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: Optional[int] = None
    doctor_id: int
    transcript: Optional[str] = None
    soap_s: Optional[str] = None
    soap_o: Optional[str] = None
    soap_a: Optional[str] = None
    soap_p: Optional[str] = None
    language: str
    duration_seconds: Optional[int] = None
    visit_type: str = "visit"
    client_id: Optional[str] = None
    status: str = "confirmed"
    confirmed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Сколько раз запись правили после подтверждения. Ноль — документ таков,
    # каким его заверили.
    revisions: int = 0
    created_at: datetime


def _find_twin(db: Session, doctor_id: int, client_id: str):
    """Осмотр, уже созданный под этим ключом у этого врача."""
    return (
        db.query(Consultation)
        .filter(Consultation.doctor_id == doctor_id,
                Consultation.client_id == client_id)
        .first()
    )


@router.post("/", response_model=ConsultationResponse, status_code=status.HTTP_201_CREATED)
def create_consultation(
    payload: ConsultationCreate,
    background_tasks: BackgroundTasks,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    data = payload.model_dump()
    was_edited = data.pop("soap_was_edited", None)
    require_own_patient(db, data.get("patient_id"), current_user)

    # Повтор после потерянного ответа обязан вернуть уже созданную запись.
    # Одного идентификатора на клиенте мало: без этой проверки вторая
    # отправка заводила второй осмотр в карте, и врач его не видел.
    cid = data.get("client_id")
    if cid:
        existing = _find_twin(db, current_user.id, cid)
        if existing:
            response.status_code = status.HTTP_200_OK
            return existing

    is_draft = data.get("status") == "draft"
    c = Consultation(doctor_id=current_user.id, **data)
    if not is_draft:
        c.confirmed_at = datetime.utcnow()
    db.add(c)
    # Bump accuracy counters only when the frontend explicitly tagged this save.
    # None means the SOAP was hand-typed without LLM — irrelevant to accuracy.
    # Черновик не считаем: точность измеряется по тому, что врач заверил,
    # а не по тому, что он ещё правит.
    if not is_draft and was_edited is True:
        current_user.soap_edited_count = (current_user.soap_edited_count or 0) + 1
    elif not is_draft and was_edited is False:
        current_user.soap_accurate_count = (current_user.soap_accurate_count or 0) + 1
    try:
        db.commit()
    except IntegrityError:
        # Две отправки, ушедшие одновременно: обе не нашли записи и обе пишут.
        # Уникальный индекс останавливает вторую — и это успех, а не сбой:
        # осмотр в карте уже есть, врачу надо вернуть его, а не ошибку 500.
        db.rollback()
        twin = _find_twin(db, current_user.id, cid)
        if twin is None:
            raise
        response.status_code = status.HTTP_200_OK
        return twin
    db.refresh(c)
    audit(db, action="create", entity="consultation", user_id=current_user.id,
          entity_id=c.id, meta={"patient_id": c.patient_id, "language": c.language})
    # Patient app: pre-generate the patient-readable summary in the background.
    # Best-effort by design — a LLM failure never breaks the doctor's save.
    # Для черновика не готовим ничего: пациент не должен увидеть текст,
    # под которым врач ещё не подписался.
    if not is_draft:
        from patient_visits import generate_visit_summary
        background_tasks.add_task(generate_visit_summary, c.id)
    return c


@router.get("/", response_model=List[ConsultationResponse])
def list_consultations(
    response: Response,
    patient_id: Optional[int] = None,
    limit: Optional[int] = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Consultation).filter(Consultation.doctor_id == current_user.id)
    if patient_id is not None:
        q = q.filter(Consultation.patient_id == patient_id)
    response.headers["X-Total-Count"] = str(q.count())
    q = q.order_by(Consultation.created_at.desc()).offset(offset)
    if limit is not None:
        q = q.limit(limit)
    return q.all()


@router.get("/{cid}", response_model=ConsultationResponse)
def get_consultation(
    cid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Consultation).filter(Consultation.id == cid).first()
    if not c:
        raise HTTPException(status_code=404, detail="Консультация не найдена")
    if c.doctor_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой консультации")
    return c


def _recording_consent_at(db: Session, c: Consultation, doctor_id: int):
    """Дата согласия на запись, действовавшего на момент этого приёма.

    Позднее согласие не задним числом узаконивает прошлую запись, поэтому
    берётся последнее, данное не позже самого приёма.
    """
    if not c.patient_id:
        return None
    row = (
        db.query(Consent)
        .filter(Consent.patient_id == c.patient_id,
                Consent.doctor_id == doctor_id,
                Consent.kind == "recording",
                Consent.granted.is_(True),
                Consent.created_at <= c.created_at)
        .order_by(Consent.created_at.desc())
        .first()
    )
    return row.created_at if row else None


def _own_consultation(db: Session, cid: int, user: User) -> Consultation:
    c = db.query(Consultation).filter(Consultation.id == cid,
                                      Consultation.doctor_id == user.id).first()
    if not c:
        # 404, а не 403: иначе перебором id можно узнать, что запись есть.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Консультация не найдена")
    return c


_TEXT_FIELDS = ("soap_s", "soap_o", "soap_a", "soap_p")


@router.patch("/{cid}", response_model=ConsultationResponse)
def update_consultation(
    cid: int,
    payload: ConsultationPatch,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Правка записи и подтверждение черновика.

    Правка заверенного документа не стирает прежний текст: он уходит в
    версии. В карте не затирают — исправление оговаривают, а написанное
    вчера остаётся читаемым.
    """
    c = _own_consultation(db, cid, current_user)
    data = payload.model_dump(exclude_unset=True)
    new_status = data.pop("status", None)

    changed = {k: v for k, v in data.items()
               if v is not None and getattr(c, k) != v}
    text_changed = any(k in _TEXT_FIELDS for k in changed)

    # Снимок делаем ДО правки и только для заверенного документа: у черновика
    # ещё нет содержания, за которое кто-то поручился.
    if text_changed and c.status == "confirmed":
        db.add(ConsultationVersion(
            consultation_id=c.id,
            no=(db.query(func.count(ConsultationVersion.id))
                .filter(ConsultationVersion.consultation_id == c.id).scalar() or 0) + 1,
            soap_s=c.soap_s, soap_o=c.soap_o, soap_a=c.soap_a, soap_p=c.soap_p,
            author_id=current_user.id,
        ))

    for k, v in changed.items():
        setattr(c, k, v)
    if changed:
        c.updated_at = datetime.utcnow()

    just_confirmed = False
    if new_status == "confirmed" and c.status != "confirmed":
        c.status = "confirmed"
        c.confirmed_at = datetime.utcnow()
        just_confirmed = True

    db.commit()
    db.refresh(c)
    audit(db, action="update", entity="consultation", user_id=current_user.id,
          entity_id=c.id, meta={"fields": sorted(changed), "confirmed": just_confirmed})

    # Пациент видит запись только после подписи врача — и сводку для него
    # начинаем готовить в тот же момент, не раньше.
    if just_confirmed and c.patient_id:
        from patient_visits import generate_visit_summary
        background_tasks.add_task(generate_visit_summary, c.id)
    return c


@router.get("/{cid}/versions", response_model=List[ConsultationVersionOut])
def consultation_versions(
    cid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Что стояло в записи до каждой правки, от старого к новому."""
    c = _own_consultation(db, cid, current_user)
    return (db.query(ConsultationVersion)
            .filter(ConsultationVersion.consultation_id == c.id)
            .order_by(ConsultationVersion.no.asc()).all())


@router.get("/{cid}/pdf")
def consultation_pdf(
    cid: int,
    transcript: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Официальная копия — по умолчанию, без дословной речи.

    `transcript=1` отдаёт ту же запись с рабочим приложением: врачу
    расшифровка нужна, медицинской карте — нет.
    """
    c = db.query(Consultation).filter(Consultation.id == cid).first()
    if not c:
        raise HTTPException(status_code=404, detail="Консультация не найдена")
    if c.doctor_id != current_user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой консультации")
    patient = None
    if c.patient_id:
        patient = db.query(Patient).filter(Patient.id == c.patient_id).first()
    pdf_bytes = render_consultation_pdf(
        c, patient, current_user,
        with_transcript=transcript,
        consent_date=_recording_consent_at(db, c, current_user.id),
    )
    fname = f"avris-consultation-{cid}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
