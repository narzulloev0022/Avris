"""Отзыв согласия и удаление аккаунта.

Оба действия экран обещает пациенту прямым текстом, поэтому они обязаны быть
настоящими: «отозвал» должно означать, что врач больше не видит, а «удалил» —
что данных нет.
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
def doctor(db_session):
    from auth import create_access_token, hash_password
    from models import User
    doc = User(email="doc-lifecycle@test.tj", password_hash=hash_password("x"),
               full_name="Др. Каримов", is_verified=True, is_approved=True)
    db_session.add(doc)
    db_session.commit()
    return {"Authorization": f"Bearer {create_access_token(doc.id)}"}, doc.id


def _patient(client, phone):
    client.post("/api/patient/auth/request-otp", json={"contact": phone})
    r = client.post("/api/patient/auth/verify-otp", json={"contact": phone, "code": DEV_OTP})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.post("/api/patient/consent", headers=h)
    return h


def _link(client, doctor, patient_headers):
    doc_headers, _ = doctor
    code = client.post("/api/patient/link-code", headers=patient_headers).json()["code"]
    r = client.post("/api/patient-links", headers=doc_headers, json={"code": code})
    assert r.status_code in (200, 201), r.text
    return r.json()["patient"]["id"]


class TestRevokeConsent:
    def test_revoke_clears_consent(self, client):
        h = _patient(client, "+992909000001")
        assert client.get("/api/patient/profile", headers=h).json()["consent_doctors_at"] is not None
        r = client.post("/api/patient/consent/revoke", headers=h)
        assert r.status_code in (200, 201), r.text
        assert r.json()["consent_doctors_at"] is None

    def test_revoke_breaks_existing_links(self, client, doctor, db_session):
        """Главное: связи, созданные ДО отзыва, должны разорваться — иначе
        отзыв не отзывает ничего."""
        from models import PatientAccount, PatientLink
        phone = "+992909000002"
        h = _patient(client, phone)
        _link(client, doctor, h)

        acc = db_session.query(PatientAccount).filter(PatientAccount.phone == phone).first()
        assert db_session.query(PatientLink).filter(
            PatientLink.patient_account_id == acc.id).count() == 1

        client.post("/api/patient/consent/revoke", headers=h)
        db_session.expire_all()
        assert db_session.query(PatientLink).filter(
            PatientLink.patient_account_id == acc.id).count() == 0

    def test_patient_can_consent_again_after_revoke(self, client):
        h = _patient(client, "+992909000003")
        client.post("/api/patient/consent/revoke", headers=h)
        r = client.post("/api/patient/consent", headers=h)
        assert r.status_code == 200
        assert r.json()["consent_doctors_at"] is not None

    def test_requires_auth(self, client):
        assert client.post("/api/patient/consent/revoke").status_code in (401, 403)


class TestDeleteAccount:
    def test_account_and_data_are_gone(self, client, db_session):
        from models import PatientAccount, PatientConversation
        phone = "+992909000010"
        h = _patient(client, phone)
        client.post("/api/patient/pre-visit-note", headers=h, json={"note_text": "Кашель"})

        acc = db_session.query(PatientAccount).filter(PatientAccount.phone == phone).first()
        acc_id = acc.id

        assert client.delete("/api/patient/account", headers=h).status_code == 204
        db_session.expire_all()
        assert db_session.query(PatientAccount).filter(PatientAccount.id == acc_id).first() is None
        # Разговоры уходят каскадом вместе с аккаунтом.
        assert db_session.query(PatientConversation).filter(
            PatientConversation.patient_account_id == acc_id).count() == 0

    def test_token_stops_working_after_delete(self, client):
        h = _patient(client, "+992909000011")
        client.delete("/api/patient/account", headers=h)
        # Аккаунта нет — старый токен не должен открывать чужой/пустой профиль.
        assert client.get("/api/patient/profile", headers=h).status_code in (401, 403, 404)

    def test_doctor_records_survive(self, client, doctor, db_session):
        """Приём у врача — его документация: пациент закрывает доступ, но не
        стирает историю болезни у врача."""
        from models import Consultation, Patient
        phone = "+992909000012"
        h = _patient(client, phone)
        pid = _link(client, doctor, h)
        _, doc_id = doctor
        db_session.add(Consultation(patient_id=pid, doctor_id=doc_id, soap_s="жалобы"))
        db_session.commit()

        client.delete("/api/patient/account", headers=h)
        db_session.expire_all()
        assert db_session.query(Patient).filter(Patient.id == pid).first() is not None
        assert db_session.query(Consultation).filter(Consultation.patient_id == pid).count() == 1

    def test_requires_auth(self, client):
        assert client.delete("/api/patient/account").status_code in (401, 403)
