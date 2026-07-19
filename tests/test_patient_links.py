"""T5 — QR/code linking: the junction where the two doors meet.

Patient (consented) issues a short-lived one-time code → doctor previews the
profile → confirms → a doctor-scoped ``patients`` row is created prefilled
from the account profile and bridged via ``patient_links``. Name fallback:
link by Avris Patient ID + full-name confirmation, always manual.
"""
import os

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
    return {"Authorization": f"Bearer {create_access_token(doc.id)}"}


def _patient(client, phone="+992907770001", consent=True, profile=True):
    client.post("/api/patient/auth/request-otp", json={"contact": phone})
    r = client.post("/api/patient/auth/verify-otp", json={"contact": phone, "code": DEV_OTP})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    if profile:
        client.put("/api/patient/profile", headers=h, json={
            "full_name": "Носирова Мехрангез",
            "date_of_birth": "1992-03-14",
            "gender": "female",
            "height": 164.0,
            "weight": 58.5,
            "blood_type": "A(II) Rh+",
            "chronic_conditions": ["Гипотиреоз"],
            "allergies": ["Пенициллин"],
            "medications": ["Левотироксин 50мкг"],
        })
    if consent:
        client.post("/api/patient/consent", headers=h)
    return h, r.json()["account"]["avris_patient_id"]


def _issue_code(client, h):
    r = client.post("/api/patient/link-code", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


class TestIssueLinkCode:
    def test_requires_consent(self, client):
        h, _ = _patient(client, consent=False)
        assert client.post("/api/patient/link-code", headers=h).status_code == 403

    def test_issues_code_and_qr_payload(self, client):
        h, _ = _patient(client)
        data = _issue_code(client, h)
        assert len(data["code"]) == 6 and data["code"].isdigit()
        assert data["qr_payload"]
        assert data["expires_at"]

    def test_doctor_token_cannot_issue(self, client, doctor_headers):
        assert client.post("/api/patient/link-code",
                           headers=doctor_headers).status_code == 401


class TestPreview:
    def test_preview_shows_profile_without_consuming(self, client, doctor_headers):
        h, _ = _patient(client)
        code = _issue_code(client, h)["code"]
        for _ in range(2):  # non-consuming — можно смотреть дважды
            r = client.get(f"/api/patient-links/preview?code={code}", headers=doctor_headers)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["full_name"] == "Носирова Мехрангез"
            assert body["blood_type"] == "A(II) Rh+"
            assert body["allergies"] == ["Пенициллин"]

    def test_wrong_code_404(self, client, doctor_headers):
        assert client.get("/api/patient-links/preview?code=000000",
                          headers=doctor_headers).status_code == 404

    def test_patient_token_cannot_preview(self, client):
        h, _ = _patient(client)
        code = _issue_code(client, h)["code"]
        assert client.get(f"/api/patient-links/preview?code={code}",
                          headers=h).status_code == 401


class TestConfirmLink:
    def test_creates_prefilled_patient_row(self, client, doctor_headers):
        h, _ = _patient(client)
        code = _issue_code(client, h)["code"]
        r = client.post("/api/patient-links", headers=doctor_headers, json={"code": code})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["created"] is True
        pat = body["patient"]
        assert pat["full_name"] == "Носирова Мехрангез"
        assert pat["allergies"] == ["Пенициллин"]
        assert pat["diagnoses"] == ["Гипотиреоз"]      # chronic → diagnoses
        assert pat["medications"] == ["Левотироксин 50мкг"]
        assert pat["age"] == 34                          # из даты рождения (2026)
        assert pat["gender"] == "Ж"                      # конвенция кабинета

        # запись видна врачу в обычном списке пациентов
        lst = client.get("/api/patients/", headers=doctor_headers).json()
        assert any(p["full_name"] == "Носирова Мехрангез" for p in lst)

    def test_code_is_single_use(self, client, doctor_headers):
        h, _ = _patient(client)
        code = _issue_code(client, h)["code"]
        assert client.post("/api/patient-links", headers=doctor_headers,
                           json={"code": code}).status_code == 201
        assert client.post("/api/patient-links", headers=doctor_headers,
                           json={"code": code}).status_code == 410

    def test_expired_code_410(self, client, doctor_headers, db_session):
        from datetime import datetime, timedelta
        from models import PatientLinkCode
        h, _ = _patient(client)
        code = _issue_code(client, h)["code"]
        row = db_session.query(PatientLinkCode).filter_by(code=code).first()
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db_session.commit()
        assert client.post("/api/patient-links", headers=doctor_headers,
                           json={"code": code}).status_code == 410

    def test_relink_is_idempotent(self, client, doctor_headers):
        """Повторная линковка того же пациента у того же врача — та же запись."""
        h, _ = _patient(client)
        code1 = _issue_code(client, h)["code"]
        r1 = client.post("/api/patient-links", headers=doctor_headers, json={"code": code1})
        code2 = _issue_code(client, h)["code"]
        r2 = client.post("/api/patient-links", headers=doctor_headers, json={"code": code2})
        assert r2.status_code == 200
        assert r2.json()["created"] is False
        assert r2.json()["patient"]["id"] == r1.json()["patient"]["id"]

    def test_link_is_audited(self, client, doctor_headers, db_session):
        h, _ = _patient(client)
        code = _issue_code(client, h)["code"]
        client.post("/api/patient-links", headers=doctor_headers, json={"code": code})
        from models import AuditLog
        assert db_session.query(AuditLog).filter(
            AuditLog.entity == "patient_link", AuditLog.action == "create"
        ).count() == 1


class TestNameFallback:
    def test_link_by_avris_id_with_name_confirmation(self, client, doctor_headers):
        h, avris_id = _patient(client)
        r = client.post("/api/patient-links", headers=doctor_headers,
                        json={"avris_patient_id": avris_id,
                              "full_name": "Носирова Мехрангез"})
        assert r.status_code == 201, r.text
        assert r.json()["created"] is True

    def test_name_mismatch_404(self, client, doctor_headers):
        h, avris_id = _patient(client)
        r = client.post("/api/patient-links", headers=doctor_headers,
                        json={"avris_patient_id": avris_id,
                              "full_name": "Другое Имя"})
        assert r.status_code == 404

    def test_fallback_requires_consent(self, client, doctor_headers):
        h, avris_id = _patient(client, phone="+992907770002", consent=False)
        r = client.post("/api/patient-links", headers=doctor_headers,
                        json={"avris_patient_id": avris_id,
                              "full_name": "Носирова Мехрангез"})
        assert r.status_code == 403
