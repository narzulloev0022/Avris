"""Отметка самочувствия: одна в день, задним числом только по памяти.

Ценность этой записи в том, что она субъективна и принадлежит пациенту.
Поэтому здесь проверяется не «правильность» уровня, а то, что дневник
остаётся дневником: одна отметка на день, будущим днём не отмечают, а
слишком давним — не помнят.
"""
from datetime import date, timedelta

from database import SessionLocal
from models import PatientAccount, PatientCheckin
from patient_auth import create_patient_access_token


def _account(db, suffix):
    account = PatientAccount(phone=f"+9929460{suffix}", avris_patient_id=f"AV-CHK-{suffix}",
                             full_name="Самочувствие Проба")
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _headers(account):
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


def test_mark_and_read_back(client):
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0001"))
    finally:
        db.close()

    out = client.put("/api/patient/checkins",
                     json={"level": 4, "note": "Кашель меньше"}, headers=headers).json()
    assert out["level"] == 4 and out["note"] == "Кашель меньше"
    assert out["day"] == date.today().isoformat()

    assert client.get("/api/patient/checkins", headers=headers).json()[0]["level"] == 4


def test_second_mark_replaces_todays(client):
    """Дневник, который можно вести десять раз в сутки, перестают вести."""
    db = SessionLocal()
    try:
        account = _account(db, "0002")
        headers = _headers(account)
        account_id = account.id
    finally:
        db.close()

    client.put("/api/patient/checkins", json={"level": 2}, headers=headers)
    client.put("/api/patient/checkins", json={"level": 5}, headers=headers)

    db = SessionLocal()
    try:
        rows = db.query(PatientCheckin).filter(
            PatientCheckin.patient_account_id == account_id).all()
        assert len(rows) == 1 and rows[0].level == 5
    finally:
        db.close()


def test_yesterday_is_allowed_but_not_the_future(client):
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0003"))
    finally:
        db.close()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert client.put("/api/patient/checkins", json={"level": 3, "day": yesterday},
                      headers=headers).status_code == 200
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert client.put("/api/patient/checkins", json={"level": 3, "day": tomorrow},
                      headers=headers).status_code == 400


def test_too_old_is_refused(client):
    """Через неделю человек не помнит, как себя чувствовал — это выдумка."""
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0004"))
    finally:
        db.close()
    old = (date.today() - timedelta(days=30)).isoformat()
    assert client.put("/api/patient/checkins", json={"level": 3, "day": old},
                      headers=headers).status_code == 400


def test_level_outside_the_scale_is_refused(client):
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0005"))
    finally:
        db.close()
    for bad in (0, 6, -1):
        assert client.put("/api/patient/checkins", json={"level": bad},
                          headers=headers).status_code == 422


def test_checkins_are_private(client):
    db = SessionLocal()
    try:
        mine_headers = _headers(_account(db, "0006"))
        other_headers = _headers(_account(db, "0007"))
    finally:
        db.close()
    client.put("/api/patient/checkins", json={"level": 5}, headers=mine_headers)
    assert client.get("/api/patient/checkins", headers=other_headers).json() == []
