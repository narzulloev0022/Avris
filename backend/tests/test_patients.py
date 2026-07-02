"""Patients CRUD + doctor scoping (a doctor must never see another's patients)."""
from conftest import auth_headers


def _mk_patient(client, doctor, **overrides):
    payload = {
        "full_name": "Тестова П.П.",
        "age": 45,
        "gender": "Ж",
        "ward": "Терапия T1",
        "diagnoses": ["Гипертония"],
        "allergies": ["Пенициллин"],
        "avris_score": 72,
    }
    payload.update(overrides)
    r = client.post("/api/patients/", json=payload, headers=auth_headers(doctor))
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_get_patient(client, doctor):
    p = _mk_patient(client, doctor)
    assert p["full_name"] == "Тестова П.П."
    assert p["diagnoses"] == ["Гипертония"]

    r = client.get(f"/api/patients/{p['id']}", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert r.json()["id"] == p["id"]


def test_list_patients(client, doctor):
    _mk_patient(client, doctor, full_name="Списков А.А.")
    r = client.get("/api/patients/", headers=auth_headers(doctor))
    assert r.status_code == 200
    names = [x["full_name"] for x in r.json()]
    assert "Списков А.А." in names


def test_update_patient(client, doctor):
    p = _mk_patient(client, doctor)
    r = client.put(
        f"/api/patients/{p['id']}",
        json={"avris_score": 38, "status": "critical"},
        headers=auth_headers(doctor),
    )
    assert r.status_code == 200
    assert r.json()["avris_score"] == 38
    assert r.json()["status"] == "critical"


def test_soft_delete_patient(client, doctor):
    p = _mk_patient(client, doctor, full_name="Удалёнов У.У.")
    r = client.delete(f"/api/patients/{p['id']}", headers=auth_headers(doctor))
    assert r.status_code == 204
    # Soft-deleted → not retrievable, not listed
    assert client.get(f"/api/patients/{p['id']}", headers=auth_headers(doctor)).status_code == 404
    names = [x["full_name"] for x in client.get("/api/patients/", headers=auth_headers(doctor)).json()]
    assert "Удалёнов У.У." not in names


def test_doctor_scoping(client, doctor, second_doctor):
    p = _mk_patient(client, doctor, full_name="Чужой П.П.")
    # Backend contract (CLAUDE.md): foreign data answers 404 or 403
    r = client.get(f"/api/patients/{p['id']}", headers=auth_headers(second_doctor))
    assert r.status_code in (403, 404)
    r = client.put(
        f"/api/patients/{p['id']}",
        json={"avris_score": 1},
        headers=auth_headers(second_doctor),
    )
    assert r.status_code in (403, 404)


def test_patients_require_auth(client):
    assert client.get("/api/patients/").status_code == 401
