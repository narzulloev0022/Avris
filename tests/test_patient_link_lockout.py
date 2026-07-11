"""Sec-2 — per-doctor brute-force lockout on patient-link code entry.

5 consecutive wrong/dead codes (404/410) from preview or confirm lock this
doctor's linking for 15 min. A 403 (valid code, no consent) is not a guess
and must not count. A successful resolve resets the counter.
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
    return {"Authorization": f"Bearer {create_access_token(doc.id)}"}, doc.id


def _consented_patient(client, phone="+992909990001"):
    client.post("/api/patient/auth/request-otp", json={"contact": phone})
    r = client.post("/api/patient/auth/verify-otp", json={"contact": phone, "code": DEV_OTP})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.put("/api/patient/profile", headers=h, json={"full_name": "Носирова Мехрангез"})
    client.post("/api/patient/consent", headers=h)
    code = client.post("/api/patient/link-code", headers=h).json()["code"]
    return h, code


def _wrong_code(existing):
    # a 6-digit code guaranteed different from `existing`
    return "000000" if existing != "000000" else "111111"


class TestLockout:
    def test_five_wrong_codes_lock_the_doctor(self, client, doctor_headers):
        headers, _ = doctor_headers
        for _ in range(5):
            r = client.get("/api/patient-links/preview?code=000000", headers=headers)
            assert r.status_code == 404
        # 6th attempt — even a syntactically fine code — is locked out
        r = client.get("/api/patient-links/preview?code=123456", headers=headers)
        assert r.status_code == 429

    def test_confirm_path_also_counts(self, client, doctor_headers):
        headers, _ = doctor_headers
        for _ in range(5):
            r = client.post("/api/patient-links", headers=headers, json={"code": "000000"})
            assert r.status_code == 404
        r = client.post("/api/patient-links", headers=headers, json={"code": "123456"})
        assert r.status_code == 429

    def test_success_resets_counter(self, client, doctor_headers):
        headers, _ = doctor_headers
        h, code = _consented_patient(client)
        for _ in range(4):
            assert client.get("/api/patient-links/preview?code=000000",
                              headers=headers).status_code == 404
        # a real code resolves and resets the streak
        assert client.get(f"/api/patient-links/preview?code={code}",
                          headers=headers).status_code == 200
        # four more failures must NOT lock (counter was reset)
        for _ in range(4):
            assert client.get("/api/patient-links/preview?code=000000",
                              headers=headers).status_code == 404
        assert client.get(f"/api/patient-links/preview?code={code}",
                          headers=headers).status_code == 200

    def test_lockout_expires(self, client, doctor_headers, db_session):
        from datetime import datetime, timedelta
        from models import LinkThrottle
        headers, doc_id = doctor_headers
        for _ in range(5):
            client.get("/api/patient-links/preview?code=000000", headers=headers)
        assert client.get("/api/patient-links/preview?code=123456",
                          headers=headers).status_code == 429
        # fast-forward past the cooldown
        row = db_session.query(LinkThrottle).filter_by(doctor_id=doc_id).first()
        row.locked_until = datetime.utcnow() - timedelta(seconds=1)
        db_session.commit()
        h, code = _consented_patient(client)
        assert client.get(f"/api/patient-links/preview?code={code}",
                          headers=headers).status_code == 200

    def test_consent_403_does_not_count(self, client, doctor_headers, db_session):
        """A correct Avris ID + name whose account has NO consent → 403, which
        is a valid lookup, not a guess — it must not accrue toward lockout."""
        headers, _ = doctor_headers
        # account WITH profile+name but WITHOUT consent
        client.post("/api/patient/auth/request-otp", json={"contact": "+992909990009"})
        r = client.post("/api/patient/auth/verify-otp",
                        json={"contact": "+992909990009", "code": DEV_OTP})
        ph = {"Authorization": f"Bearer {r.json()['access_token']}"}
        client.put("/api/patient/profile", headers=ph, json={"full_name": "Без Согласия"})
        avris_id = r.json()["account"]["avris_patient_id"]

        for _ in range(6):  # more than the limit
            resp = client.post("/api/patient-links", headers=headers,
                               json={"avris_patient_id": avris_id, "full_name": "Без Согласия"})
            assert resp.status_code == 403
        # not locked — 403s never counted
        from models import LinkThrottle
        assert db_session.query(LinkThrottle).count() == 0
