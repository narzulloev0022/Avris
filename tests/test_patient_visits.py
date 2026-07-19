"""T7 — post-visit summary: Claude retells the SOAP note for the patient.

Generation is a best-effort background task on consultation save (pre-cached,
never live on stage). A summary that slips into diagnosis-speak ("вероятно,
у вас…") is dropped by the safety net and stays pending. Patient access goes
strictly through patient_links ownership.
"""
import json
import os

os.environ.setdefault("PATIENT_DEV_OTP", "424242")

import pytest
from fastapi.testclient import TestClient

DEV_OTP = os.environ["PATIENT_DEV_OTP"]

GOOD_SUMMARY = (
    "Вы были на приёме у врача. Врач осмотрел вас и назначил лечение: "
    "продолжайте принимать назначенный препарат и пейте больше воды. "
    "Повторный приём — через неделю."
)
FORBIDDEN_SUMMARY = "Вероятно, у вас пневмония. Принимайте антибиотики."


@pytest.fixture()
def client(db_session):
    from rate_limit import limiter
    limiter.enabled = False
    import main
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def mock_claude(monkeypatch):
    """Patch the Claude call; tests never hit the network."""
    state = {"reply": GOOD_SUMMARY, "calls": 0}

    async def fake_claude(system_prompt, user_msg, max_tokens=1024):
        state["calls"] += 1
        if isinstance(state["reply"], Exception):
            raise state["reply"]
        return state["reply"]

    import patient_visits
    monkeypatch.setattr(patient_visits, "_claude_call", fake_claude)
    return state


def _patient(client, phone="+992908880001"):
    client.post("/api/patient/auth/request-otp", json={"contact": phone})
    r = client.post("/api/patient/auth/verify-otp", json={"contact": phone, "code": DEV_OTP})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.put("/api/patient/profile", headers=h, json={"full_name": "Носирова Мехрангез"})
    client.post("/api/patient/consent", headers=h)
    return h


@pytest.fixture()
def doctor_headers(db_session):
    from auth import create_access_token, hash_password
    from models import User
    doc = User(email="doc@test.tj", password_hash=hash_password("x"),
               full_name="Др. Каримов", is_verified=True, is_approved=True)
    db_session.add(doc)
    db_session.commit()
    return {"Authorization": f"Bearer {create_access_token(doc.id)}"}


def _linked_patient(client, doctor_headers, phone="+992908880001"):
    """Full junction: patient onboards+consents, doctor links by code."""
    h = _patient(client, phone)
    code = client.post("/api/patient/link-code", headers=h).json()["code"]
    r = client.post("/api/patient-links", headers=doctor_headers, json={"code": code})
    return h, r.json()["patient"]["id"]


def _save_consultation(client, doctor_headers, patient_id):
    r = client.post("/api/consultations/", headers=doctor_headers, json={
        "patient_id": patient_id,
        "soap_s": "Жалобы на кашель 5 дней",
        "soap_o": "Т 37.2, хрипов нет",
        "soap_a": "ОРВИ",
        "soap_p": "Обильное питьё, амброксол 30мг 3р/д, контроль через неделю",
        "language": "ru",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestSummaryGeneration:
    def test_summary_created_for_linked_patient(self, client, doctor_headers, mock_claude):
        h, pid = _linked_patient(client, doctor_headers)
        cid = _save_consultation(client, doctor_headers, pid)

        visits = client.get("/api/patient/visits", headers=h).json()
        assert len(visits) == 1
        assert visits[0]["consultation_id"] == cid
        assert visits[0]["summary_status"] == "ready"

        detail = client.get(f"/api/patient/visits/{cid}", headers=h).json()
        assert detail["summary"] == GOOD_SUMMARY
        assert detail["doctor_name"] == "Др. Каримов"

    def test_no_summary_for_unlinked_patient(self, client, doctor_headers, mock_claude, db_session):
        # обычный пациент кабинета, без аккаунта в приложении
        r = client.post("/api/patients/", headers=doctor_headers,
                        json={"full_name": "Обычный Пациент"})
        cid = _save_consultation(client, doctor_headers, r.json()["id"])
        from models import VisitSummary
        assert db_session.query(VisitSummary).count() == 0
        assert mock_claude["calls"] == 0

    def test_forbidden_phrasing_dropped(self, client, doctor_headers, mock_claude):
        mock_claude["reply"] = FORBIDDEN_SUMMARY
        h, pid = _linked_patient(client, doctor_headers)
        cid = _save_consultation(client, doctor_headers, pid)

        visits = client.get("/api/patient/visits", headers=h).json()
        assert visits[0]["summary_status"] == "pending"
        detail = client.get(f"/api/patient/visits/{cid}", headers=h).json()
        assert detail["summary"] is None

    def test_claude_failure_never_breaks_doctor_save(self, client, doctor_headers, mock_claude):
        mock_claude["reply"] = RuntimeError("503 no key")
        h, pid = _linked_patient(client, doctor_headers)
        cid = _save_consultation(client, doctor_headers, pid)  # asserts 201 inside
        assert client.get(f"/api/patient/visits/{cid}", headers=h).json()["summary"] is None


class TestPrescriptions:
    def test_prescriptions_extracted_as_separate_block(self, client, doctor_headers, mock_claude):
        mock_claude["reply"] = json.dumps({
            "summary": GOOD_SUMMARY,
            "prescriptions": "Амброксол 30мг — 3 раза в день после еды, 5 дней.\nОбильное тёплое питьё.",
        })
        h, pid = _linked_patient(client, doctor_headers)
        cid = _save_consultation(client, doctor_headers, pid)

        detail = client.get(f"/api/patient/visits/{cid}", headers=h).json()
        assert detail["summary"] == GOOD_SUMMARY
        assert "Амброксол" in detail["prescriptions"]
        assert detail["summary_status"] == "ready"

    def test_no_prescriptions_when_plan_empty(self, client, doctor_headers, mock_claude):
        mock_claude["reply"] = json.dumps({"summary": GOOD_SUMMARY, "prescriptions": ""})
        h, pid = _linked_patient(client, doctor_headers)
        cid = _save_consultation(client, doctor_headers, pid)
        detail = client.get(f"/api/patient/visits/{cid}", headers=h).json()
        assert detail["summary"] == GOOD_SUMMARY
        assert detail["prescriptions"] is None

    def test_plaintext_reply_still_works_as_summary(self, client, doctor_headers, mock_claude):
        """Fallback: a non-JSON Claude reply becomes the summary, no prescriptions."""
        mock_claude["reply"] = GOOD_SUMMARY  # plain text, not JSON
        h, pid = _linked_patient(client, doctor_headers)
        cid = _save_consultation(client, doctor_headers, pid)
        detail = client.get(f"/api/patient/visits/{cid}", headers=h).json()
        assert detail["summary"] == GOOD_SUMMARY
        assert detail["prescriptions"] is None

    def test_forbidden_in_prescriptions_dropped_but_summary_kept(self, client, doctor_headers, mock_claude):
        mock_claude["reply"] = json.dumps({
            "summary": GOOD_SUMMARY,
            "prescriptions": "Вероятно, у вас пневмония, принимайте антибиотики.",
        })
        h, pid = _linked_patient(client, doctor_headers)
        cid = _save_consultation(client, doctor_headers, pid)
        detail = client.get(f"/api/patient/visits/{cid}", headers=h).json()
        assert detail["summary"] == GOOD_SUMMARY       # хорошее резюме сохранено
        assert detail["prescriptions"] is None          # опасный блок отброшен


class TestVisitAccess:
    def test_patient_cannot_read_foreign_visit(self, client, doctor_headers, mock_claude):
        h_a, pid_a = _linked_patient(client, doctor_headers, phone="+992908880001")
        cid_a = _save_consultation(client, doctor_headers, pid_a)

        h_b, _ = _linked_patient(client, doctor_headers, phone="+992908880002")
        assert client.get(f"/api/patient/visits/{cid_a}", headers=h_b).status_code == 404
        assert client.get("/api/patient/visits", headers=h_b).json() == []

    def test_doctor_token_rejected(self, client, doctor_headers):
        assert client.get("/api/patient/visits", headers=doctor_headers).status_code == 401
