"""Носимые устройства пациента: привязка и приём измерений.

Протокол вендор-нейтральный с первого дня. Свой браслет Avris, чужой трекер
и выгрузка из Apple Health / Health Connect говорят с сервером одинаково:

1. пациент в приложении берёт код привязки (``POST /pair-code``);
2. устройство предъявляет код один раз (``POST /claim``) и получает
   собственный токен — пациентский ему не дают, им читается вся медкарта;
3. устройство шлёт пачки измерений своим токеном (``POST /measurements``).

Почему так, а не «залогинься за пациента»: браслет живёт своей жизнью,
синхронизируется без человека и может быть потерян вместе с токеном. Токен
устройства умеет ровно одно — писать измерения в свою же строку, и отзывается
отвязкой, не трогая аккаунт.

Чего здесь нет: составных «баллов» вроде индекса стресса, которые считает
прошивка. Хранится только то, что физически измерено датчиком. Чужой балл
нельзя ни проверить, ни объяснить врачу, а выглядит он как медицинский
показатель.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit import audit
from database import get_db
from models import (DeviceMeasurement, DevicePairCode, PatientAccount, PatientDevice)
from patient_auth import get_current_patient
from rate_limit import limiter

router = APIRouter(prefix="/api/patient/devices", tags=["patient"])

PAIR_CODE_TTL_MINUTES = 10

# Реестр показателей: единица и границы правдоподобия. Границы не медицинская
# норма, а защита от мусора — датчик на потерянном браслете умеет прислать
# пульс 0 и температуру 200, и такие значения не должны попадать в медкарту.
METRICS = {
    "heart_rate": ("уд/мин", 20.0, 250.0),
    "heart_rate_resting": ("уд/мин", 20.0, 150.0),
    "hrv": ("мс", 1.0, 500.0),
    "spo2": ("%", 50.0, 100.0),
    "respiratory_rate": ("вдох/мин", 4.0, 60.0),
    "body_temperature": ("°C", 30.0, 45.0),
    "skin_temperature": ("°C", 25.0, 45.0),
    "blood_pressure_systolic": ("мм рт. ст.", 50.0, 300.0),
    "blood_pressure_diastolic": ("мм рт. ст.", 30.0, 200.0),
    "steps": ("шагов", 0.0, 200000.0),
    "distance": ("м", 0.0, 500000.0),
    "active_energy": ("ккал", 0.0, 20000.0),
    "sleep": ("мин", 0.0, 1440.0),
    "sleep_deep": ("мин", 0.0, 1440.0),
    "weight": ("кг", 2.0, 400.0),
}

VENDORS = {"avris_band", "apple_health", "health_connect", "other"}

# Сколько измерений принимаем за один запрос. Браслет за сутки offline
# накопит сотни точек — но не десятки тысяч.
MAX_BATCH = 500


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def device_from_token(
    db: Session,
    authorization: Optional[str],
) -> PatientDevice:
    """Устройство по его токену. Пациентский токен здесь не подходит и не
    должен: у них разные права и разный срок жизни."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Нужен токен устройства")
    row = (db.query(PatientDevice)
           .filter(PatientDevice.secret_hash == _hash(authorization[7:].strip()),
                   PatientDevice.is_active.is_(True))
           .first())
    if row is None:
        raise HTTPException(status_code=401, detail="Устройство не найдено или отвязано")
    return row


# ---------- схемы ----------

class PairCodeOut(BaseModel):
    code: str
    expires_at: datetime


class ClaimBody(BaseModel):
    code: str = Field(..., min_length=4, max_length=8)
    external_id: str = Field(..., min_length=4, max_length=128)
    vendor: str = Field("other", max_length=32)
    model: Optional[str] = Field(None, max_length=64)
    name: Optional[str] = Field(None, max_length=64)


class ClaimOut(BaseModel):
    device_id: int
    token: str
    # Что сервер готов принять — прошивке не нужно зашивать список у себя.
    accepted_metrics: List[str]


class DeviceOut(BaseModel):
    id: int
    vendor: str
    model: Optional[str] = None
    name: Optional[str] = None
    paired_at: datetime
    last_sync_at: Optional[datetime] = None


class MeasurementIn(BaseModel):
    kind: str = Field(..., max_length=32)
    value: float
    taken_at: datetime


class IngestBody(BaseModel):
    measurements: List[MeasurementIn]


class IngestOut(BaseModel):
    accepted: int
    # Уже присылали (та же тройка устройство-вид-время) — не ошибка, а
    # нормальная работа устройства, которое повторяет пропущенный час.
    duplicates: int
    rejected: int


class MetricOut(BaseModel):
    kind: str
    value: float
    unit: str
    taken_at: datetime
    device_id: int


# ---------- сторона пациента ----------

@router.post("/pair-code", response_model=PairCodeOut)
@limiter.limit("10/minute")
def issue_pair_code(
    request: Request,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Код, который пациент вводит на устройстве (или сканирует им)."""
    now = datetime.utcnow()
    # Просроченные чистим лениво, на каждой выдаче — отдельный джоб ради
    # десятка строк не нужен.
    db.query(DevicePairCode).filter(DevicePairCode.expires_at < now).delete()
    for _ in range(10):
        code = f"{secrets.randbelow(100_000_000):08d}"
        row = DevicePairCode(code=code, patient_account_id=current.id,
                             expires_at=now + timedelta(minutes=PAIR_CODE_TTL_MINUTES))
        db.add(row)
        try:
            db.commit()
            return PairCodeOut(code=code, expires_at=row.expires_at)
        except IntegrityError:
            db.rollback()
    raise HTTPException(status_code=503, detail="Не удалось выдать код — попробуйте ещё раз")


@router.get("", response_model=List[DeviceOut])
def list_devices(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    rows = (db.query(PatientDevice)
            .filter(PatientDevice.patient_account_id == current.id,
                    PatientDevice.is_active.is_(True))
            .order_by(PatientDevice.paired_at.desc())
            .all())
    return [DeviceOut(id=d.id, vendor=d.vendor, model=d.model, name=d.name,
                      paired_at=d.paired_at, last_sync_at=d.last_sync_at) for d in rows]


@router.delete("/{device_id}", status_code=204)
@limiter.limit("20/minute")
def unpair(
    request: Request,
    device_id: int,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Отвязать устройство. Измерения остаются: это медкарта, а не настройки
    приложения — стирать историю вместе с гаджетом нельзя."""
    row = (db.query(PatientDevice)
           .filter(PatientDevice.id == device_id,
                   PatientDevice.patient_account_id == current.id)
           .first())
    if row is None:
        raise HTTPException(status_code=404, detail="Устройство не найдено")
    row.is_active = False
    # Токен умирает вместе с привязкой: потерянный браслет писать не должен.
    row.secret_hash = _hash(secrets.token_urlsafe(32))
    db.commit()
    audit(db, action="unpair", entity="patient_device", user_id=None,
          entity_id=row.id, meta={"door": "patient"})


@router.get("/metrics", response_model=List[MetricOut])
def latest_metrics(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Последнее значение по каждому показателю — для экрана «Здоровье»."""
    rows = (db.query(DeviceMeasurement)
            .filter(DeviceMeasurement.patient_account_id == current.id)
            .order_by(DeviceMeasurement.taken_at.desc())
            .limit(2000)
            .all())
    seen: dict = {}
    for r in rows:
        if r.kind not in seen:
            seen[r.kind] = MetricOut(kind=r.kind, value=r.value, unit=r.unit,
                                     taken_at=r.taken_at, device_id=r.device_id)
    return list(seen.values())


@router.get("/metrics/{kind}", response_model=List[MetricOut])
def metric_series(
    kind: str,
    days: int = 7,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    if kind not in METRICS:
        raise HTTPException(status_code=404, detail="Неизвестный показатель")
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 90)))
    rows = (db.query(DeviceMeasurement)
            .filter(DeviceMeasurement.patient_account_id == current.id,
                    DeviceMeasurement.kind == kind,
                    DeviceMeasurement.taken_at >= since)
            .order_by(DeviceMeasurement.taken_at.desc())
            .all())
    return [MetricOut(kind=r.kind, value=r.value, unit=r.unit,
                      taken_at=r.taken_at, device_id=r.device_id) for r in rows]


# ---------- сторона устройства ----------

@router.post("/claim", response_model=ClaimOut)
@limiter.limit("20/minute")
def claim(
    request: Request,
    body: ClaimBody,
    db: Session = Depends(get_db),
):
    """Устройство предъявляет код и получает собственный токен.

    Код одноразовый и живёт минуты: подсмотренный вчера экран не даёт доступа
    к чужим измерениям.
    """
    now = datetime.utcnow()
    code = (db.query(DevicePairCode)
            .filter(DevicePairCode.code == body.code.strip(),
                    DevicePairCode.used_at.is_(None),
                    DevicePairCode.expires_at >= now)
            .first())
    if code is None:
        raise HTTPException(status_code=404, detail="Код недействителен или истёк")
    vendor = body.vendor if body.vendor in VENDORS else "other"

    secret = secrets.token_urlsafe(32)
    device = (db.query(PatientDevice)
              .filter(PatientDevice.patient_account_id == code.patient_account_id,
                      PatientDevice.external_id == body.external_id.strip())
              .first())
    if device is None:
        device = PatientDevice(patient_account_id=code.patient_account_id,
                               external_id=body.external_id.strip())
        db.add(device)
    # Повторная привязка того же экземпляра обновляет токен, а не заводит
    # второе устройство: пациент сбросил браслет — строка та же.
    device.vendor = vendor
    device.model = body.model
    device.name = body.name or body.model
    device.secret_hash = _hash(secret)
    device.is_active = True
    device.paired_at = now
    code.used_at = now
    db.commit()
    db.refresh(device)
    audit(db, action="pair", entity="patient_device", user_id=None,
          entity_id=device.id, meta={"door": "patient", "vendor": vendor})
    return ClaimOut(device_id=device.id, token=secret,
                    accepted_metrics=sorted(METRICS))


@router.post("/measurements", response_model=IngestOut)
@limiter.limit("60/minute")
def ingest(
    request: Request,
    body: IngestBody,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Пачка измерений от устройства."""
    device = device_from_token(db, authorization)
    if len(body.measurements) > MAX_BATCH:
        raise HTTPException(status_code=413, detail="Слишком большая пачка измерений")

    now = datetime.utcnow()
    accepted = duplicates = rejected = 0
    for m in body.measurements:
        spec = METRICS.get(m.kind)
        if spec is None:
            rejected += 1
            continue
        unit, low, high = spec
        # Будущее время — сбитые часы на устройстве; такое значение исказит
        # и график, и мониторинг.
        if not (low <= m.value <= high) or m.taken_at > now + timedelta(minutes=5):
            rejected += 1
            continue
        db.add(DeviceMeasurement(
            patient_account_id=device.patient_account_id,
            device_id=device.id,
            kind=m.kind,
            value=float(m.value),
            unit=unit,
            taken_at=m.taken_at,
        ))
        try:
            db.commit()
            accepted += 1
        except IntegrityError:
            db.rollback()
            duplicates += 1

    device.last_sync_at = now
    db.commit()
    return IngestOut(accepted=accepted, duplicates=duplicates, rejected=rejected)
