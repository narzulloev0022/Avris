"""Сводка «Здоровье»: что попадает в карточку на главной и что — нет."""
from datetime import datetime, timedelta

import pytest

from database import SessionLocal
from models import GlucoseReading, HealthAlert, NutritionEntry, PatientAccount
from patient_auth import create_patient_access_token


def _account(db, phone, tier):
    account = PatientAccount(phone=phone, avris_patient_id=f"AV-SUM-{phone[-4:]}",
                             full_name="Сводка Проба", subscription_tier=tier)
    if tier != "free":
        account.subscription_expires_at = datetime.utcnow() + timedelta(days=30)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _token(account):
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


@pytest.mark.parametrize("tier,nutrition,diabetes,monitoring", [
    ("free", False, False, False),
    ("plus", False, False, False),
    ("pro", True, True, True),
])
def test_rights_are_reported_per_part(client, tier, nutrition, diabetes, monitoring):
    """Общего 402 здесь нет: сводка говорит, что доступно, а что нет."""
    db = SessionLocal()
    try:
        account = _account(db, f"+99294000{abs(hash(tier)) % 9000 + 1000}", tier)
        headers = _token(account)
    finally:
        db.close()
    body = client.get("/api/patient/health/summary", headers=headers).json()
    assert body["has_nutrition"] is nutrition
    assert body["has_diabetes"] is diabetes
    assert body["has_monitoring"] is monitoring


def test_empty_day_reports_nothing_rather_than_zero(client):
    """Ноль калорий и «сегодня ещё не ели» — разные вещи."""
    db = SessionLocal()
    try:
        account = _account(db, "+992940001111", "pro")
        headers = _token(account)
    finally:
        db.close()
    body = client.get("/api/patient/health/summary", headers=headers).json()
    assert body["kcal_today"] is None
    assert body["glucose_mmol"] is None
    assert body["active_alerts"] == 0


def test_today_totals_and_last_reading(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992940002222", "pro")
        now = datetime.utcnow()
        db.add(NutritionEntry(patient_account_id=account.id, eaten_at=now, title="Завтрак",
                              items=[], kcal=420))
        db.add(NutritionEntry(patient_account_id=account.id, eaten_at=now, title="Обед",
                              items=[], kcal=610))
        # Вчерашняя еда в сегодняшний итог попасть не должна.
        db.add(NutritionEntry(patient_account_id=account.id,
                              eaten_at=now - timedelta(days=1), title="Вчера",
                              items=[], kcal=900))
        db.add(GlucoseReading(patient_account_id=account.id, mmol=8.2, context="fasting",
                              taken_at=now - timedelta(hours=2)))
        db.add(GlucoseReading(patient_account_id=account.id, mmol=5.5, context="fasting",
                              taken_at=now - timedelta(hours=20)))
        db.add(HealthAlert(patient_account_id=account.id, kind="glucose_low",
                           severity="attention", dedup_key="s1", details={}))
        db.add(HealthAlert(patient_account_id=account.id, kind="lab_out_of_range",
                           severity="attention", dedup_key="s2", details={},
                           acknowledged_at=now))
        db.commit()
        headers = _token(account)
    finally:
        db.close()

    body = client.get("/api/patient/health/summary", headers=headers).json()
    assert body["kcal_today"] == 1030
    assert body["glucose_mmol"] == 8.2
    assert body["glucose_mark"] == "high"
    # Прочитанная находка активной не считается.
    assert body["active_alerts"] == 1


def test_stale_reading_is_not_shown_as_current(client):
    """Позавчерашний сахар на главной — архив, а не состояние."""
    db = SessionLocal()
    try:
        account = _account(db, "+992940003333", "pro")
        db.add(GlucoseReading(patient_account_id=account.id, mmol=6.0, context="fasting",
                              taken_at=datetime.utcnow() - timedelta(days=3)))
        db.commit()
        headers = _token(account)
    finally:
        db.close()
    assert client.get("/api/patient/health/summary",
                      headers=headers).json()["glucose_mmol"] is None
