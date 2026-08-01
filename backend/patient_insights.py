"""AI-инсайты по истории визитов — платная фича (Plus+).

Чем отличается от бесплатной сводки визита: сводка (``patient_visits``)
пересказывает ОДИН приём — что было, что назначили. Инсайт смотрит на
историю целиком: что повторяется от визита к визиту, что уже назначали
раньше, какие анализы сдавались и о чём стоит спросить врача в следующий
раз. Пересказ одного приёма продавать нельзя, картину за полгода — можно.

Границы жёсткие и те же, что у ассистента: никаких диагнозов, никаких
назначений и дозировок, никакой отмены назначенного врачом. Инсайт готовит
пациента к разговору с врачом, а не заменяет его. Формулировки вида
«вероятно, у вас…» отсекаются фильтром, а не только запрещены в промпте:
промпт — это просьба, фильтр — гарантия.

Разбор кэшируется по отпечатку исходников: пока история не изменилась,
повторное открытие экрана отдаёт сохранённый текст и не стоит ничего.
"""
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from llm import _claude_call
from models import (Consultation, LabOrder, PatientAccount, User, VisitInsight,
                    VisitSummary)
from patient_auth import get_current_patient
from patient_subscription import FEATURE_VISIT_INSIGHTS, require_feature
from patient_visits import _FORBIDDEN, _linked_patient_ids
from rate_limit import limiter

log = logging.getLogger("avris.patient_insights")

router = APIRouter(prefix="/api/patient/insights", tags=["patient"])

# Сколько визитов и анализов кладём в контекст. Больше — дороже и без пользы:
# картина последнего полугода информативнее, чем вся жизнь целиком.
MAX_VISITS = 8
MAX_LABS = 5

# Меньше двух визитов — продольного разбора не существует. Честнее сказать
# «пока не о чем», чем продать пересказ единственного приёма второй раз.
MIN_VISITS = 2

MAX_LIST_ITEMS = 5


class InsightParseError(ValueError):
    """Ответ модели не удалось разобрать — пациенту показывать нечего."""


_SYSTEM_PROMPT = """Ты — помощник клиники. Тебе дают историю визитов пациента к врачам (заметки SOAP) и результаты его анализов. Твоя задача — помочь пациенту увидеть картину целиком и подготовиться к следующему приёму.

Верни СТРОГО JSON-объект:
{
  "picture": "3-5 предложений простым языком: что происходило за это время, к чему врачи возвращаются чаще всего, что уже назначали и что менялось",
  "watch": ["на что обратить внимание в самочувствии — короткие фразы, 2-4 пункта"],
  "questions": ["вопросы, которые стоит задать врачу на следующем приёме — 2-4 пункта"]
}

Жёсткие правила:
- НИКАКИХ диагнозов и предположений о диагнозе («вероятно, у вас…», «скорее всего…», «похоже, у вас…»). Диагноз ставит только врач.
- НИКАКИХ назначений, препаратов и дозировок от себя. Ты можешь только пересказать то, что уже назначил врач.
- НИКОГДА не советуй отменить, заменить или изменить назначенное врачом.
- Пиши только о том, что есть в предоставленных данных. Не додумывай события и анализы.
- Если в данных есть тревожные признаки, скажи прямо: об этом нужно сказать врачу; при резком ухудшении — 103.
- Без медицинского жаргона; неизбежный термин поясняй в скобках.
- Обращайся на «вы», пиши на русском.
- Ответ — ТОЛЬКО JSON, без преамбул и markdown-ограждений."""


def _clean_list(raw, limit: int = MAX_LIST_ITEMS) -> List[str]:
    """Список коротких строк: пустые выкидываем, длину ограничиваем."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text[:300])
        if len(out) >= limit:
            break
    return out


def parse_insight(raw: str) -> dict:
    """Разбор ответа модели.

    Вынесено в чистую функцию и покрыто тестами: именно здесь ошибка тише
    всего — пациент прочитает выдуманное как факт и не усомнится.
    """
    if not raw or not raw.strip():
        raise InsightParseError("пустой ответ")
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise InsightParseError("в ответе нет JSON")
    try:
        obj = json.loads(match.group(0))
    except ValueError as exc:
        raise InsightParseError(f"не JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise InsightParseError("ожидался объект")

    picture = str(obj.get("picture") or "").strip()
    if not picture:
        raise InsightParseError("пустая картина")
    # Промпт запрещает вероятностные диагнозы, но обещание модели — не
    # гарантия. Проскочившее «вероятно, у вас…» пациент прочитает как приговор.
    if _FORBIDDEN.search(picture):
        raise InsightParseError("вероятностный диагноз в тексте")

    watch = _clean_list(obj.get("watch"))
    questions = _clean_list(obj.get("questions"))
    for phrase in watch + questions:
        if _FORBIDDEN.search(phrase):
            raise InsightParseError("вероятностный диагноз в списке")

    return {"picture": picture[:2000], "watch": watch, "questions": questions}


def _source_key(visits: List[Consultation], labs: List[LabOrder]) -> str:
    """Отпечаток исходников. Изменилась история — изменится ключ, и разбор
    соберётся заново; не изменилась — отдаём сохранённый."""
    payload = json.dumps(
        {
            "visits": [[v.id, str(v.created_at)] for v in visits],
            # Результаты анализа приезжают позже заказа и меняют картину —
            # поэтому в отпечаток идёт момент получения, а не создания.
            "labs": [[o.id, str(o.received_at), bool(o.results)] for o in labs],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _visit_block(v: Consultation, doctor: Optional[str], summary: Optional[VisitSummary]) -> str:
    when = (v.created_at or datetime.utcnow()).strftime("%d.%m.%Y")
    lines = [f"Визит {when}" + (f", врач {doctor}" if doctor else "")]
    # Сводка визита уже написана человеческим языком — она информативнее
    # сырой заметки и дешевле по токенам.
    if summary and summary.summary:
        lines.append(summary.summary)
    else:
        for label, value in (("Жалобы", v.soap_s), ("Осмотр", v.soap_o),
                             ("Заключение", v.soap_a), ("План", v.soap_p)):
            if value and str(value).strip():
                lines.append(f"{label}: {str(value).strip()[:600]}")
    return "\n".join(lines)


def _build_context(db: Session, account: PatientAccount, patient_ids: List[int]) -> tuple:
    visits = (db.query(Consultation)
              .filter(Consultation.patient_id.in_(patient_ids))
              .order_by(Consultation.created_at.desc())
              .limit(MAX_VISITS)
              .all())
    labs = (db.query(LabOrder)
            .filter(LabOrder.patient_id.in_(patient_ids))
            .order_by(LabOrder.created_at.desc())
            .limit(MAX_LABS)
            .all())
    return visits, labs


def _user_message(db: Session, account: PatientAccount,
                  visits: List[Consultation], labs: List[LabOrder]) -> str:
    doctors = {u.id: u.full_name for u in db.query(User).filter(
        User.id.in_({v.doctor_id for v in visits if v.doctor_id})).all()} if visits else {}
    summaries = {s.consultation_id: s for s in db.query(VisitSummary).filter(
        VisitSummary.consultation_id.in_([v.id for v in visits])).all()} if visits else {}

    parts = []
    if account.chronic_conditions:
        parts.append("Хронические состояния: " + ", ".join(account.chronic_conditions))
    if account.allergies:
        parts.append("Аллергии: " + ", ".join(account.allergies))

    # От старых к новым: так модель видит развитие, а не ленту задом наперёд.
    parts.append("История визитов (от ранних к поздним):")
    for v in reversed(visits):
        parts.append(_visit_block(v, doctors.get(v.doctor_id), summaries.get(v.id)))

    ready = [o for o in labs if o.results]
    if ready:
        parts.append("Анализы:")
        for o in reversed(ready):
            when = (o.created_at or datetime.utcnow()).strftime("%d.%m.%Y")
            parts.append(f"{when}: {json.dumps(o.results, ensure_ascii=False)[:800]}")
    return "\n\n".join(parts)


class InsightOut(BaseModel):
    picture: str
    watch: List[str]
    questions: List[str]
    visits_used: int
    created_at: datetime


@router.post("", response_model=InsightOut)
@limiter.limit("5/minute")
async def build_insight(
    request: Request,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Собрать (или отдать сохранённый) разбор истории визитов.

    По кнопке, а не при открытии экрана: каждый новый разбор — вызов модели.
    """
    require_feature(current, FEATURE_VISIT_INSIGHTS)

    patient_ids = _linked_patient_ids(db, current.id)
    if not patient_ids:
        raise HTTPException(status_code=409, detail="Нет связанной медкарты")

    visits, labs = _build_context(db, current, patient_ids)
    if len(visits) < MIN_VISITS:
        raise HTTPException(
            status_code=409,
            detail="Для разбора нужно хотя бы два визита — пока их меньше",
        )

    key = _source_key(visits, labs)
    saved = (db.query(VisitInsight)
             .filter(VisitInsight.patient_account_id == current.id)
             .order_by(VisitInsight.created_at.desc())
             .first())
    if saved and saved.source_key == key:
        return InsightOut(picture=saved.picture, watch=saved.watch or [],
                          questions=saved.questions or [], visits_used=saved.visits_used,
                          created_at=saved.created_at)

    raw = await _claude_call(_SYSTEM_PROMPT, _user_message(db, current, visits, labs),
                             max_tokens=900)
    try:
        parsed = parse_insight(raw or "")
    except InsightParseError as exc:
        log.warning("insight parse failed: %s", exc)
        raise HTTPException(status_code=502,
                            detail="Не удалось собрать разбор — попробуйте позже") from exc

    row = VisitInsight(
        patient_account_id=current.id,
        source_key=key,
        picture=parsed["picture"],
        watch=parsed["watch"],
        questions=parsed["questions"],
        visits_used=len(visits),
    )
    # Прошлый разбор больше не нужен: история изменилась, а хранить черновики
    # чужой медицинской жизни дольше необходимого незачем.
    if saved:
        db.delete(saved)
    db.add(row)
    db.commit()
    db.refresh(row)
    return InsightOut(picture=row.picture, watch=row.watch, questions=row.questions,
                      visits_used=row.visits_used, created_at=row.created_at)


@router.get("", response_model=Optional[InsightOut])
def read_insight(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Сохранённый разбор, если он есть. Бесплатно и без вызова модели —
    открытие экрана не должно стоить денег."""
    require_feature(current, FEATURE_VISIT_INSIGHTS)
    saved = (db.query(VisitInsight)
             .filter(VisitInsight.patient_account_id == current.id)
             .order_by(VisitInsight.created_at.desc())
             .first())
    if not saved:
        return None
    return InsightOut(picture=saved.picture, watch=saved.watch or [],
                      questions=saved.questions or [], visits_used=saved.visits_used,
                      created_at=saved.created_at)
