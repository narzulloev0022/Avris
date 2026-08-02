"""Носимые устройства: привязка по коду, приём измерений, границы.

Главный риск здесь не в том, что измерение не дойдёт, а в том, что дойдёт
мусор: браслет с севшей батареей или сбитыми часами присылает пульс 0 и
завтрашнюю дату, и это попадает в медкарту, по которой врач принимает
решение. Поэтому границы и дедупликация проверяются наравне с самим приёмом.
"""
from datetime import datetime, timedelta

from database import SessionLocal
from models import DeviceMeasurement, PatientAccount, PatientDevice
from patient_auth import create_patient_access_token


def _account(db, suffix):
    account = PatientAccount(phone=f"+9929450{suffix}", avris_patient_id=f"AV-DEV-{suffix}",
                             full_name="Браслет Проба")
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _headers(account):
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


def _pair(client, headers, external_id="SN-0001", vendor="avris_band"):
    code = client.post("/api/patient/devices/pair-code", headers=headers).json()["code"]
    return client.post("/api/patient/devices/claim", json={
        "code": code, "external_id": external_id, "vendor": vendor,
        "model": "Avris Band 1", "name": "Мой браслет",
    }).json()


def test_pair_then_send_then_read(client):
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0001"))
    finally:
        db.close()

    claimed = _pair(client, headers)
    assert claimed["token"]
    # Прошивке не нужно зашивать список показателей у себя.
    assert "heart_rate" in claimed["accepted_metrics"]

    device_headers = {"Authorization": f"Bearer {claimed['token']}"}
    now = datetime.utcnow().replace(microsecond=0)
    body = {"measurements": [
        {"kind": "heart_rate", "value": 71, "taken_at": (now - timedelta(minutes=5)).isoformat()},
        {"kind": "spo2", "value": 97, "taken_at": (now - timedelta(minutes=5)).isoformat()},
        {"kind": "steps", "value": 8421, "taken_at": (now - timedelta(hours=1)).isoformat()},
    ]}
    out = client.post("/api/patient/devices/measurements", json=body, headers=device_headers).json()
    assert (out["accepted"], out["duplicates"], out["rejected"]) == (3, 0, 0)

    metrics = {m["kind"]: m for m in
               client.get("/api/patient/devices/metrics", headers=headers).json()}
    assert metrics["heart_rate"]["value"] == 71
    assert metrics["heart_rate"]["unit"] == "уд/мин"

    devices = client.get("/api/patient/devices", headers=headers).json()
    assert len(devices) == 1
    assert devices[0]["name"] == "Мой браслет"
    assert devices[0]["last_sync_at"] is not None


def test_resend_of_the_same_hour_is_not_a_duplicate_row(client):
    """Браслет, потерявший связь, дошлёт тот же час заново."""
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0002"))
    finally:
        db.close()
    claimed = _pair(client, headers, external_id="SN-0002")
    device_headers = {"Authorization": f"Bearer {claimed['token']}"}
    at = (datetime.utcnow() - timedelta(hours=2)).replace(microsecond=0).isoformat()
    body = {"measurements": [{"kind": "heart_rate", "value": 64, "taken_at": at}]}

    assert client.post("/api/patient/devices/measurements", json=body,
                       headers=device_headers).json()["accepted"] == 1
    second = client.post("/api/patient/devices/measurements", json=body,
                         headers=device_headers).json()
    assert (second["accepted"], second["duplicates"]) == (0, 1)


def test_garbage_is_refused(client):
    """Пульс 0, температура 200 и завтрашняя дата в медкарту не попадают."""
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0003"))
    finally:
        db.close()
    claimed = _pair(client, headers, external_id="SN-0003")
    device_headers = {"Authorization": f"Bearer {claimed['token']}"}
    now = datetime.utcnow()
    body = {"measurements": [
        {"kind": "heart_rate", "value": 0, "taken_at": now.isoformat()},
        {"kind": "body_temperature", "value": 200, "taken_at": now.isoformat()},
        {"kind": "heart_rate", "value": 70, "taken_at": (now + timedelta(days=1)).isoformat()},
        {"kind": "stress_index", "value": 42, "taken_at": now.isoformat()},
    ]}
    out = client.post("/api/patient/devices/measurements", json=body,
                      headers=device_headers).json()
    assert (out["accepted"], out["rejected"]) == (0, 4)


def test_re_pairing_the_same_device_does_not_create_a_second(client):
    """Пациент сбросил браслет — строка та же, токен новый."""
    db = SessionLocal()
    try:
        account = _account(db, "0004")
        headers = _headers(account)
        account_id = account.id
    finally:
        db.close()

    first = _pair(client, headers, external_id="SN-0004")
    second = _pair(client, headers, external_id="SN-0004")
    assert first["device_id"] == second["device_id"]
    assert first["token"] != second["token"]

    db = SessionLocal()
    try:
        assert db.query(PatientDevice).filter(
            PatientDevice.patient_account_id == account_id).count() == 1
    finally:
        db.close()
    # Старый токен больше не пишет.
    assert client.post("/api/patient/devices/measurements",
                       json={"measurements": []},
                       headers={"Authorization": f"Bearer {first['token']}"}).status_code == 401


def test_code_works_once(client):
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0005"))
    finally:
        db.close()
    code = client.post("/api/patient/devices/pair-code", headers=headers).json()["code"]
    payload = {"code": code, "external_id": "SN-0005", "vendor": "avris_band"}
    assert client.post("/api/patient/devices/claim", json=payload).status_code == 200
    payload["external_id"] = "SN-0005-B"
    assert client.post("/api/patient/devices/claim", json=payload).status_code == 404


def test_unpaired_device_cannot_write_but_history_stays(client):
    """Отвязка — не стирание медкарты."""
    db = SessionLocal()
    try:
        account = _account(db, "0006")
        headers = _headers(account)
        account_id = account.id
    finally:
        db.close()
    claimed = _pair(client, headers, external_id="SN-0006")
    device_headers = {"Authorization": f"Bearer {claimed['token']}"}
    client.post("/api/patient/devices/measurements", json={"measurements": [
        {"kind": "heart_rate", "value": 68,
         "taken_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat()}]},
        headers=device_headers)

    assert client.delete(f"/api/patient/devices/{claimed['device_id']}",
                         headers=headers).status_code == 204
    assert client.post("/api/patient/devices/measurements", json={"measurements": []},
                       headers=device_headers).status_code == 401
    assert client.get("/api/patient/devices", headers=headers).json() == []

    db = SessionLocal()
    try:
        assert db.query(DeviceMeasurement).filter(
            DeviceMeasurement.patient_account_id == account_id).count() == 1
    finally:
        db.close()


def test_patient_token_is_not_a_device_token(client):
    """У пациента и у браслета разные права: пациентским токеном писать нельзя."""
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0007"))
    finally:
        db.close()
    assert client.post("/api/patient/devices/measurements", json={"measurements": []},
                       headers=headers).status_code == 401


def test_measurements_of_another_patient_are_invisible(client):
    db = SessionLocal()
    try:
        mine = _account(db, "0008")
        other = _account(db, "0009")
        mine_headers, other_headers = _headers(mine), _headers(other)
    finally:
        db.close()
    claimed = _pair(client, mine_headers, external_id="SN-0008")
    client.post("/api/patient/devices/measurements", json={"measurements": [
        {"kind": "spo2", "value": 98,
         "taken_at": (datetime.utcnow() - timedelta(minutes=2)).isoformat()}]},
        headers={"Authorization": f"Bearer {claimed['token']}"})

    assert client.get("/api/patient/devices/metrics", headers=other_headers).json() == []
    assert client.get("/api/patient/devices", headers=other_headers).json() == []
