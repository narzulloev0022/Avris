import os
import json
import re
import logging
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv

from auth import get_current_user
from models import User
from rate_limit import limiter

load_dotenv()
logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_VERSION = "2023-06-01"

LANG_LABEL = {"ru": "русский", "tj": "тоҷикӣ", "en": "English"}

router = APIRouter(prefix="/api/llm", tags=["llm"])


class SoapRequest(BaseModel):
    transcript: str
    language: str = "ru"
    patient_context: Optional[dict[str, Any]] = None


class DifferentialDiagnosis(BaseModel):
    name: str
    probability: int  # 0-100, Claude's qualitative confidence — not a calibrated stat
    icd10: Optional[str] = None


class AiRecommendations(BaseModel):
    """Claude's clinical thinking layer on top of the SOAP — what the doctor
    might be missing. Every field is suggestion-only; the frontend renders
    them as click-to-append snippets so the doctor stays in control."""
    additional_tests: list[str] = []
    additional_examination: list[str] = []
    differential_diagnosis: list[DifferentialDiagnosis] = []
    warnings: list[str] = []
    plan_suggestions: list[str] = []


class SoapResponse(BaseModel):
    subjective: str
    objective: str
    assessment: str
    plan: str
    # Optional auto-extracted patient + classification — only filled when
    # the prompt finds them in the transcript. Frontend treats every
    # field as a hint and lets the doctor confirm before saving.
    patient_full_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    patient_diagnoses: Optional[list[str]] = None
    patient_allergies: Optional[list[str]] = None
    department: Optional[str] = None  # therapy|cardiology|surgery|neurology|pulmonology|icu|post_icu|other
    severity: Optional[str] = None    # stable|watch|serious|critical
    avris_score: Optional[int] = None  # 0-100, derived from Claude's clinical assessment
    ai_recommendations: Optional[AiRecommendations] = None


class LabsRequest(BaseModel):
    results: dict[str, Any]
    patient_context: Optional[dict[str, Any]] = None


class LabsResponse(BaseModel):
    comment: str


async def _claude_call(system_prompt: str, user_msg: str, max_tokens: int = 1024) -> str:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Anthropic API не настроен (ANTHROPIC_API_KEY)")
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_msg}],
    }
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            r = await client.post(ANTHROPIC_URL, headers=headers, json=body)
        except httpx.HTTPError as e:
            logger.error("Anthropic request failed: %s", e)
            raise HTTPException(status_code=502, detail="Claude недоступен")
    if r.status_code != 200:
        logger.warning("Anthropic %d: %s", r.status_code, r.text[:300])
        raise HTTPException(status_code=r.status_code, detail=f"Ошибка Claude: {r.text[:200]}")
    j = r.json()
    parts = j.get("content", []) or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def _extract_json(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise HTTPException(status_code=502, detail="Claude вернул не JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Claude вернул невалидный JSON")


@router.post("/generate-soap", response_model=SoapResponse)
@limiter.limit("60/minute")
async def generate_soap(request: Request, req: SoapRequest, current_user: User = Depends(get_current_user)):
    if not (req.transcript or "").strip():
        raise HTTPException(status_code=400, detail="Пустой транскрипт")
    lang_label = LANG_LABEL.get(req.language, "русский")
    system_prompt = (
        "Ты — опытный клинический AI-ассистент AvrisAI. Твоя задача не просто структурировать "
        "документацию, а ПОМОГАТЬ врачу думать: указывать на пропуски, предлагать дифференциальные "
        "диагнозы, предупреждать о красных флагах и взаимодействиях, дополнять план. "
        f"Язык всех текстовых полей: {lang_label}. "
        "\n\n=== ОБЯЗАТЕЛЬНЫЕ ПОЛЯ JSON ===\n"
        "subjective — жалобы пациента (S); "
        "objective — данные осмотра (O); "
        "assessment — оценка/диагноз (A); "
        "plan — план лечения (P). "
        "\n\n=== ИЗВЛЕЧЁННЫЕ ДАННЫЕ ПАЦИЕНТА (заполняй только если явно есть в транскрипте) ===\n"
        'patient_full_name (string), patient_age (number), patient_gender ("М" или "Ж"), '
        "patient_diagnoses (array of strings), patient_allergies (array of strings). "
        "\n\n=== КЛАССИФИКАЦИЯ ===\n"
        "department — одно из: therapy / cardiology / surgery / neurology / pulmonology / icu / post_icu / other. "
        "severity — одно из: stable / watch / serious / critical. "
        "avris_score — целое число от 0 до 100, отражающее общее клиническое состояние пациента: "
        "0–39 = критическое (нестабильные витальные, угроза жизни, ОРИТ); "
        "40–59 = тяжёлое (значимые отклонения, требует пристального наблюдения); "
        "60–79 = среднее / под наблюдением (стабилизирующиеся, лечение в процессе); "
        "80–100 = стабильное (без острых проблем, плановое наблюдение). "
        "Если в Assessment/Plan есть слова: реанимация, ОРИТ, интенсивная терапия, ИВЛ, "
        'критическое состояние — ставь department="icu", severity="critical", avris_score 0-39. '
        'Если "послеоперационная палата" / "наблюдение после операции" — department="post_icu". '
        "\n\n=== AI_RECOMMENDATIONS — РЕКОМЕНДАЦИИ ВРАЧУ ===\n"
        "Заполняй объект ai_recommendations с пятью массивами. Это ключевая часть твоей работы — "
        "не дублируй то, что врач уже сделал; указывай только на пропуски и риски.\n\n"
        "additional_tests (array of strings) — дополнительные обследования, которые врач мог не назначить. "
        "Каждый пункт = одна строка с обоснованием в формате: "
        "'<обследование> — <почему>'. Пример: 'Спирометрия — для исключения бронхообструкции при жалобах на одышку'. "
        "Не предлагай то, что уже есть в plan.\n\n"
        "additional_examination (array of strings) — дополнительные приёмы физикального осмотра. "
        "Пример: 'Аускультация лёгких — кашель может указывать на пневмонию'. "
        "Не предлагай то, что уже описано в objective.\n\n"
        "differential_diagnosis (array of objects) — 3 наиболее вероятных диагноза с вероятностями. "
        'Каждый объект: {"name": "<диагноз>", "probability": <число 0-100>, "icd10": "<код или null>"}. '
        "Сортируй по убыванию probability. probability — твоя качественная оценка, не статистика. "
        "Включай ICD-10 код когда уверен. Пример: "
        '{"name":"Гипертоническая болезнь II ст.","probability":85,"icd10":"I11"}.\n\n'
        "warnings (array of strings) — клинические предупреждения. Включай: "
        "(а) лекарственные взаимодействия с препаратами из patient_context.medications или plan; "
        "(б) аллергии — если в plan назначен препарат, на который есть аллергия из patient_context.allergies; "
        "(в) красные флаги — комбинации симптомов требующие исключения опасных состояний "
        '(например: "Боль в груди + одышка — исключите ТЭЛА и инфаркт миокарда"). '
        "Каждое предупреждение начинай со слова 'Внимание:' для эмфазы.\n\n"
        "plan_suggestions (array of strings) — дополнения к плану лечения, которые врач мог упустить. "
        "Пример: 'Помимо эналаприла, рассмотрите аспирин 75 мг при наличии факторов риска ССЗ'. "
        "Не дублируй уже назначенные препараты.\n\n"
        "Если по какой-то секции нечего добавить — оставь пустой массив []. "
        "Не выдумывай рекомендации ради заполнения. Если транскрипт короткий или несодержательный — "
        "большинство массивов могут быть пустыми, и это нормально.\n\n"
        "Верни ровно один JSON-объект, без префиксов и markdown-обёрток."
    )
    user_msg = f"Транскрипт приёма:\n\n{req.transcript}"
    if req.patient_context:
        user_msg += "\n\nКонтекст пациента: " + json.dumps(req.patient_context, ensure_ascii=False)
    text = await _claude_call(system_prompt, user_msg, max_tokens=3000)
    parsed = _extract_json(text)
    def _list(v):
        if v is None: return None
        if isinstance(v, list): return [str(x) for x in v if x]
        if isinstance(v, str) and v.strip(): return [s.strip() for s in v.split(",") if s.strip()]
        return None
    def _str_list(v) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if x and str(x).strip()]
        return []
    age_raw = parsed.get("patient_age")
    try:
        age = int(age_raw) if age_raw not in (None, "") else None
    except (TypeError, ValueError):
        age = None
    score_raw = parsed.get("avris_score")
    try:
        score = int(score_raw) if score_raw not in (None, "") else None
        if score is not None:
            score = max(0, min(100, score))  # clamp to 0..100
    except (TypeError, ValueError):
        score = None
    # ai_recommendations is best-effort — bad shape from Claude shouldn't fail
    # the whole SOAP response; we just drop the recs and return SOAP without.
    ai_rec = None
    raw_rec = parsed.get("ai_recommendations")
    if isinstance(raw_rec, dict):
        diff_raw = raw_rec.get("differential_diagnosis") or []
        diffs: list[DifferentialDiagnosis] = []
        if isinstance(diff_raw, list):
            for d in diff_raw:
                if not isinstance(d, dict) or not d.get("name"):
                    continue
                try:
                    prob = int(d.get("probability") or 0)
                except (TypeError, ValueError):
                    prob = 0
                prob = max(0, min(100, prob))
                icd = d.get("icd10")
                diffs.append(DifferentialDiagnosis(
                    name=str(d["name"]).strip(),
                    probability=prob,
                    icd10=(str(icd).strip() if icd else None),
                ))
        ai_rec = AiRecommendations(
            additional_tests=_str_list(raw_rec.get("additional_tests")),
            additional_examination=_str_list(raw_rec.get("additional_examination")),
            differential_diagnosis=diffs,
            warnings=_str_list(raw_rec.get("warnings")),
            plan_suggestions=_str_list(raw_rec.get("plan_suggestions")),
        )
    return SoapResponse(
        subjective=str(parsed.get("subjective", "")),
        objective=str(parsed.get("objective", "")),
        assessment=str(parsed.get("assessment", "")),
        plan=str(parsed.get("plan", "")),
        patient_full_name=(parsed.get("patient_full_name") or None),
        patient_age=age,
        patient_gender=(parsed.get("patient_gender") or None),
        patient_diagnoses=_list(parsed.get("patient_diagnoses")),
        patient_allergies=_list(parsed.get("patient_allergies")),
        department=(parsed.get("department") or None),
        severity=(parsed.get("severity") or None),
        avris_score=score,
        ai_recommendations=ai_rec,
    )


@router.post("/analyze-labs", response_model=LabsResponse)
@limiter.limit("60/minute")
async def analyze_labs(request: Request, req: LabsRequest, current_user: User = Depends(get_current_user)):
    if not req.results:
        raise HTTPException(status_code=400, detail="Пустые результаты")
    system_prompt = (
        "Ты медицинский AI-ассистент. Дай краткий клинический комментарий "
        "к лабораторным результатам пациента: отметь отклонения от нормы и возможные интерпретации. "
        "Не делай окончательных диагнозов — формулируй как наблюдения и рекомендации. "
        "Ответ на русском, 2–4 предложения."
    )
    user_msg = "Результаты анализов: " + json.dumps(req.results, ensure_ascii=False)
    if req.patient_context:
        user_msg += "\n\nКонтекст пациента: " + json.dumps(req.patient_context, ensure_ascii=False)
    text = await _claude_call(system_prompt, user_msg, max_tokens=400)
    return LabsResponse(comment=text)
