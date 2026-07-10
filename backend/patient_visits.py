"""Post-visit summaries — the patient-readable retelling of the SOAP note.

Generation runs as a best-effort background task right after the doctor saves
a consultation (never live during the demo — the app only ever reads cached
rows). A failure or a missing ANTHROPIC_API_KEY must never break the doctor's
save; the visit just stays "pending" in the app.

Safety net: a summary that slips into diagnosis-speak ("вероятно, у вас…")
is dropped, not shown. The prompt bans it, the filter enforces it — a patient
must never receive a probabilistic diagnosis in a push notification.
"""
import logging
import os
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from llm import _claude_call
from models import Consultation, PatientAccount, PatientLink, User, VisitSummary
from patient_auth import get_current_patient

log = logging.getLogger("avris.patient_visits")

router = APIRouter(prefix="/api/patient/visits", tags=["patient"])

# Probabilistic-diagnosis phrasing that must never reach a patient.
_FORBIDDEN = re.compile(
    r"(вероятно,?\s+у\s+вас|у\s+вас,?\s+вероятно|скорее\s+всего,?\s+у\s+вас"
    r"|похоже,?\s+у\s+вас|можно\s+предположить,?\s+что\s+у\s+вас)",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = """Ты — помощник клиники, который пересказывает пациенту итог его визита к врачу.
Тебе дают SOAP-заметку врача. Напиши короткое (4-7 предложений) тёплое резюме для пациента простым языком:
1) что происходило на приёме, 2) что назначил врач и зачем, 3) что делать дальше и когда прийти снова.

Жёсткие правила:
- НИКОГДА не формулируй вероятностные диагнозы («вероятно, у вас…», «скорее всего…», «похоже, у вас…»). Диагноз ставит только врач.
- Не добавляй ничего, чего нет в заметке врача. Не давай собственных медицинских советов.
- Не используй медицинский жаргон; если термин неизбежен — поясни в скобках.
- Пиши на языке пациента (указан в запросе). Обращайся на «вы».
- Ответ — только текст резюме, без заголовков и преамбул."""


async def generate_visit_summary(consultation_id: int) -> None:
    """Background task: own DB session, swallow-and-log all failures."""
    db = SessionLocal()
    try:
        consultation = db.query(Consultation).filter(
            Consultation.id == consultation_id
        ).first()
        if not consultation or consultation.patient_id is None:
            return
        link = db.query(PatientLink).filter(
            PatientLink.patient_id == consultation.patient_id
        ).first()
        if not link:
            return  # обычный пациент кабинета, без приложения
        if db.query(VisitSummary).filter(
            VisitSummary.consultation_id == consultation_id
        ).first():
            return  # уже есть

        account = db.query(PatientAccount).filter(
            PatientAccount.id == link.patient_account_id
        ).first()
        if not account:
            return
        lang = account.language_pref or "ru"
        lang_name = {"ru": "русский", "tj": "таджикский", "en": "английский"}.get(lang, "русский")

        soap = "\n".join(
            f"{label}: {value}" for label, value in [
                ("S (жалобы)", consultation.soap_s),
                ("O (осмотр)", consultation.soap_o),
                ("A (оценка врача)", consultation.soap_a),
                ("P (план лечения)", consultation.soap_p),
            ] if value
        )
        if not soap:
            return

        text = await _claude_call(
            _SYSTEM_PROMPT,
            f"Язык пациента: {lang_name}.\n\nSOAP-заметка врача:\n{soap}",
            max_tokens=600,
        )
        text = (text or "").strip()
        if not text:
            return
        if _FORBIDDEN.search(text):
            log.warning("visit summary %s dropped: forbidden phrasing", consultation_id)
            return

        db.add(VisitSummary(
            consultation_id=consultation_id,
            patient_account_id=account.id,
            summary=text,
            language=lang,
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        ))
        db.commit()
    except Exception:
        log.exception("visit summary generation failed for consultation %s", consultation_id)
    finally:
        db.close()


# ---------- patient-facing endpoints ----------

class VisitListItem(BaseModel):
    consultation_id: int
    date: datetime
    doctor_name: Optional[str] = None
    summary_status: str  # ready|pending


class VisitDetailOut(BaseModel):
    consultation_id: int
    date: datetime
    doctor_name: Optional[str] = None
    summary: Optional[str] = None
    summary_status: str


def _linked_patient_ids(db: Session, account_id: int) -> List[int]:
    return [l.patient_id for l in db.query(PatientLink).filter(
        PatientLink.patient_account_id == account_id
    ).all()]


@router.get("", response_model=List[VisitListItem])
def list_visits(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    patient_ids = _linked_patient_ids(db, current.id)
    if not patient_ids:
        return []
    rows = (
        db.query(Consultation, User.full_name, VisitSummary.id)
        .join(User, User.id == Consultation.doctor_id)
        .outerjoin(VisitSummary, VisitSummary.consultation_id == Consultation.id)
        .filter(Consultation.patient_id.in_(patient_ids))
        .order_by(Consultation.created_at.desc())
        .all()
    )
    return [
        VisitListItem(
            consultation_id=c.id,
            date=c.created_at,
            doctor_name=doctor_name,
            summary_status="ready" if summary_id else "pending",
        )
        for c, doctor_name, summary_id in rows
    ]


@router.get("/{consultation_id}", response_model=VisitDetailOut)
def visit_detail(
    consultation_id: int,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    patient_ids = _linked_patient_ids(db, current.id)
    consultation = db.query(Consultation).filter(
        Consultation.id == consultation_id,
        Consultation.patient_id.in_(patient_ids) if patient_ids else False,
    ).first()
    if not consultation:
        raise HTTPException(status_code=404, detail="Визит не найден")
    doctor = db.query(User).filter(User.id == consultation.doctor_id).first()
    summary = db.query(VisitSummary).filter(
        VisitSummary.consultation_id == consultation_id
    ).first()
    return VisitDetailOut(
        consultation_id=consultation.id,
        date=consultation.created_at,
        doctor_name=doctor.full_name if doctor else None,
        summary=summary.summary if summary else None,
        summary_status="ready" if summary else "pending",
    )
