"""Медкарта-фундамент: ДР/№ карты, снимок поступления, visit_type консультаций.

Стационарный слой универсален: все новые поля nullable, старые payload'ы
(без них) обязаны работать как раньше.
"""
from datetime import date

from conftest import auth_headers


def _mk_patient(client, doctor, **overrides):
    payload = {"full_name": "Стационаров С.С."}
    payload.update(overrides)
    r = client.post("/api/patients/", json=payload, headers=auth_headers(doctor))
    assert r.status_code == 201, r.text
    return r.json()


# ---------- ДР / № истории болезни ----------

def test_dob_and_record_number_persist(client, doctor):
    p = _mk_patient(
        client, doctor,
        date_of_birth="1961-03-15",
        record_number="ИБ-2026/0417",
    )
    assert p["date_of_birth"] == "1961-03-15"
    assert p["record_number"] == "ИБ-2026/0417"
    # И читается обратно
    r = client.get(f"/api/patients/{p['id']}", headers=auth_headers(doctor))
    assert r.json()["date_of_birth"] == "1961-03-15"


def test_age_computed_from_dob_when_missing(client, doctor):
    dob = date(1961, 3, 15)
    p = _mk_patient(client, doctor, date_of_birth=dob.isoformat())
    today = date.today()
    expected = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    assert p["age"] == expected


def test_explicit_age_wins_over_dob(client, doctor):
    # Врач указал возраст сам — не перетираем вычисленным.
    p = _mk_patient(client, doctor, date_of_birth="1961-03-15", age=70)
    assert p["age"] == 70


def test_update_dob_recomputes_age(client, doctor):
    p = _mk_patient(client, doctor, age=40)
    r = client.put(
        f"/api/patients/{p['id']}",
        json={"date_of_birth": "2000-01-01"},
        headers=auth_headers(doctor),
    )
    assert r.status_code == 200
    today = date.today()
    expected = today.year - 2000 - ((today.month, today.day) < (1, 1))
    assert r.json()["age"] == expected


# ---------- Снимок поступления ----------

def test_admission_snapshot_persists(client, doctor):
    p = _mk_patient(
        client, doctor,
        patient_type="inpatient",
        admission_date="2026-07-10T09:30:00",
        admission_diagnosis="Внебольничная пневмония, средней тяжести",
        admission_status="serious",
        status="watch",  # текущее состояние уже улучшилось
        diagnoses=["Пневмония"],
    )
    assert p["admission_diagnosis"] == "Внебольничная пневмония, средней тяжести"
    assert p["admission_status"] == "serious"
    assert p["admission_date"].startswith("2026-07-10T09:30:00")
    # Снимок поступления живёт отдельно от текущего статуса
    assert p["status"] == "watch"


def test_admission_snapshot_not_touched_by_status_update(client, doctor):
    p = _mk_patient(
        client, doctor,
        patient_type="inpatient",
        admission_status="critical",
        admission_diagnosis="ОКС",
    )
    r = client.put(
        f"/api/patients/{p['id']}",
        json={"status": "stable", "diagnoses": ["ИБС, стабилизирован"]},
        headers=auth_headers(doctor),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "stable"
    assert body["admission_status"] == "critical"
    assert body["admission_diagnosis"] == "ОКС"


def test_legacy_payload_still_works(client, doctor):
    # Старый амбулаторный payload без единого нового поля
    p = _mk_patient(client, doctor, age=45, gender="Ж", diagnoses=["Гипертония"])
    assert p["date_of_birth"] is None
    assert p["record_number"] is None
    assert p["admission_date"] is None
    assert p["admission_diagnosis"] is None
    assert p["admission_status"] is None
    assert p["patient_type"] == "outpatient"


# ---------- visit_type консультаций ----------

def _mk_consultation(client, doctor, **overrides):
    payload = {"soap_s": "Жалобы на кашель", "language": "ru"}
    payload.update(overrides)
    r = client.post("/api/consultations/", json=payload, headers=auth_headers(doctor))
    assert r.status_code == 201, r.text
    return r.json()


def test_consultation_default_visit_type(client, doctor):
    c = _mk_consultation(client, doctor)
    assert c["visit_type"] == "visit"


def test_consultation_primary_and_daily(client, doctor):
    p = _mk_patient(client, doctor, patient_type="inpatient")
    primary = _mk_consultation(client, doctor, patient_id=p["id"], visit_type="primary")
    daily = _mk_consultation(client, doctor, patient_id=p["id"], visit_type="daily")
    assert primary["visit_type"] == "primary"
    assert daily["visit_type"] == "daily"

    # Оба видны в выборке по пациенту (фундамент таймлайна истории болезни)
    r = client.get(
        f"/api/consultations/?patient_id={p['id']}",
        headers=auth_headers(doctor),
    )
    assert r.status_code == 200
    types = [c["visit_type"] for c in r.json()]
    assert "primary" in types and "daily" in types
