"""Delta — patient read-only access to their own lab results (lab_orders).

Same ownership model as visits: strictly through PatientLink. A patient sees
lab orders on their linked doctor-scoped record, can read results and download
scan files, and can never reach another patient's labs.
"""
import os
import uuid

os.environ.setdefault("PATIENT_DEV_OTP", "424242")

import pytest
from fastapi.testclient import TestClient

DEV_OTP = os.environ["PATIENT_DEV_OTP"]


@pytest.fixture()
def client(db_session):
    from rate_limit import limiter
    limiter.enabled = False
    import main
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def doctor_headers(db_session):
    from auth import create_access_token, hash_password
    from models import User
    doc = User(email="doc@test.tj", password_hash=hash_password("x"),
               full_name="Др. Каримов", is_verified=True, is_approved=True)
    db_session.add(doc)
    db_session.commit()
    return {"Authorization": f"Bearer {create_access_token(doc.id)}"}, doc.id


def _linked_patient(client, doctor_headers, phone="+992906660001"):
    headers, _ = doctor_headers
    client.post("/api/patient/auth/request-otp", json={"contact": phone})
    r = client.post("/api/patient/auth/verify-otp", json={"contact": phone, "code": DEV_OTP})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.put("/api/patient/profile", headers=h, json={"full_name": "Носирова Мехрангез"})
    client.post("/api/patient/consent", headers=h)
    code = client.post("/api/patient/link-code", headers=h).json()["code"]
    pid = client.post("/api/patient-links", headers=headers, json={"code": code}).json()["patient"]["id"]
    return h, pid


def _make_lab_order(db, doctor_id, patient_id, with_file=True):
    from models import LabFile, LabOrder
    o = LabOrder(
        patient_id=patient_id, doctor_id=doctor_id, qr_token=str(uuid.uuid4()),
        tests=["ОАК", "Глюкоза"], status="received",
        results={"Гемоглобин": "140 г/л", "Глюкоза": "5.1 ммоль/л"},
        ai_comment="Показатели в норме.",
    )
    db.add(o)
    db.commit()
    if with_file:
        db.add(LabFile(lab_order_id=o.id, filename="oak.pdf", content_type="application/pdf",
                       result_type="lab", size_bytes=5, data=b"%PDF%"))
        db.commit()
    return o.id


class TestLabAccess:
    def test_linked_patient_lists_own_labs(self, client, doctor_headers, db_session):
        h, pid = _linked_patient(client, doctor_headers)
        _, doc_id = doctor_headers
        _make_lab_order(db_session, doc_id, pid)
        r = client.get("/api/patient/labs", headers=h)
        assert r.status_code == 200
        labs = r.json()
        assert len(labs) == 1
        assert labs[0]["status"] == "received"
        assert labs[0]["tests"] == ["ОАК", "Глюкоза"]
        assert labs[0]["file_count"] == 1
        assert labs[0]["doctor_name"] == "Др. Каримов"

    def test_lab_detail_shows_results(self, client, doctor_headers, db_session):
        h, pid = _linked_patient(client, doctor_headers)
        _, doc_id = doctor_headers
        oid = _make_lab_order(db_session, doc_id, pid)
        r = client.get(f"/api/patient/labs/{oid}", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["results"]["Гемоглобин"] == "140 г/л"
        assert body["ai_comment"] == "Показатели в норме."
        assert len(body["files"]) == 1
        assert body["files"][0]["filename"] == "oak.pdf"

    def test_download_lab_file(self, client, doctor_headers, db_session):
        h, pid = _linked_patient(client, doctor_headers)
        _, doc_id = doctor_headers
        oid = _make_lab_order(db_session, doc_id, pid)
        fid = client.get(f"/api/patient/labs/{oid}", headers=h).json()["files"][0]["id"]
        r = client.get(f"/api/patient/labs/{oid}/files/{fid}", headers=h)
        assert r.status_code == 200
        assert r.content == b"%PDF%"

    def test_empty_when_no_links(self, client, doctor_headers):
        _, _ = doctor_headers
        client.post("/api/patient/auth/request-otp", json={"contact": "+992906660055"})
        r = client.post("/api/patient/auth/verify-otp",
                        json={"contact": "+992906660055", "code": DEV_OTP})
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        assert client.get("/api/patient/labs", headers=h).json() == []


class TestLabIsolation:
    def test_patient_cannot_read_foreign_labs(self, client, doctor_headers, db_session):
        h_a, pid_a = _linked_patient(client, doctor_headers, phone="+992906660001")
        _, doc_id = doctor_headers
        oid_a = _make_lab_order(db_session, doc_id, pid_a)

        h_b, _ = _linked_patient(client, doctor_headers, phone="+992906660002")
        assert client.get("/api/patient/labs", headers=h_b).json() == []
        assert client.get(f"/api/patient/labs/{oid_a}", headers=h_b).status_code == 404
        fid = client.get(f"/api/patient/labs/{oid_a}", headers=h_a).json()["files"][0]["id"]
        assert client.get(f"/api/patient/labs/{oid_a}/files/{fid}",
                          headers=h_b).status_code == 404

    def test_doctor_token_rejected(self, client, doctor_headers):
        headers, _ = doctor_headers
        assert client.get("/api/patient/labs", headers=headers).status_code == 401
