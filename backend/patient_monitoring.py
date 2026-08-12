"""Мониторинг показателей (тариф Pro).

Что это на самом деле: сервер регулярно смотрит данные, которые пациент сам
внёс — измерения сахара и пришедшие результаты анализов, — и, если что-то
выбивается, оставляет заметку «покажите это врачу». Проверка идёт на сервере
по расписанию, поэтому находка появляется и тогда, когда приложение не
открывали.

Чего здесь нет и не будет: причин, диагнозов, советов по лечению и слова
«норма» о том, что мы не сумели сопоставить с нормой. Правило умеет ровно
одно — заметить и позвать к врачу.

Дублей нет по построению: у каждой находки есть ключ, и повторная проверка
не создаёт вторую копию. Приложение, которое каждый день сообщает об одном
и том же, перестают читать — включая тот единственный раз, когда прочитать
было нужно.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from email_service import send_patient_alert_email
from llm import LIGHT, _llm_call
from models import (GlucoseReading, HealthAlert, LabOrder, MonitoringDigest,
                    MonitoringRun, PatientAccount, PatientLink)
from patient_auth import get_current_patient
from patient_glucose import HIGH_STREAK, LOW_MMOL, VERY_LOW_MMOL, classify
from patient_subscription import FEATURE_MONITORING, PRO, has_feature, resolve_tier
from patient_visits import _FORBIDDEN

log = logging.getLogger("avris.patient_monitoring")

router = APIRouter(prefix="/api/patient/monitoring", tags=["patient"])

# Как часто сервер обходит пациентов. Шесть часов — компромисс: находка не
# ждёт сутки, а база не перечитывается каждые пять минут ради данных,
# которые меняются раз в день.
CHECK_INTERVAL_SECONDS = 6 * 3600

# Сколько дней молчания дневника считаем поводом напомнить. Меньше — и
# напоминание превращается в придирку к человеку, который просто занят.
SILENT_DAYS = 5

# Напоминаем о молчании только тем, кто дневник действительно вёл: иначе
# получится упрёк за то, что функцией не пользовались.
SILENT_MIN_HISTORY = 5

ALERT_TTL_DAYS = 30


# ---------- разбор нормы лаборатории ----------

def _number(raw: str) -> Optional[float]:
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(raw).strip())
    return float(match.group(0).replace(",", ".")) if match else None


def out_of_range(value: str, range_text: Optional[str]) -> Optional[bool]:
    """True/False — попадает ли значение в норму; None — норму не разобрали.

    Сознательно консервативна и повторяет клиентскую reference_range.dart:
    любая строка, которую не удалось прочесть однозначно, даёт None. Сказать
    «показатель вне нормы» о том, что мы не поняли, хуже, чем промолчать.
    """
    v = _number(value)
    text = (range_text or "").strip()
    if v is None or not text:
        return None
    norm = (text.replace("–", "-").replace("—", "-").replace("−", "-").lower())

    upper = re.fullmatch(r"(?:<|≤|<=|до|не более)\s*([\d.,]+)", norm)
    if upper:
        top = _number(upper.group(1))
        return None if top is None else v > top

    lower = re.fullmatch(r"(?:>|≥|>=|от|не менее)\s*([\d.,]+)", norm)
    if lower:
        bottom = _number(lower.group(1))
        return None if bottom is None else v < bottom

    interval = re.fullmatch(r"([\d.,]+)\s*-\s*([\d.,]+)", norm)
    if interval:
        lo, hi = _number(interval.group(1)), _number(interval.group(2))
        if lo is None or hi is None or lo > hi:
            return None
        return not (lo <= v <= hi)

    return None


# ---------- правила ----------

def evaluate(db: Session, account: PatientAccount, now: datetime) -> List[dict]:
    """Что мониторинг видит в данных пациента прямо сейчас.

    Чистая по смыслу функция: читает и возвращает находки, ничего не пишет.
    Правила простые намеренно — их должно быть видно насквозь, потому что
    каждое из них однажды разбудит человека сообщением.
    """
    found: List[dict] = []

    recent = (db.query(GlucoseReading)
              .filter(GlucoseReading.patient_account_id == account.id,
                      GlucoseReading.taken_at >= now - timedelta(days=7))
              .order_by(GlucoseReading.taken_at.desc())
              .all())

    # 1. Гипогликемия за сутки — единственное, о чём стоит сказать сразу.
    for r in recent:
        if r.taken_at >= now - timedelta(days=1) and r.mmol < LOW_MMOL:
            found.append({
                "kind": "glucose_low",
                "severity": "urgent" if r.mmol < VERY_LOW_MMOL else "attention",
                "dedup_key": f"glucose_low:{r.id}",
                "details": {"mmol": r.mmol},
            })
            break

    # 2. Несколько высоких подряд — устойчивая картина, а не плохой день.
    streak = 0
    for r in recent:
        if classify(r.mmol, r.context) == "high":
            streak += 1
            if streak >= HIGH_STREAK:
                found.append({
                    "kind": "glucose_high_streak",
                    "severity": "attention",
                    "dedup_key": f"glucose_high_streak:{r.id}",
                    "details": {"count": streak},
                })
                break
        else:
            break

    # 3. Дневник замолчал — но только у того, кто его вёл.
    total = (db.query(GlucoseReading)
             .filter(GlucoseReading.patient_account_id == account.id).count())
    if total >= SILENT_MIN_HISTORY:
        last = (db.query(GlucoseReading)
                .filter(GlucoseReading.patient_account_id == account.id)
                .order_by(GlucoseReading.taken_at.desc())
                .first())
        if last and last.taken_at < now - timedelta(days=SILENT_DAYS):
            found.append({
                "kind": "glucose_silent",
                "severity": "info",
                # Ключ по дню последнего измерения: пока дневник молчит,
                # находка остаётся одна и та же.
                "dedup_key": f"glucose_silent:{last.taken_at.date()}",
                "details": {"days": (now - last.taken_at).days},
            })

    # 4. Свежий анализ со значением вне нормы лаборатории.
    patient_ids = [l.patient_id for l in db.query(PatientLink).filter(
        PatientLink.patient_account_id == account.id,
        PatientLink.revoked_at.is_(None)).all()]
    if patient_ids:
        orders = (db.query(LabOrder)
                  .filter(LabOrder.patient_id.in_(patient_ids),
                          LabOrder.results.isnot(None),
                          LabOrder.received_at >= now - timedelta(days=7))
                  .all())
        for order in orders:
            names = [name for name, cell in (order.results or {}).items()
                     if isinstance(cell, dict)
                     and out_of_range(cell.get("value", ""), cell.get("range")) is True]
            if names:
                found.append({
                    "kind": "lab_out_of_range",
                    "severity": "attention",
                    "dedup_key": f"lab_out_of_range:{order.id}",
                    "details": {"tests": names[:5], "count": len(names)},
                })

    return found


def _notify(db: Session, account: PatientAccount, fresh: List[HealthAlert],
            now: datetime) -> None:
    """Позвать пациента в приложение, если находка срочная.

    Почему только ``urgent``: письмо о каждой находке — это рассылка, а
    рассылку перестают открывать. Право разбудить человека надо тратить на
    низкий сахар, а не на «сдайте анализ вовремя».

    Почему письмо, а не пуш: пуш требует сертификата Apple, которого пока
    нет. Письмо — единственный канал, который у платформы уже работает, и
    он лучше, чем не сообщить вовсе. Содержания в письме нет: медданные
    остаются за входом в приложение (см. send_patient_alert_email).
    """
    urgent = [a for a in fresh if a.severity == "urgent"]
    if not urgent or not account.email:
        return
    # Не чаще одного письма в сутки: три находки подряд не должны
    # превращаться в три письма.
    recent = (db.query(HealthAlert)
              .filter(HealthAlert.patient_account_id == account.id,
                      HealthAlert.notified_at.isnot(None),
                      HealthAlert.notified_at >= now - timedelta(days=1))
              .first())
    if recent is not None:
        return
    try:
        sent = send_patient_alert_email(account.email, account.full_name or "")
    except Exception:  # почта не должна ронять обход
        log.exception("alert email failed for account %s", account.id)
        return
    if not sent:
        # Не проставляем штамп: пусть следующая проверка попробует снова.
        return
    for alert in urgent:
        alert.notified_at = now
    db.commit()


def run_for_account(db: Session, account: PatientAccount, now: Optional[datetime] = None) -> int:
    """Проверить пациента и сохранить новые находки. Возвращает их число."""
    now = now or datetime.utcnow()
    created = 0
    fresh: List[HealthAlert] = []
    for item in evaluate(db, account, now):
        alert = HealthAlert(
            patient_account_id=account.id,
            kind=item["kind"],
            severity=item["severity"],
            dedup_key=item["dedup_key"],
            details=item["details"],
            created_at=now,
        )
        db.add(alert)
        try:
            db.commit()
            created += 1
            fresh.append(alert)
        except IntegrityError:
            # Такая находка уже есть — ровно то, ради чего нужен dedup_key.
            db.rollback()

    _notify(db, account, fresh, now)

    run = (db.query(MonitoringRun)
           .filter(MonitoringRun.patient_account_id == account.id).first())
    if run:
        run.checked_at = now
    else:
        db.add(MonitoringRun(patient_account_id=account.id, checked_at=now))
    db.commit()
    return created


def run_all() -> int:
    """Обход всех пациентов с правом на мониторинг.

    Простой цикл, а не очередь: на текущей базе это секунды. Когда пациентов
    станут тысячи, сюда придёт нормальный планировщик — но выдумывать его
    заранее значит поддерживать сложность, которая пока ничего не решает.
    """
    db = SessionLocal()
    total = 0
    try:
        accounts = (db.query(PatientAccount)
                    .filter(PatientAccount.is_active.is_(True),
                            PatientAccount.subscription_tier == PRO)
                    .all())
        for account in accounts:
            if not has_feature(account, FEATURE_MONITORING):
                continue  # тариф мог истечь между запросом и проверкой
            try:
                created = run_for_account(db, account)
                total += created
                if created:
                    asyncio.run(build_digest(db, account))
            except Exception:  # одна карта не должна ронять обход
                log.exception("monitoring failed for account %s", account.id)
                db.rollback()
    finally:
        db.close()
    return total


# ---------- Schemas ----------

class AlertOut(BaseModel):
    id: int
    kind: str
    severity: str
    details: dict
    created_at: datetime
    acknowledged: bool


class MonitoringOut(BaseModel):
    alerts: List[AlertOut]
    last_checked_at: Optional[datetime] = None
    interval_hours: int = CHECK_INTERVAL_SECONDS // 3600


# ---------- Endpoints ----------

@router.get("", response_model=MonitoringOut)
async def get_monitoring(
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Находки и время последней проверки.

    Открытие экрана заодно запускает проверку: между обходами могли прийти
    новые данные, и показывать вчерашнюю картину человеку, который смотрит
    сейчас, незачем. Вызовов модели здесь нет — проверка стоит запроса к
    своей же базе.
    """
    if not has_feature(current, FEATURE_MONITORING):
        raise HTTPException(status_code=402, detail="Функция доступна на тарифе Pro")
    run_for_account(db, current)
    # Фраза для главной пересобирается здесь, а не в /health/summary: главная
    # не должна ждать модель ради одной карточки.
    await build_digest(db, current)

    since = datetime.utcnow() - timedelta(days=ALERT_TTL_DAYS)
    rows = (db.query(HealthAlert)
            .filter(HealthAlert.patient_account_id == current.id,
                    HealthAlert.created_at >= since)
            .order_by(HealthAlert.created_at.desc())
            .all())
    run = (db.query(MonitoringRun)
           .filter(MonitoringRun.patient_account_id == current.id).first())
    return MonitoringOut(
        alerts=[AlertOut(id=a.id, kind=a.kind, severity=a.severity, details=a.details or {},
                         created_at=a.created_at, acknowledged=a.acknowledged_at is not None)
                for a in rows],
        last_checked_at=run.checked_at if run else None,
    )


@router.post("/{alert_id}/ack", status_code=204)
async def acknowledge(
    alert_id: int,
    current: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """«Прочитано» — находка уходит из активных, но остаётся в истории."""
    if not has_feature(current, FEATURE_MONITORING):
        raise HTTPException(status_code=402, detail="Функция доступна на тарифе Pro")
    row = db.query(HealthAlert).filter(
        HealthAlert.id == alert_id,
        HealthAlert.patient_account_id == current.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Находка не найдена")
    if row.acknowledged_at is None:
        row.acknowledged_at = datetime.utcnow()
        db.commit()
        # Набор находок изменился — старая фраза описывает то, что пациент
        # уже прочитал.
        await build_digest(db, current)


def tier_allows_monitoring(account: PatientAccount) -> bool:
    """Отдельная функция, чтобы условие «Pro и не истёк» не расползлось копиями."""
    return resolve_tier(account) == PRO and has_feature(account, FEATURE_MONITORING)


# ---------- фраза для главной ----------

# Правило формулирует сухо и по одной находке за раз. На главной такой текст
# читается как тревога, а число в нём бесполезно: сделать с ним в этот момент
# нечего, кроме как испугаться. Модель пересказывает весь набор одной фразой.
_DIGEST_PROMPT = """Ты — помощник клиники. Тебе дают список того, что автоматическая проверка
заметила в данных пациента (измерения сахара, результаты анализов, которые он внёс сам).

Напиши ОДНУ спокойную фразу для пациента — её увидят на главном экране приложения.

Строго:
- одно-два предложения, не длиннее 200 символов;
- никаких чисел с единицами измерения (не пиши «2.8 ммоль/л», «13 ммоль/л»);
- никакого диагноза, никаких причин, никаких советов по лечению, дозам и питанию;
- не пугай и не успокаивай: просто скажи, что заметили, и что это стоит показать врачу;
- обращение на «вы», обычные слова, без медицинских терминов;
- ответь только текстом фразы, без кавычек и пояснений.
"""

# Числа с десятичной частью и единицы измерения на главной не нужны — ровно
# то, из-за чего карточка и выглядела пугающей.
_DIGEST_NUMBERS = re.compile(r"\d+[.,]\d|ммоль|mmol|ммол")


def _digest_key(alerts: List[HealthAlert]) -> str:
    return ",".join(str(a.id) for a in sorted(alerts, key=lambda a: a.id))


def _digest_facts(alerts: List[HealthAlert]) -> str:
    lines = []
    for a in alerts:
        d = a.details or {}
        if a.kind == "glucose_low":
            lines.append(f"измерение сахара оказалось низким ({d.get('mmol')} ммоль/л)")
        elif a.kind == "glucose_high_streak":
            lines.append(f"{d.get('count', 3)} измерения сахара подряд оказались высокими")
        elif a.kind == "glucose_silent":
            lines.append(f"дневник сахара не пополнялся {d.get('days', 0)} дней")
        elif a.kind == "lab_out_of_range":
            tests = ", ".join(str(t) for t in (d.get("tests") or []))
            lines.append(f"в результатах анализа вне нормы лаборатории: {tests}")
    return "\n".join(f"- {line}" for line in lines)


async def build_digest(db: Session, account: PatientAccount) -> Optional[str]:
    """Собрать (или взять из кэша) фразу об активных находках.

    None — фразы нет: модель недоступна, ответ не прошёл проверку или
    находок не осталось. Клиент в этом случае показывает текст правила:
    молчание хуже сухой формулировки.
    """
    active = (db.query(HealthAlert)
              .filter(HealthAlert.patient_account_id == account.id,
                      HealthAlert.acknowledged_at.is_(None))
              .all())
    row = (db.query(MonitoringDigest)
           .filter(MonitoringDigest.patient_id == account.id).first())
    if not active:
        if row is not None:
            db.delete(row)
            db.commit()
        return None

    key = _digest_key(active)
    if row is not None and row.source_key == key:
        return row.text

    language = {"ru": "русском", "tj": "таджикском", "en": "английском"}.get(
        account.language_pref, "русском")
    try:
        # Одна фраза на 200 токенов из готовых фактов, да ещё и с
        # пост-фильтром ниже: он отвергает цифры, диагнозы и всё длиннее
        # 240 знаков. Клиническое суждение здесь делают правила, а не модель.
        raw = await _llm_call(
            _DIGEST_PROMPT,
            f"Язык ответа: {language}.\nЧто заметила проверка:\n{_digest_facts(active)}",
            max_tokens=200,
            tier=LIGHT,
        )
    except Exception:
        log.exception("monitoring digest failed for account %s", account.id)
        return row.text if row is not None else None

    text = " ".join((raw or "").split()).strip('"«» ')
    # Проверки не про стиль: длинный текст не поместится в карточку, а
    # запрещённые обороты — это диагноз, которого правило не ставило.
    if not text or len(text) > 240 or _DIGEST_NUMBERS.search(text) or _FORBIDDEN.search(text):
        log.warning("monitoring digest rejected for account %s", account.id)
        return row.text if row is not None else None

    if row is None:
        row = MonitoringDigest(patient_id=account.id, source_key=key, text=text)
        db.add(row)
    else:
        row.source_key = key
        row.text = text
        row.created_at = datetime.utcnow()
    db.commit()
    return text
