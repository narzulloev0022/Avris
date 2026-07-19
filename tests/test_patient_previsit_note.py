"""Patient pre-visit note — a short note the patient writes before a visit,
surfaced to the doctor ONCE at QR-link confirmation.

Covers: create/update the single active note; visibility only through the
existing consent + PatientLink gate (confirm_link); marking seen on first
view; and an already-seen note not blocking a fresh one.
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


def _patient(client, phone="+992907770001", consent=True):
    client.post("/api/patient/auth/request-otp", json={"contact": phone})
    r = client.post("/api/patient/auth/verify-otp", json={"contact": phone, "code": DEV_OTP})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.put("/api/patient/profile", headers=h, json={
        "full_name": "Носирова Мехрангез", "date_of_birth": "1992-03-14", "gender": "female"})
    if consent:
        client.post("/api/patient/consent", headers=h)
    return h, r.json()["account"]["avris_patient_id"]


def _issue_code(client, h):
    r = client.post("/api/patient/link-code", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["code"]


def _account_id(db_session, avris_id):
    from models import PatientAccount
    db_session.expire_all()
    return db_session.query(PatientAccount).filter_by(avris_patient_id=avris_id).first().id


def _notes(db_session, account_id):
    from models import PatientPreVisitNote
    db_session.expire_all()
    return db_session.query(PatientPreVisitNote).filter_by(
        patient_account_id=account_id).order_by(PatientPreVisitNote.id).all()


class TestCreateUpdate:
    def test_create_returns_note(self, client):
        h, _ = _patient(client)
        r = client.post("/api/patient/pre-visit-note", headers=h,
                        json={"note_text": "Болит горло 3 дня, температура 38"})
        assert r.status_code == 200, r.text
        assert r.json()["note_text"] == "Болит горло 3 дня, температура 38"
        assert r.json()["created_at"]

    def test_update_keeps_single_active_note(self, client, db_session):
        h, avris = _patient(client)
        client.post("/api/patient/pre-visit-note", headers=h, json={"note_text": "первый вариант"})
        client.post("/api/patient/pre-visit-note", headers=h, json={"note_text": "исправленный вариант"})
        rows = _notes(db_session, _account_id(db_session, avris))
        assert len(rows) == 1                         # одна активная, не история
        assert rows[0].note_text == "исправленный вариант"

    def test_empty_note_rejected(self, client):
        h, _ = _patient(client)
        assert client.post("/api/patient/pre-visit-note", headers=h,
                           json={"note_text": "   "}).status_code == 422

    def test_over_limit_rejected(self, client):
        h, _ = _patient(client)
        assert client.post("/api/patient/pre-visit-note", headers=h,
                           json={"note_text": "a" * 301}).status_code == 422


class TestDoctorVisibility:
    def test_surfaced_on_confirm_and_marked_seen(self, client, doctor_headers, db_session):
        h, avris = _patient(client)
        client.post("/api/patient/pre-visit-note", headers=h, json={"note_text": "Хочу обсудить давление"})
        code = _issue_code(client, h)
        r = client.post("/api/patient-links", headers=doctor_headers, json={"code": code})
        assert r.status_code == 201, r.text
        assert r.json()["pre_visit_note"] is not None
        assert r.json()["pre_visit_note"]["note_text"] == "Хочу обсудить давление"

        # stamped seen in the DB
        note = _notes(db_session, _account_id(db_session, avris))[0]
        assert note.seen_at is not None
        assert note.seen_by_doctor_id is not None

        # a later confirm (new code, idempotent re-link) does NOT re-surface it
        code2 = _issue_code(client, h)
        r2 = client.post("/api/patient-links", headers=doctor_headers, json={"code": code2})
        assert r2.json()["pre_visit_note"] is None

    def test_no_note_returns_null(self, client, doctor_headers):
        h, _ = _patient(client)
        code = _issue_code(client, h)
        r = client.post("/api/patient-links", headers=doctor_headers, json={"code": code})
        assert r.status_code == 201
        assert r.json()["pre_visit_note"] is None

    def test_visibility_gated_by_consent(self, client, doctor_headers, db_session):
        # Note written WITHOUT consent is invisible: the only doctor path
        # (confirm_link) refuses on the existing consent gate, so the note
        # never reaches the doctor and stays unseen.
        h, avris = _patient(client, phone="+992907770009", consent=False)
        client.post("/api/patient/pre-visit-note", headers=h, json={"note_text": "секретная заметка"})
        r = client.post("/api/patient-links", headers=doctor_headers,
                        json={"avris_patient_id": avris, "full_name": "Носирова Мехрангез"})
        assert r.status_code == 403
        assert _notes(db_session, _account_id(db_session, avris))[0].seen_at is None


class TestSeenDoesNotBlockNew:
    def test_seen_note_does_not_block_new(self, client, doctor_headers, db_session):
        h, avris = _patient(client)
        client.post("/api/patient/pre-visit-note", headers=h, json={"note_text": "заметка к визиту 1"})
        code = _issue_code(client, h)
        client.post("/api/patient-links", headers=doctor_headers, json={"code": code})  # marks seen

        # patient writes a NEW note for the next visit → fresh active row
        r = client.post("/api/patient/pre-visit-note", headers=h, json={"note_text": "заметка к визиту 2"})
        assert r.status_code == 200
        assert r.json()["note_text"] == "заметка к визиту 2"

        rows = _notes(db_session, _account_id(db_session, avris))
        assert len(rows) == 2
        seen = [x for x in rows if x.seen_at is not None]
        unseen = [x for x in rows if x.seen_at is None]
        assert len(seen) == 1 and seen[0].note_text == "заметка к визиту 1"
        assert len(unseen) == 1 and unseen[0].note_text == "заметка к визиту 2"
