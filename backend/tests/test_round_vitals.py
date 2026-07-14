"""Витальные ночного обхода дописываются в Patient.vitals (питают ICU-монитор)."""
from conftest import auth_headers


def _mk_patient(client, doctor, **overrides):
    payload = {"full_name": "Мониторов М.М.", "patient_type": "inpatient",
               "department": "icu", "ward": "3"}
    payload.update(overrides)
    r = client.post("/api/patients/", json=payload, headers=auth_headers(doctor))
    assert r.status_code == 201, r.text
    return r.json()


def _round(client, doctor, pid, vitals):
    r = client.post("/api/night-rounds/",
                    json={"patient_id": pid, "ward": "3", "vitals": vitals,
                          "notes": "Осмотр в 03:00"},
                    headers=auth_headers(doctor))
    assert r.status_code == 201, r.text
    return r.json()


def test_round_vitals_merge_into_patient(client, doctor):
    p = _mk_patient(client, doctor)
    _round(client, doctor, p["id"],
           {"pulse": 88, "bp": "150/90", "temp": 37.5, "spo2": 94})
    got = client.get(f"/api/patients/{p['id']}", headers=auth_headers(doctor)).json()
    v = got["vitals"]
    assert v["ЧСС"][-1] == 88
    assert v["АД"][-1] == 150       # систолическое из "150/90"
    assert v["T°C"][-1] == 37.5
    assert v["SpO₂"][-1] == 94


def test_round_vitals_append_and_cap(client, doctor):
    # Стартовые 7 точек — новый обход должен вытеснить самую старую
    p = _mk_patient(client, doctor, vitals={"ЧСС": [80, 81, 82, 83, 84, 85, 86]})
    _round(client, doctor, p["id"], {"pulse": 99})
    v = client.get(f"/api/patients/{p['id']}", headers=auth_headers(doctor)).json()["vitals"]
    assert v["ЧСС"] == [81, 82, 83, 84, 85, 86, 99]
    assert len(v["ЧСС"]) == 7


def test_round_partial_and_garbage_vitals(client, doctor):
    p = _mk_patient(client, doctor)
    # Только пульс + мусорное значение температуры — мусор игнорируется
    _round(client, doctor, p["id"], {"pulse": 72, "temp": "не измерялась"})
    v = client.get(f"/api/patients/{p['id']}", headers=auth_headers(doctor)).json()["vitals"]
    assert v["ЧСС"] == [72]
    assert "T°C" not in v


def test_round_foreign_patient_vitals_ignored(client, doctor, second_doctor):
    """Чужой patient_id: обход сохраняется, но чужая карта не тронута."""
    p = _mk_patient(client, doctor)
    r = client.post("/api/night-rounds/",
                    json={"patient_id": p["id"], "vitals": {"pulse": 55}},
                    headers=auth_headers(second_doctor))
    assert r.status_code == 201
    v = client.get(f"/api/patients/{p['id']}", headers=auth_headers(doctor)).json()["vitals"]
    assert not v or "ЧСС" not in (v or {})


def test_round_without_patient_still_saves(client, doctor):
    r = client.post("/api/night-rounds/",
                    json={"ward": "5", "vitals": {"pulse": 70}, "notes": "Палата без привязки"},
                    headers=auth_headers(doctor))
    assert r.status_code == 201
    assert r.json()["patient_id"] is None
