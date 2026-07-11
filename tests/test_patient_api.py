"""T3 — patient profile API: own-data-only profile, onboarding consent, audit.

Profile endpoints are self-scoped (no id in the route) — the token owner IS
the addressed resource, so patient A physically cannot query patient B.
Consent is a one-time timestamp set at onboarding; re-posting must not move it.
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


def _auth(client, phone):
    client.post("/api/patient/auth/request-otp", json={"contact": phone})
    r = client.post("/api/patient/auth/verify-otp", json={"contact": phone, "code": DEV_OTP})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


PROFILE = {
    "full_name": "Носирова Мехрангез",
    "date_of_birth": "1992-03-14",
    "gender": "female",
    "height": 164.0,
    "weight": 58.5,
    "blood_type": "A(II) Rh+",
    "chronic_conditions": ["Гипотиреоз"],
    "allergies": ["Пенициллин"],
    "medications": ["Левотироксин 50мкг"],
    "language_pref": "tj",
}


class TestProfile:
    def test_get_initial_profile(self, client):
        h = _auth(client, "+992901000001")
        r = client.get("/api/patient/profile", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["avris_patient_id"].startswith("AV-")
        assert body["full_name"] is None
        assert body["consent_doctors_at"] is None

    def test_put_profile_roundtrip(self, client):
        h = _auth(client, "+992901000002")
        r = client.put("/api/patient/profile", headers=h, json=PROFILE)
        assert r.status_code == 200, r.text
        body = client.get("/api/patient/profile", headers=h).json()
        assert body["full_name"] == PROFILE["full_name"]
        assert body["allergies"] == ["Пенициллин"]
        assert body["language_pref"] == "tj"
        assert body["height"] == 164.0

    def test_partial_update_keeps_other_fields(self, client):
        h = _auth(client, "+992901000003")
        client.put("/api/patient/profile", headers=h, json=PROFILE)
        r = client.put("/api/patient/profile", headers=h, json={"weight": 60.0})
        assert r.status_code == 200
        body = client.get("/api/patient/profile", headers=h).json()
        assert body["weight"] == 60.0
        assert body["full_name"] == PROFILE["full_name"]  # untouched

    def test_validation_rejects_nonsense(self, client):
        h = _auth(client, "+992901000004")
        assert client.put("/api/patient/profile", headers=h,
                          json={"height": -5}).status_code == 422
        assert client.put("/api/patient/profile", headers=h,
                          json={"date_of_birth": "2200-01-01"}).status_code == 422

    def test_unauthenticated_401(self, client):
        assert client.get("/api/patient/profile").status_code == 401


class TestIsolation:
    def test_each_patient_sees_only_own_profile(self, client):
        h_a = _auth(client, "+992901000005")
        h_b = _auth(client, "+992901000006")
        client.put("/api/patient/profile", headers=h_a, json={"full_name": "Пациент А"})
        client.put("/api/patient/profile", headers=h_b, json={"full_name": "Пациент Б"})
        assert client.get("/api/patient/profile", headers=h_a).json()["full_name"] == "Пациент А"
        assert client.get("/api/patient/profile", headers=h_b).json()["full_name"] == "Пациент Б"


class TestConsent:
    def test_consent_sets_timestamp_once(self, client):
        h = _auth(client, "+992901000007")
        r1 = client.post("/api/patient/consent", headers=h)
        assert r1.status_code == 200
        first = r1.json()["consent_doctors_at"]
        assert first is not None

        r2 = client.post("/api/patient/consent", headers=h)
        assert r2.status_code == 200
        assert r2.json()["consent_doctors_at"] == first  # не переустанавливается

    def test_consent_is_audited(self, client, db_session):
        h = _auth(client, "+992901000008")
        client.post("/api/patient/consent", headers=h)
        from models import AuditLog
        rows = db_session.query(AuditLog).filter(
            AuditLog.entity == "patient_account",
            AuditLog.action == "consent",
        ).all()
        assert len(rows) == 1

    def test_consent_records_default_version(self, client):
        h = _auth(client, "+992901000009")
        r = client.post("/api/patient/consent", headers=h)
        assert r.status_code == 200
        assert r.json()["consent_version"] == "1.0"

    def test_consent_records_explicit_version(self, client):
        h = _auth(client, "+992901000010")
        r = client.post("/api/patient/consent", headers=h, json={"version": "2.1-tj"})
        assert r.status_code == 200
        assert r.json()["consent_version"] == "2.1-tj"

    def test_consent_version_not_overwritten(self, client):
        h = _auth(client, "+992901000011")
        first = client.post("/api/patient/consent", headers=h, json={"version": "1.0"}).json()
        second = client.post("/api/patient/consent", headers=h, json={"version": "9.9"}).json()
        # первое согласие — юридически значимое, версия не переустанавливается
        assert second["consent_version"] == "1.0"
        assert second["consent_doctors_at"] == first["consent_doctors_at"]
