"""Диабет-контроль: классификация измерений, сводка, тариф и границы.

Сводка — самое опасное место функции: неверный процент «в цели» или
пропущенная гипогликемия выглядят как знание и читаются как знание.
"""
from datetime import datetime, timedelta

import pytest

from database import SessionLocal
from models import GlucoseReading, PatientAccount
from patient_auth import create_patient_access_token
from patient_glucose import classify, summarize


# ---------- классификация ----------

@pytest.mark.parametrize("mmol,context,expected", [
    (3.2, "fasting", "low"),
    (3.8, "after_meal", "low"),      # гипогликемия не зависит от обстоятельств
    (5.5, "fasting", "in_range"),
    (7.5, "fasting", "high"),        # натощак цель до 7.0
    (7.5, "after_meal", "in_range"), # после еды те же 7.5 — норма
    (12.0, "after_meal", "high"),
    (9.0, "bedtime", "high"),
    (6.0, "неизвестно", "in_range"), # незнакомый контекст → общий ориентир
])
def test_classify(mmol, context, expected):
    assert classify(mmol, context) == expected


# ---------- сводка ----------

def test_empty_summary_says_nothing():
    s = summarize([])
    assert s["count"] == 0 and s["average"] is None and s["enough_data"] is False


def test_two_readings_are_not_enough_for_percent():
    s = summarize([{"mmol": 5.0, "context": "fasting"}, {"mmol": 6.0, "context": "fasting"}])
    assert s["count"] == 2
    assert s["average"] == 5.5
    assert s["in_range_percent"] is None, "процент по двум точкам — совпадение, не знание"
    assert "not_enough_data" in s["flags"]


def test_percent_and_extremes():
    s = summarize([
        {"mmol": 5.0, "context": "fasting"},
        {"mmol": 9.0, "context": "fasting"},   # high
        {"mmol": 6.0, "context": "fasting"},
        {"mmol": 6.5, "context": "fasting"},
    ])
    assert s["in_range_percent"] == 75
    assert s["highs"] == 1 and s["lows"] == 0
    assert s["min"] == 5.0 and s["max"] == 9.0


def test_hypoglycemia_is_flagged():
    s = summarize([{"mmol": 3.5, "context": "random"},
                   {"mmol": 6.0, "context": "random"},
                   {"mmol": 5.5, "context": "random"}])
    assert "low" in s["flags"] and s["lows"] == 1


def test_very_low_outranks_plain_low():
    s = summarize([{"mmol": 2.7, "context": "random"},
                   {"mmol": 6.0, "context": "random"},
                   {"mmol": 5.5, "context": "random"}])
    assert "very_low" in s["flags"] and "low" not in s["flags"]


def test_three_recent_highs_in_a_row_are_flagged():
    s = summarize([{"mmol": 11.0, "context": "fasting"},
                   {"mmol": 12.0, "context": "fasting"},
                   {"mmol": 10.5, "context": "fasting"},
                   {"mmol": 5.5, "context": "fasting"}])
    assert "high_streak" in s["flags"]


def test_old_highs_do_not_trigger_the_streak():
    """Три высоких неделю назад и норма сегодня — это не «сейчас высоко»."""
    s = summarize([{"mmol": 5.5, "context": "fasting"},
                   {"mmol": 11.0, "context": "fasting"},
                   {"mmol": 12.0, "context": "fasting"},
                   {"mmol": 10.5, "context": "fasting"}])
    assert "high_streak" not in s["flags"]


# ---------- доступ и границы ----------

def _account(db, phone, tier):
    account = PatientAccount(phone=phone, avris_patient_id=f"AV-GLU-{phone[-4:]}",
                             full_name="Диабет Проба", subscription_tier=tier)
    if tier != "free":
        account.subscription_expires_at = datetime.utcnow() + timedelta(days=30)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _token(account):
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


@pytest.mark.parametrize("tier,expected", [("free", 402), ("plus", 402), ("pro", 200)])
def test_diary_is_pro_only(client, tier, expected):
    db = SessionLocal()
    try:
        account = _account(db, f"+99292000{abs(hash(tier)) % 9000 + 1000}", tier)
        headers = _token(account)
    finally:
        db.close()
    assert client.get("/api/patient/glucose", headers=headers).status_code == expected


def test_add_list_and_delete(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992920001111", "pro")
        headers = _token(account)
    finally:
        db.close()

    created = client.post("/api/patient/glucose", headers=headers,
                          json={"mmol": 8.4, "context": "fasting"})
    assert created.status_code == 201, created.text
    assert created.json()["mark"] == "high"
    rid = created.json()["id"]

    body = client.get("/api/patient/glucose", headers=headers).json()
    assert body["count"] == 1 and body["readings"][0]["mmol"] == 8.4

    assert client.delete(f"/api/patient/glucose/{rid}", headers=headers).status_code == 204
    assert client.get("/api/patient/glucose", headers=headers).json()["count"] == 0


@pytest.mark.parametrize("mmol", [0.5, 40.0, -3])
def test_impossible_values_are_refused(client, mmol):
    """Ввели мг/дл или промахнулись по клавише — в дневник это попасть не должно."""
    db = SessionLocal()
    try:
        account = _account(db, f"+9929200022{abs(int(mmol * 10)) % 90 + 10}", "pro")
        headers = _token(account)
    finally:
        db.close()
    assert client.post("/api/patient/glucose", headers=headers,
                       json={"mmol": mmol}).status_code == 422


def test_unknown_context_falls_back_to_random(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992920003333", "pro")
        headers = _token(account)
    finally:
        db.close()
    resp = client.post("/api/patient/glucose", headers=headers,
                       json={"mmol": 6.0, "context": "после бани"})
    assert resp.status_code == 201
    assert resp.json()["context"] == "random"


def test_readings_are_isolated_between_patients(client):
    db = SessionLocal()
    try:
        mine = _account(db, "+992920004444", "pro")
        other = _account(db, "+992920005555", "pro")
        db.add(GlucoseReading(patient_account_id=other.id, taken_at=datetime.utcnow(),
                              mmol=15.0, context="fasting"))
        db.commit()
        headers = _token(mine)
    finally:
        db.close()
    assert client.get("/api/patient/glucose", headers=headers).json()["count"] == 0


def test_cannot_delete_someone_elses_reading(client):
    db = SessionLocal()
    try:
        mine = _account(db, "+992920006666", "pro")
        other = _account(db, "+992920007777", "pro")
        row = GlucoseReading(patient_account_id=other.id, taken_at=datetime.utcnow(),
                             mmol=6.0, context="fasting")
        db.add(row)
        db.commit()
        rid = row.id
        headers = _token(mine)
    finally:
        db.close()

    assert client.delete(f"/api/patient/glucose/{rid}", headers=headers).status_code == 404
    db = SessionLocal()
    try:
        assert db.query(GlucoseReading).filter_by(id=rid).count() == 1
    finally:
        db.close()


def test_period_window_excludes_older_readings(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992920008888", "pro")
        db.add(GlucoseReading(patient_account_id=account.id, mmol=6.0, context="fasting",
                              taken_at=datetime.utcnow() - timedelta(days=3)))
        db.add(GlucoseReading(patient_account_id=account.id, mmol=7.0, context="fasting",
                              taken_at=datetime.utcnow() - timedelta(days=40)))
        db.commit()
        headers = _token(account)
    finally:
        db.close()

    assert client.get("/api/patient/glucose?days=14", headers=headers).json()["count"] == 1
    assert client.get("/api/patient/glucose?days=90", headers=headers).json()["count"] == 2
