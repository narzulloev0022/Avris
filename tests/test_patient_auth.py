"""T2 — patient auth: OTP login, JWT audience=patient, refresh rotation.

The patient door and the doctor door share one base but must never share
tokens: a doctor JWT on a patient endpoint is 401, and vice versa.
For the demo OTP is delivered via email (Resend) or a fixed dev code
(PATIENT_DEV_OTP env) for phone contacts — there is no SMS provider yet.
"""
import os

os.environ.setdefault("PATIENT_DEV_OTP", "424242")

import pytest
from fastapi.testclient import TestClient

PHONE = "+992901112233"
DEV_OTP = os.environ["PATIENT_DEV_OTP"]


@pytest.fixture()
def client(db_session):
    """Fresh schema (via db_session) + TestClient, rate limiting off."""
    from rate_limit import limiter
    limiter.enabled = False
    import main
    with TestClient(main.app) as c:
        yield c


def _login_patient(client, phone=PHONE):
    r = client.post("/api/patient/auth/request-otp", json={"contact": phone})
    assert r.status_code == 200, r.text
    r = client.post("/api/patient/auth/verify-otp", json={"contact": phone, "code": DEV_OTP})
    assert r.status_code == 200, r.text
    return r.json()


class TestOtpFlow:
    def test_request_then_verify_creates_account(self, client):
        data = _login_patient(client)
        assert data["is_new"] is True
        assert data["access_token"] and data["refresh_token"]
        assert data["account"]["avris_patient_id"].startswith("AV-")
        assert data["account"]["phone"] == PHONE

    def test_second_login_is_not_new(self, client):
        _login_patient(client)
        data = _login_patient(client)
        assert data["is_new"] is False

    def test_wrong_code_rejected(self, client):
        client.post("/api/patient/auth/request-otp", json={"contact": PHONE})
        r = client.post("/api/patient/auth/verify-otp", json={"contact": PHONE, "code": "000000"})
        assert r.status_code == 400

    def test_verify_without_request_rejected(self, client):
        r = client.post("/api/patient/auth/verify-otp", json={"contact": PHONE, "code": DEV_OTP})
        assert r.status_code == 400

    def test_me_returns_own_account(self, client):
        data = _login_patient(client)
        r = client.get("/api/patient/auth/me",
                       headers={"Authorization": f"Bearer {data['access_token']}"})
        assert r.status_code == 200
        assert r.json()["avris_patient_id"] == data["account"]["avris_patient_id"]


class TestTokenIsolation:
    """The two doors must not accept each other's keys."""

    def test_doctor_token_rejected_on_patient_endpoint(self, client, db_session):
        from auth import create_access_token, hash_password
        from models import User
        doc = User(email="doc@test.tj", password_hash=hash_password("x"),
                   full_name="Др. Тест", is_verified=True, is_approved=True)
        db_session.add(doc)
        db_session.commit()
        doctor_token = create_access_token(doc.id)

        r = client.get("/api/patient/auth/me",
                       headers={"Authorization": f"Bearer {doctor_token}"})
        assert r.status_code == 401

    def test_patient_token_rejected_on_doctor_endpoint(self, client):
        data = _login_patient(client)
        r = client.get("/api/patients/",
                       headers={"Authorization": f"Bearer {data['access_token']}"})
        assert r.status_code == 401

    def test_garbage_token_rejected(self, client):
        r = client.get("/api/patient/auth/me",
                       headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401


class TestRefreshRotation:
    def test_refresh_rotates_and_revokes_old(self, client):
        data = _login_patient(client)
        r = client.post("/api/patient/auth/refresh",
                        json={"refresh_token": data["refresh_token"]})
        assert r.status_code == 200, r.text
        fresh = r.json()
        assert fresh["access_token"] != data["access_token"]

        # the old refresh token must be dead after rotation
        r = client.post("/api/patient/auth/refresh",
                        json={"refresh_token": data["refresh_token"]})
        assert r.status_code == 401

    def test_patient_access_token_is_not_a_refresh_token(self, client):
        data = _login_patient(client)
        r = client.post("/api/patient/auth/refresh",
                        json={"refresh_token": data["access_token"]})
        assert r.status_code == 401
