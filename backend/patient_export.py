"""Выгрузка медкарты пациента одним PDF — платная фича Plus.

Тариф обещает «Экспорт медкарты в PDF», и до этого модуля обещание было
пустым. Собираем то, что пациент уже видит в приложении: профиль, приёмы с
человеческими сводками и назначениями, анализы с результатами. Сырой SOAP
сюда не попадает — это выгрузка для пациента, не врачебная форма.

Доступ строго через PatientLink (как в patient_visits/patient_labs): в PDF
попадают только те приёмы и анализы, которые пациент и так вправе читать.
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

import pdf_export
from database import get_db
from models import Consultation, LabOrder, PatientAccount, User, VisitSummary
from patient_auth import get_current_patient
from patient_subscription import FEATURE_PDF_EXPORT, require_feature
from patient_visits import _linked_patient_ids
from rate_limit import limiter

router = APIRouter(prefix="/api/patient/export", tags=["patient"])


def _translit(text: str) -> str:
    """ASCII-имя файла: Content-Disposition с кириллицей ломает часть клиентов."""
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
        "ғ": "gh", "ӣ": "i", "қ": "q", "ӯ": "u", "ҳ": "h", "ҷ": "j",
    }
    out = []
    for ch in (text or "").lower():
        if ch.isascii() and (ch.isalnum() or ch in "-_"):
            out.append(ch)
        elif ch in table:
            out.append(table[ch])
        elif ch.isspace():
            out.append("-")
    return "".join(out).strip("-") or "patient"


@router.get("/record.pdf")
@limiter.limit("6/minute")
def export_medical_record(
    request: Request,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Медкарта пациента одним PDF (Plus+). Лимит частоты — сборка не бесплатна
    по CPU, а нажать «поделиться» легко несколько раз подряд."""
    require_feature(current, FEATURE_PDF_EXPORT)

    patient_ids = _linked_patient_ids(db, current.id)

    visits: List[tuple] = []
    labs: List[tuple] = []
    if patient_ids:
        visits = (
            db.query(Consultation, User.full_name, VisitSummary)
            .join(User, User.id == Consultation.doctor_id)
            .outerjoin(VisitSummary, VisitSummary.consultation_id == Consultation.id)
            .filter(Consultation.patient_id.in_(patient_ids))
            .order_by(Consultation.created_at.desc())
            .all()
        )
        labs = (
            db.query(LabOrder, User.full_name)
            .join(User, User.id == LabOrder.doctor_id)
            .filter(LabOrder.patient_id.in_(patient_ids))
            .order_by(LabOrder.created_at.desc())
            .all()
        )

    pdf = pdf_export.render_patient_record_pdf(current, visits, labs)
    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    name = f"avris-{_translit(current.full_name or 'patient')}-{stamp}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
