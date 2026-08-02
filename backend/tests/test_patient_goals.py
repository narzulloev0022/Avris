"""Дневные цели и вода.

Проверяется главное: цель ставит человек и может её убрать, а вода
считается одним тапом и не уходит в минус.
"""
from datetime import date

from database import SessionLocal
from models import PatientAccount, WaterIntake
from patient_auth import create_patient_access_token


def _headers(db, suffix):
    account = PatientAccount(phone=f"+9929480{suffix}", avris_patient_id=f"AV-GOL-{suffix}",
                             full_name="Цели Проба")
    db.add(account)
    db.commit()
    db.refresh(account)
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}, account.id


def test_goals_are_set_and_cleared(client):
    db = SessionLocal()
    try:
        headers, _ = _headers(db, "0001")
    finally:
        db.close()

    assert client.get("/api/patient/goals", headers=headers).json() == {
        "steps": None, "water_glasses": None, "kcal": None}

    out = client.put("/api/patient/goals",
                     json={"steps": 8000, "water_glasses": 8}, headers=headers).json()
    assert (out["steps"], out["water_glasses"], out["kcal"]) == (8000, 8, None)

    # Убрать цель — такое же нормальное действие, как поставить.
    out = client.put("/api/patient/goals", json={"steps": None}, headers=headers).json()
    assert out["steps"] is None and out["water_glasses"] == 8


def test_absurd_goal_is_refused(client):
    """Защита от промаха по клавиатуре, а не рекомендация."""
    db = SessionLocal()
    try:
        headers, _ = _headers(db, "0002")
    finally:
        db.close()
    assert client.put("/api/patient/goals", json={"steps": 900000},
                      headers=headers).status_code == 400
    assert client.put("/api/patient/goals", json={"kcal": 10},
                      headers=headers).status_code == 400


def test_water_counts_up_and_down_but_not_below_zero(client):
    db = SessionLocal()
    try:
        headers, account_id = _headers(db, "0003")
    finally:
        db.close()

    for _ in range(3):
        client.post("/api/patient/water", json={"delta": 1}, headers=headers)
    assert client.get("/api/patient/water", headers=headers).json()["glasses"] == 3

    client.post("/api/patient/water", json={"delta": -1}, headers=headers)
    body = client.post("/api/patient/water", json={"delta": -5}, headers=headers).json()
    assert body["glasses"] == 0

    db = SessionLocal()
    try:
        rows = db.query(WaterIntake).filter(
            WaterIntake.patient_account_id == account_id).all()
        # Одна строка на день: человеку важно «сколько сегодня», а не в
        # котором часу был третий стакан.
        assert len(rows) == 1 and rows[0].day == date.today()
    finally:
        db.close()


def test_water_carries_the_goal(client):
    db = SessionLocal()
    try:
        headers, _ = _headers(db, "0004")
    finally:
        db.close()
    client.put("/api/patient/goals", json={"water_glasses": 8}, headers=headers)
    body = client.post("/api/patient/water", json={"delta": 1}, headers=headers).json()
    assert (body["glasses"], body["goal"]) == (1, 8)


def test_goals_and_water_are_private(client):
    db = SessionLocal()
    try:
        mine, _ = _headers(db, "0005")
        other, _ = _headers(db, "0006")
    finally:
        db.close()
    client.put("/api/patient/goals", json={"steps": 9000}, headers=mine)
    client.post("/api/patient/water", json={"delta": 2}, headers=mine)
    assert client.get("/api/patient/goals", headers=other).json()["steps"] is None
    assert client.get("/api/patient/water", headers=other).json()["glasses"] == 0
