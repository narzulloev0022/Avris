"""Мониторинг: разбор нормы, правила, дедупликация и тариф.

Главная опасность функции не в том, что она промолчит, а в том, что она
заговорит слишком часто: приложение, каждый день сообщающее об одном и том
же, перестают читать — включая тот единственный раз, когда прочитать было
нужно. Поэтому дедупликация проверяется наравне с самими правилами.
"""
from datetime import datetime, timedelta

import pytest

from database import SessionLocal
from models import (GlucoseReading, HealthAlert, LabOrder, MonitoringRun, Patient,
                    PatientAccount, PatientLink, User)
from patient_auth import create_patient_access_token
from patient_monitoring import evaluate, out_of_range, run_for_account


# ---------- разбор нормы лаборатории ----------

@pytest.mark.parametrize("value,rng,expected", [
    ("128", "120–150", False),
    ("9.8", "4.0-9.0", True),
    ("3.0", "4,0 – 9,0", True),
    ("5.1", "до 5.1", False),
    ("5.9", "до 5.1", True),
    ("1.0", "от 1.2", True),
    ("1.5", "≥ 1.2", False),
    # Неразобранная норма — молчание, а не догадка.
    ("5.0", "в пределах референса", None),
    ("5.0", "", None),
    ("не обнаружено", "4.0-9.0", None),
    ("5.0", "9.0-4.0", None),
])
def test_out_of_range(value, rng, expected):
    assert out_of_range(value, rng) is expected


# ---------- правила ----------

def _account(db, phone, tier="pro"):
    account = PatientAccount(phone=phone, avris_patient_id=f"AV-MON-{phone[-4:]}",
                             full_name="Мониторинг Проба", subscription_tier=tier)
    if tier != "free":
        account.subscription_expires_at = datetime.utcnow() + timedelta(days=30)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _reading(db, account, mmol, days_ago=0, context="fasting"):
    row = GlucoseReading(patient_account_id=account.id, mmol=mmol, context=context,
                         taken_at=datetime.utcnow() - timedelta(days=days_ago, minutes=1))
    db.add(row)
    db.commit()
    return row


def _kinds(found):
    return {f["kind"] for f in found}


def test_no_data_no_alerts(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992930000001")
        assert evaluate(db, account, datetime.utcnow()) == []
    finally:
        db.close()


def test_hypoglycemia_within_a_day_is_found(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992930000002")
        _reading(db, account, 3.4)
        found = evaluate(db, account, datetime.utcnow())
        assert "glucose_low" in _kinds(found)
        assert next(f for f in found if f["kind"] == "glucose_low")["severity"] == "attention"
    finally:
        db.close()


def test_very_low_is_urgent(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992930000003")
        _reading(db, account, 2.6)
        found = evaluate(db, account, datetime.utcnow())
        assert next(f for f in found if f["kind"] == "glucose_low")["severity"] == "urgent"
    finally:
        db.close()


def test_old_hypoglycemia_is_not_reported_again(client):
    """Провал трёхдневной давности — не новость сегодняшнего дня."""
    db = SessionLocal()
    try:
        account = _account(db, "+992930000004")
        _reading(db, account, 3.2, days_ago=3)
        assert "glucose_low" not in _kinds(evaluate(db, account, datetime.utcnow()))
    finally:
        db.close()


def test_three_highs_in_a_row(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992930000005")
        for days in (2, 1, 0):
            _reading(db, account, 11.5, days_ago=days)
        assert "glucose_high_streak" in _kinds(evaluate(db, account, datetime.utcnow()))
    finally:
        db.close()


def test_normal_reading_breaks_the_streak(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992930000006")
        for days in (3, 2, 1):
            _reading(db, account, 11.5, days_ago=days)
        _reading(db, account, 5.8, days_ago=0)
        assert "glucose_high_streak" not in _kinds(evaluate(db, account, datetime.utcnow()))
    finally:
        db.close()


def test_silent_diary_is_noticed_only_for_those_who_kept_it(client):
    db = SessionLocal()
    try:
        kept = _account(db, "+992930000007")
        for days in range(10, 15):
            _reading(db, kept, 6.0, days_ago=days)
        assert "glucose_silent" in _kinds(evaluate(db, kept, datetime.utcnow()))

        # Два измерения месяц назад — это не «вёл дневник», и упрекать не за что.
        barely = _account(db, "+992930000008")
        _reading(db, barely, 6.0, days_ago=30)
        _reading(db, barely, 6.1, days_ago=29)
        assert "glucose_silent" not in _kinds(evaluate(db, barely, datetime.utcnow()))
    finally:
        db.close()


def test_fresh_lab_out_of_range_is_found(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992930000009")
        doctor = User(email="mon-doc@avris.test", password_hash="x", full_name="Врач")
        db.add(doctor)
        db.commit()
        db.refresh(doctor)
        patient = Patient(doctor_id=doctor.id, full_name="Пациент", age=50)
        db.add(patient)
        db.commit()
        db.refresh(patient)
        db.add(PatientLink(patient_account_id=account.id, patient_id=patient.id,
                           doctor_id=doctor.id))
        db.add(LabOrder(patient_id=patient.id, doctor_id=doctor.id, tests=["ОАК"],
                        qr_token="mon-probe-token",
                        status="received", received_at=datetime.utcnow() - timedelta(days=1),
                        results={
                            "Гемоглобин": {"value": "128", "range": "120–150"},
                            "Лейкоциты": {"value": "12.4", "range": "4.0-9.0"},
                            "Непонятное": {"value": "5", "range": "в пределах нормы"},
                        }))
        db.commit()

        found = [f for f in evaluate(db, account, datetime.utcnow())
                 if f["kind"] == "lab_out_of_range"]
        assert len(found) == 1
        # Только разобранное и только выбивающееся.
        assert found[0]["details"]["tests"] == ["Лейкоциты"]
    finally:
        db.close()


# ---------- сохранение и дедупликация ----------

def test_repeated_run_does_not_duplicate_alerts(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992930000010")
        _reading(db, account, 3.1)
        first = run_for_account(db, account)
        second = run_for_account(db, account)
        assert first >= 1
        assert second == 0, "повторная проверка не должна плодить копии"
        assert db.query(HealthAlert).filter(
            HealthAlert.patient_account_id == account.id,
            HealthAlert.kind == "glucose_low").count() == 1
    finally:
        db.close()


def test_run_records_the_check_time(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992930000011")
        run_for_account(db, account)
        run = db.query(MonitoringRun).filter(
            MonitoringRun.patient_account_id == account.id).first()
        assert run is not None and run.checked_at is not None
    finally:
        db.close()


# ---------- доступ ----------

def _token(account):
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


@pytest.mark.parametrize("tier,expected", [("free", 402), ("plus", 402), ("pro", 200)])
def test_monitoring_is_pro_only(client, tier, expected):
    db = SessionLocal()
    try:
        account = _account(db, f"+99293100{abs(hash(tier)) % 9000 + 1000}", tier)
        headers = _token(account)
    finally:
        db.close()
    assert client.get("/api/patient/monitoring", headers=headers).status_code == expected


def test_alerts_are_isolated_and_acknowledgeable(client):
    db = SessionLocal()
    try:
        mine = _account(db, "+992930000012")
        other = _account(db, "+992930000013")
        db.add(HealthAlert(patient_account_id=other.id, kind="glucose_low",
                           severity="attention", dedup_key="x", details={}))
        db.commit()
        _reading(db, mine, 3.3)
        headers = _token(mine)
    finally:
        db.close()

    body = client.get("/api/patient/monitoring", headers=headers).json()
    assert len(body["alerts"]) == 1
    assert body["last_checked_at"] is not None
    alert_id = body["alerts"][0]["id"]
    assert body["alerts"][0]["acknowledged"] is False

    assert client.post(f"/api/patient/monitoring/{alert_id}/ack",
                       headers=headers).status_code == 204
    again = client.get("/api/patient/monitoring", headers=headers).json()
    assert again["alerts"][0]["acknowledged"] is True


def test_cannot_acknowledge_someone_elses_alert(client):
    db = SessionLocal()
    try:
        mine = _account(db, "+992930000014")
        other = _account(db, "+992930000015")
        alert = HealthAlert(patient_account_id=other.id, kind="glucose_low",
                            severity="attention", dedup_key="y", details={})
        db.add(alert)
        db.commit()
        alert_id = alert.id
        headers = _token(mine)
    finally:
        db.close()
    assert client.post(f"/api/patient/monitoring/{alert_id}/ack",
                       headers=headers).status_code == 404
