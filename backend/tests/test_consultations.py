"""Consultations: CRUD, SOAP accuracy counters, PDF export."""
from conftest import auth_headers


def _mk_consultation(client, doctor, **overrides):
    payload = {
        "transcript": "Пациент жалуется на головную боль.",
        "soap_s": "Головная боль 2 дня.",
        "soap_o": "АД 150/95, ЧСС 82.",
        "soap_a": "Артериальная гипертензия.",
        "soap_p": "Амлодипин 5 мг, контроль АД.",
        "language": "ru",
        "duration_seconds": 180,
    }
    payload.update(overrides)
    r = client.post("/api/consultations/", json=payload, headers=auth_headers(doctor))
    assert r.status_code == 201, r.text
    return r.json()


def test_create_list_get(client, doctor):
    c = _mk_consultation(client, doctor)
    assert c["soap_a"] == "Артериальная гипертензия."

    r = client.get("/api/consultations/", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert any(x["id"] == c["id"] for x in r.json())

    r = client.get(f"/api/consultations/{c['id']}", headers=auth_headers(doctor))
    assert r.status_code == 200


def test_accuracy_counters(client, doctor):
    me_before = client.get("/api/auth/me", headers=auth_headers(doctor)).json()
    _mk_consultation(client, doctor, soap_was_edited=False)  # accurate
    _mk_consultation(client, doctor, soap_was_edited=True)   # edited
    _mk_consultation(client, doctor)                          # manual → not counted
    me_after = client.get("/api/auth/me", headers=auth_headers(doctor)).json()
    assert me_after["soap_accurate_count"] == me_before["soap_accurate_count"] + 1
    assert me_after["soap_edited_count"] == me_before["soap_edited_count"] + 1


def test_consultation_scoping(client, doctor, second_doctor):
    c = _mk_consultation(client, doctor)
    r = client.get(f"/api/consultations/{c['id']}", headers=auth_headers(second_doctor))
    assert r.status_code in (403, 404)


def test_pdf_export(client, doctor):
    c = _mk_consultation(client, doctor)
    r = client.get(f"/api/consultations/{c['id']}/pdf", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:5] == b"%PDF-"
