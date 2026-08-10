"""Дневник питания по фото — платная функция тарифа Pro.

Пациент фотографирует тарелку, модель называет блюда и оценивает калорийность,
приложение складывает день. Это единственная функция, где приложение
высказывается о еде, поэтому две вещи заданы жёстко:

1. **Оценка, а не измерение.** По фотографии нельзя узнать, сколько масла в
   плове. Модель возвращает уровень уверенности, а любые цифры пациент может
   поправить руками — и в интерфейсе это названо оценкой, а не фактом.
2. **Никаких советов.** Ни «вам стоит меньше есть», ни «это вредно при вашем
   диабете». Дневник считает, решения принимает врач.

Фото не сохраняется: оно уходит в модель и забывается. Снимок еды — это место,
время и обстоятельства жизни человека, и хранить его ради трёх цифр незачем.
"""
import base64
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from audit import audit
from database import get_db
from llm import _llm_vision_call
from models import NutritionEntry, NutritionUsage, PatientAccount
from patient_auth import get_current_patient
from patient_subscription import FEATURE_NUTRITION, month_total, require_feature
from rate_limit import limiter

router = APIRouter(prefix="/api/patient/nutrition", tags=["patient"])
logger = logging.getLogger("avris.nutrition")

MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/heic", "image/webp"}

# Разборов фото в сутки и в месяц. Зрение — самый дорогой вызов в приложении:
# дневной ловит случайную серию, месячный держит экономику Pro (тридцать
# снимков в день целый месяц стоили бы больше половины его цены). Десять
# снимков в день — больше, чем человек ест.
DAILY_PHOTO_CAP = int(os.getenv("NUTRITION_DAILY_CAP", "30"))
MONTHLY_PHOTO_CAP = int(os.getenv("NUTRITION_MONTHLY_CAP", "300"))

_SYSTEM_PROMPT = """Ты помогаешь пациенту вести дневник питания по фотографии тарелки.

Верни СТРОГО JSON-объект без пояснений вокруг:
{
  "is_food": true,
  "title": "короткое название приёма пищи (например «Плов с курицей и салат»)",
  "items": [{"name": "Плов", "grams": 250, "kcal": 520}],
  "kcal": 780,
  "protein_g": 32.0,
  "fat_g": 28.0,
  "carbs_g": 95.0,
  "confidence": "low|medium|high"
}

Правила:
- Если на фото нет еды — верни {"is_food": false} и больше ничего.
- Оценивай порции по видимому размеру посуды. Если размер непонятен — confidence: "low".
- Числа — целые для kcal и grams, дробные допустимы для БЖУ.
- Не давай советов о питании, здоровье или диете. Не упоминай болезни.
- Не пиши ничего, кроме JSON."""


# ---------- разбор ответа модели ----------

class NutritionParseError(ValueError):
    """Модель ответила не тем, чем нужно."""


def parse_nutrition(raw: str) -> dict:
    """Достать структуру из ответа модели.

    Отдельная чистая функция: разбор ответа модели — то место, где ошибка
    тише всего и дороже всего, и его надо проверять тестом, а не глазами на
    живом вызове.
    """
    text = (raw or "").strip()
    if not text:
        raise NutritionParseError("пустой ответ модели")
    # Модель иногда оборачивает JSON в ```json ... ```
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise NutritionParseError("в ответе нет JSON")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise NutritionParseError(f"невалидный JSON: {e}") from e
    if not isinstance(data, dict):
        raise NutritionParseError("ожидался объект")

    if data.get("is_food") is False:
        return {"is_food": False}

    items = []
    for raw_item in (data.get("items") or []):
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get("name") or "").strip()[:120]
        if not name:
            continue
        items.append({
            "name": name,
            "grams": _positive_int(raw_item.get("grams")),
            "kcal": _positive_int(raw_item.get("kcal")),
        })

    kcal = _positive_int(data.get("kcal"))
    if kcal is None and items:
        # Модель назвала блюда, но забыла итог — складываем сами.
        kcal = sum(i["kcal"] or 0 for i in items) or None
    if kcal is None:
        raise NutritionParseError("нет калорийности")

    confidence = str(data.get("confidence") or "").lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"

    return {
        "is_food": True,
        "title": (str(data.get("title") or "").strip() or "Приём пищи")[:200],
        "items": items,
        "kcal": kcal,
        "protein_g": _positive_float(data.get("protein_g")),
        "fat_g": _positive_float(data.get("fat_g")),
        "carbs_g": _positive_float(data.get("carbs_g")),
        "confidence": confidence,
    }


def _positive_int(value: Any) -> Optional[int]:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    # Верхняя граница — защита от галлюцинации вроде 99999 ккал в тарелке.
    return n if 0 <= n <= 20000 else None


def _positive_float(value: Any) -> Optional[float]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return round(n, 1) if 0 <= n <= 5000 else None


# ---------- дневной предохранитель ----------

def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _bump_usage(db: Session, account_id: int) -> None:
    # Месяц считаем первым: если кончился он, «попробуйте завтра» было бы
    # враньём — ждать до первого числа.
    if month_total(db, NutritionUsage, account_id) >= MONTHLY_PHOTO_CAP:
        raise HTTPException(
            status_code=429,
            detail="Разборы фото на этот месяц исчерпаны — обновятся первого числа")

    day = _today_key()
    row = (db.query(NutritionUsage)
           .filter(NutritionUsage.patient_account_id == account_id,
                   NutritionUsage.day == day)
           .first())
    if row is None:
        row = NutritionUsage(patient_account_id=account_id, day=day, count=0)
        db.add(row)
    if row.count >= DAILY_PHOTO_CAP:
        raise HTTPException(status_code=429,
                            detail="Слишком много снимков за сегодня — попробуйте завтра")
    row.count += 1
    db.commit()


def _release(db: Session, account_id: int) -> None:
    """Вернуть слот, если разбор не состоялся: платить лимитом за нашу ошибку
    пациент не должен."""
    row = (db.query(NutritionUsage)
           .filter(NutritionUsage.patient_account_id == account_id,
                   NutritionUsage.day == _today_key())
           .first())
    if row is not None and row.count > 0:
        row.count -= 1
        db.commit()


# ---------- схемы ----------

class NutritionItemOut(BaseModel):
    name: str
    grams: Optional[int] = None
    kcal: Optional[int] = None


class NutritionEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    eaten_at: datetime
    title: str
    items: List[dict] = []
    kcal: int
    protein_g: Optional[float] = None
    fat_g: Optional[float] = None
    carbs_g: Optional[float] = None
    source: str
    confidence: Optional[str] = None
    note: Optional[str] = None


class NutritionDayOut(BaseModel):
    day: date
    total_kcal: int
    total_protein_g: float
    total_fat_g: float
    total_carbs_g: float
    entries: List[NutritionEntryOut]


class ManualEntryIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kcal: int = Field(ge=0, le=20000)
    protein_g: Optional[float] = Field(default=None, ge=0, le=5000)
    fat_g: Optional[float] = Field(default=None, ge=0, le=5000)
    carbs_g: Optional[float] = Field(default=None, ge=0, le=5000)
    eaten_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=500)


# ---------- эндпоинты ----------

@router.post("/photo", response_model=NutritionEntryOut, status_code=201)
@limiter.limit("20/minute")
async def add_by_photo(
    request: Request,
    file: UploadFile = File(...),
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Фото тарелки → запись в дневнике. Само фото нигде не сохраняется."""
    require_feature(current, FEATURE_NUTRITION)

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Поддерживаются только фотографии")
    image = await file.read()
    if not image:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(image) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Фото слишком большое")

    # Слот занимаем ДО вызова модели и возвращаем при любой неудаче.
    _bump_usage(db, current.id)
    try:
        answer = await _llm_vision_call(
            _SYSTEM_PROMPT,
            "Что на фото и сколько в этом калорий?",
            base64.b64encode(image).decode(),
            "image/jpeg" if content_type in {"image/jpg", "image/jpeg"} else content_type,
        )
        parsed = parse_nutrition(answer)
    except NutritionParseError as e:
        _release(db, current.id)
        logger.warning("nutrition parse failed: %s", e)
        raise HTTPException(status_code=502, detail="Не удалось разобрать снимок — попробуйте ещё раз")
    except Exception:
        _release(db, current.id)
        raise

    if not parsed.get("is_food"):
        _release(db, current.id)
        raise HTTPException(status_code=422, detail="На фото не видно еды — сфотографируйте тарелку")

    entry = NutritionEntry(
        patient_account_id=current.id,
        eaten_at=datetime.utcnow(),
        title=parsed["title"],
        items=parsed["items"],
        kcal=parsed["kcal"],
        protein_g=parsed["protein_g"],
        fat_g=parsed["fat_g"],
        carbs_g=parsed["carbs_g"],
        source="photo",
        confidence=parsed["confidence"],
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    audit(db, action="create", entity="nutrition_entry", entity_id=entry.id,
          meta={"door": "patient", "source": "photo"})
    return entry


@router.post("", response_model=NutritionEntryOut, status_code=201)
@limiter.limit("60/minute")
def add_manually(
    request: Request,
    body: ManualEntryIn,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Запись руками — когда фото не нужно или оценка по нему неверна."""
    require_feature(current, FEATURE_NUTRITION)
    entry = NutritionEntry(
        patient_account_id=current.id,
        eaten_at=body.eaten_at or datetime.utcnow(),
        title=body.title.strip(),
        items=[],
        kcal=body.kcal,
        protein_g=body.protein_g,
        fat_g=body.fat_g,
        carbs_g=body.carbs_g,
        source="manual",
        note=body.note,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.get("", response_model=NutritionDayOut)
def day(
    on: Optional[date] = Query(default=None, description="день, по умолчанию сегодня"),
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """День дневника: записи и итоги."""
    require_feature(current, FEATURE_NUTRITION)
    target = on or datetime.utcnow().date()
    start = datetime.combine(target, datetime.min.time())
    end = datetime.combine(target, datetime.max.time())
    rows = (db.query(NutritionEntry)
            .filter(NutritionEntry.patient_account_id == current.id,
                    NutritionEntry.eaten_at >= start,
                    NutritionEntry.eaten_at <= end)
            .order_by(NutritionEntry.eaten_at.asc())
            .all())
    return NutritionDayOut(
        day=target,
        total_kcal=sum(r.kcal or 0 for r in rows),
        total_protein_g=round(sum(r.protein_g or 0 for r in rows), 1),
        total_fat_g=round(sum(r.fat_g or 0 for r in rows), 1),
        total_carbs_g=round(sum(r.carbs_g or 0 for r in rows), 1),
        entries=[NutritionEntryOut.model_validate(r) for r in rows],
    )


@router.delete("/{entry_id}", status_code=204)
def delete_entry(
    entry_id: int,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    require_feature(current, FEATURE_NUTRITION)
    entry = (db.query(NutritionEntry)
             .filter(NutritionEntry.id == entry_id,
                     NutritionEntry.patient_account_id == current.id)
             .first())
    if entry is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    db.delete(entry)
    db.commit()
