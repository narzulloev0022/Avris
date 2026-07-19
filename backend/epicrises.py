"""Эпикризы — этапный (interim) и выписной (discharge).

Черновик генерирует Claude из ВСЕЙ истории болезни пациента: снимок
поступления + консультации (первичный осмотр / дневники / приёмы, SOAP) +
ночные обходы + полученные результаты анализов. Врач редактирует черновик
и сохраняет финальный текст; каждое сохранение — новая запись (версии).
Сохранённый эпикриз выгружается PDF-документом.
"""
import json
from datetime import datetime
from io import BytesIO
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from audit import audit
from database import get_db
from llm import _claude_call
from models import Consultation, Epicrisis, LabOrder, NightRound, Patient, User
from auth import get_current_user
from pdf_export import render_epicrisis_pdf

router = APIRouter(prefix="/api/epicrises", tags=["epicrises"])

KINDS = ("interim", "discharge")

# Защита контекста: история может быть длинной. Берём первичный осмотр +
# последние записи каждого типа; о пропущенном честно сообщаем модели.
MAX_CONSULTS = 15
MAX_ROUNDS = 15
MAX_LABS = 10
MAX_CHARS = 30000


# ---------- Schemas ----------

class EpicrisisDraftRequest(BaseModel):
    patient_id: int
    kind: str = "discharge"  # interim|discharge
    language: str = "ru"


class EpicrisisDraftResponse(BaseModel):
    draft: str
    counts: dict


class EpicrisisCreate(BaseModel):
    patient_id: int
    kind: str = "discharge"
    body: str = Field(min_length=1)
    language: str = "ru"


class EpicrisisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    doctor_id: int
    kind: str
    body: str
    language: str
    created_at: datetime


# ---------- Helpers ----------

def _owned_patient(db: Session, pid: int, user: User) -> Patient:
    p = db.query(Patient).filter(Patient.id == pid).first()
    if not p or not p.is_active:
        raise HTTPException(status_code=404, detail="Пациент не найден")
    if p.doctor_id != user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому пациенту")
    return p


def _owned_epicrisis(db: Session, eid: int, user: User) -> Epicrisis:
    e = db.query(Epicrisis).filter(Epicrisis.id == eid).first()
    if not e:
        raise HTTPException(status_code=404, detail="Эпикриз не найден")
    if e.doctor_id != user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому эпикризу")
    return e


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(status_code=422, detail="kind должен быть interim или discharge")
    return kind


_STATUS_RU = {"stable": "стабильное", "watch": "наблюдение",
              "serious": "тяжёлое", "critical": "критическое"}

_VISIT_RU = {"primary": "первичный осмотр", "daily": "дневник", "visit": "приём"}

_DEPT_RU = {"therapy": "Терапия", "cardiology": "Кардиология", "surgery": "Хирургия",
            "neurology": "Неврология", "pulmonology": "Пульмонология",
            "icu": "ОРИТ", "post_icu": "Пост-реанимация", "other": "Другое"}


def _fmt_d(dt) -> str:
    try:
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return str(dt or "—")


def _fmt_dt(dt) -> str:
    try:
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(dt or "—")


def _tail_with_note(items: list, keep: int, label: str) -> tuple:
    """Последние keep элементов + строка-пометка о пропущенных ранних."""
    if len(items) <= keep:
        return items, None
    omitted = len(items) - keep
    return items[-keep:], f"[…опущено {omitted} более ранних записей раздела «{label}»]"


def _build_history(db: Session, p: Patient,
                   max_consults: int = MAX_CONSULTS,
                   max_rounds: int = MAX_ROUNDS,
                   max_labs: int = MAX_LABS) -> tuple:
    """Собрать текст истории болезни для Claude + счётчики записей.

    Лимиты параметризованы: эпикриз берёт полные хвосты, pre-visit сводка
    (patients.py) — короткие.
    """
    parts = []

    # Паспортная часть + текущее состояние
    passport = [f"ФИО: {p.full_name}"]
    if p.date_of_birth:
        passport.append(f"Дата рождения: {_fmt_d(p.date_of_birth)}")
    if p.age is not None:
        passport.append(f"Возраст: {p.age}")
    if p.gender:
        passport.append(f"Пол: {p.gender}")
    if p.record_number:
        passport.append(f"№ карты/ИБ: {p.record_number}")
    if p.department:
        passport.append(f"Отделение: {_DEPT_RU.get(p.department, p.department)}")
    if p.ward:
        passport.append(f"Палата: {p.ward}")
    passport.append(f"Тип: {'стационарный' if p.patient_type == 'inpatient' else 'амбулаторный'}")
    parts.append("== ПАЦИЕНТ ==\n" + "\n".join(passport))

    adm = []
    if p.admission_date:
        adm.append(f"Дата поступления: {_fmt_dt(p.admission_date)}")
    if p.admission_diagnosis:
        adm.append(f"Диагноз при поступлении: {p.admission_diagnosis}")
    if p.admission_status:
        adm.append(f"Состояние при поступлении: {_STATUS_RU.get(p.admission_status, p.admission_status)}")
    if adm:
        parts.append("== ПОСТУПЛЕНИЕ ==\n" + "\n".join(adm))

    cur = []
    if p.status:
        cur.append(f"Текущее состояние: {_STATUS_RU.get(p.status, p.status)}")
    if p.diagnoses:
        cur.append("Диагнозы: " + ", ".join(p.diagnoses))
    if p.allergies:
        cur.append("Аллергии: " + ", ".join(p.allergies))
    if p.medications:
        cur.append("Назначения: " + ", ".join(p.medications))
    if p.history:
        cur.append("Анамнез (перенесённые): " + ", ".join(p.history))
    if cur:
        parts.append("== ТЕКУЩИЕ ДАННЫЕ КАРТЫ ==\n" + "\n".join(cur))

    # Консультации по хронологии; первичный осмотр не должен вылетать при срезе
    consults = (db.query(Consultation)
                .filter(Consultation.patient_id == p.id)
                .order_by(Consultation.created_at.asc()).all())
    primary = [c for c in consults if c.visit_type == "primary"]
    rest = [c for c in consults if c.visit_type != "primary"]
    rest_kept, rest_note = _tail_with_note(rest, max_consults, "записи врача")
    kept = sorted(primary + rest_kept, key=lambda c: c.created_at)
    if kept:
        lines = []
        if rest_note:
            lines.append(rest_note)
        for c in kept:
            hdr = f"--- {_fmt_dt(c.created_at)} · {_VISIT_RU.get(c.visit_type, c.visit_type)} ---"
            body = "\n".join(f"{lbl}: {val}" for lbl, val in [
                ("S (жалобы)", c.soap_s), ("O (объективно)", c.soap_o),
                ("A (оценка)", c.soap_a), ("P (план)", c.soap_p),
            ] if val)
            lines.append(hdr + ("\n" + body if body else "\n(без записи)"))
        parts.append("== ЗАПИСИ ВРАЧА (хронология) ==\n" + "\n".join(lines))

    rounds = (db.query(NightRound)
              .filter(NightRound.patient_id == p.id)
              .order_by(NightRound.created_at.asc()).all())
    rounds_kept, rounds_note = _tail_with_note(rounds, max_rounds, "ночные обходы")
    if rounds_kept:
        lines = [rounds_note] if rounds_note else []
        for r in rounds_kept:
            seg = [f"--- {_fmt_dt(r.created_at)} · ночной обход ---"]
            if r.status:
                seg.append(f"Статус: {_STATUS_RU.get(r.status, r.status)}")
            if r.vitals:
                seg.append("Витальные: " + json.dumps(r.vitals, ensure_ascii=False))
            if r.notes:
                seg.append(f"Осмотр: {r.notes}")
            if r.plan:
                seg.append(f"План: {r.plan}")
            lines.append("\n".join(seg))
        parts.append("== НОЧНЫЕ ОБХОДЫ ==\n" + "\n".join(lines))

    labs = (db.query(LabOrder)
            .filter(LabOrder.patient_id == p.id, LabOrder.status == "received")
            .order_by(LabOrder.received_at.asc()).all())
    labs_kept, labs_note = _tail_with_note(labs, max_labs, "анализы")
    if labs_kept:
        lines = [labs_note] if labs_note else []
        for o in labs_kept:
            seg = [f"--- {_fmt_dt(o.received_at)} · результаты анализов ---"]
            if o.tests:
                seg.append("Назначено: " + ", ".join(str(t) for t in o.tests))
            if o.results:
                seg.append("Результаты: " + json.dumps(o.results, ensure_ascii=False))
            lines.append("\n".join(seg))
        parts.append("== ЛАБОРАТОРНЫЕ ИССЛЕДОВАНИЯ ==\n" + "\n".join(lines))

    text = "\n\n".join(parts)
    if len(text) > MAX_CHARS:
        text = ("[Начало истории сокращено из-за объёма — ниже самые свежие данные]\n…"
                + text[-MAX_CHARS:])
    counts = {"consultations": len(consults), "rounds": len(rounds), "labs": len(labs)}
    return text, counts


_LANG_NAME = {"ru": "русском", "tj": "таджикском", "en": "английском"}

_SECTIONS_COMMON = """ПАСПОРТНАЯ ЧАСТЬ
ДИАГНОЗ ПРИ ПОСТУПЛЕНИИ
КЛИНИЧЕСКИЙ ДИАГНОЗ
ЖАЛОБЫ И АНАМНЕЗ
ДИНАМИКА СОСТОЯНИЯ
РЕЗУЛЬТАТЫ ОБСЛЕДОВАНИЙ
ПРОВЕДЁННОЕ ЛЕЧЕНИЕ"""

_SECTIONS_TAIL = {
    "discharge": "СОСТОЯНИЕ ПРИ ВЫПИСКЕ\nРЕКОМЕНДАЦИИ",
    "interim": "ТЕКУЩЕЕ СОСТОЯНИЕ И ОБОСНОВАНИЕ ПРОДОЛЖЕНИЯ ЛЕЧЕНИЯ\nПЛАН ДАЛЬНЕЙШЕГО ЛЕЧЕНИЯ",
}

_KIND_RU = {"discharge": "выписной", "interim": "этапный (промежуточный)"}


def _system_prompt(kind: str, language: str) -> str:
    lang_name = _LANG_NAME.get(language, "русском")
    return f"""Ты — врач, который готовит {_KIND_RU[kind]} эпикриз по истории болезни.
Тебе передают структурированные данные: сведения о пациенте, снимок поступления,
хронологию записей врача (первичный осмотр, дневники, приёмы в формате SOAP),
ночные обходы и результаты лабораторных исследований.

Составь текст эпикриза на {lang_name} языке. Разделы СТРОГО в этом порядке,
каждый заголовок — с новой строки ЗАГЛАВНЫМИ БУКВАМИ:

{_SECTIONS_COMMON}
{_SECTIONS_TAIL[kind]}

Жёсткие правила:
- Используй ТОЛЬКО факты из переданных данных. Ничего не выдумывай: ни результатов,
  ни препаратов, ни дозировок, ни дат.
- Если данных для раздела нет — напиши в нём одну строку: «Данных недостаточно.»
- Пиши в медицинском регистре, кратко и по существу, в третьем лице
  («пациент поступил…», «за время наблюдения…»).
- Не ставь новых диагнозов, которых нет в записях врача; формулировки диагнозов
  бери из записей.
- Ответ — только текст эпикриза, без преамбул, без markdown-разметки."""


# ---------- Endpoints ----------

@router.post("/draft", response_model=EpicrisisDraftResponse)
async def draft_epicrisis(
    payload: EpicrisisDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Черновик от Claude. НЕ сохраняется — врач правит и жмёт «Сохранить»."""
    _check_kind(payload.kind)
    p = _owned_patient(db, payload.patient_id, current_user)
    history, counts = _build_history(db, p)
    text = await _claude_call(
        _system_prompt(payload.kind, payload.language),
        f"История болезни пациента:\n\n{history}",
        max_tokens=2500,
    )
    draft = (text or "").strip()
    if not draft:
        raise HTTPException(status_code=502, detail="Пустой ответ модели — повторите попытку")
    audit(db, action="draft", entity="epicrisis", user_id=current_user.id,
          meta={"patient_id": p.id, "kind": payload.kind, **counts})
    return {"draft": draft, "counts": counts}


@router.post("/", response_model=EpicrisisResponse, status_code=status.HTTP_201_CREATED)
def create_epicrisis(
    payload: EpicrisisCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _check_kind(payload.kind)
    p = _owned_patient(db, payload.patient_id, current_user)
    e = Epicrisis(
        patient_id=p.id,
        doctor_id=current_user.id,
        kind=payload.kind,
        body=payload.body,
        language=payload.language or "ru",
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    audit(db, action="create", entity="epicrisis", user_id=current_user.id,
          entity_id=e.id, meta={"patient_id": p.id, "kind": e.kind})
    return e


@router.get("/", response_model=List[EpicrisisResponse])
def list_epicrises(
    response: Response,
    patient_id: Optional[int] = None,
    limit: Optional[int] = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Epicrisis).filter(Epicrisis.doctor_id == current_user.id)
    if patient_id is not None:
        q = q.filter(Epicrisis.patient_id == patient_id)
    response.headers["X-Total-Count"] = str(q.count())
    q = q.order_by(Epicrisis.created_at.desc()).offset(offset)
    if limit is not None:
        q = q.limit(limit)
    return q.all()


@router.get("/{eid}", response_model=EpicrisisResponse)
def get_epicrisis(
    eid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _owned_epicrisis(db, eid, current_user)


@router.get("/{eid}/pdf")
def epicrisis_pdf(
    eid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    e = _owned_epicrisis(db, eid, current_user)
    patient = db.query(Patient).filter(Patient.id == e.patient_id).first()
    pdf_bytes = render_epicrisis_pdf(e, patient, current_user)
    fname = f"avris-epicrisis-{eid}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
