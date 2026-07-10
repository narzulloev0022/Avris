"""T1 — patient-layer models: PatientAccount, PatientLink, VisitSummary.

The patient app introduces the platform's first GLOBAL patient identity:
an account the patient owns (phone/email + OTP), linked to the existing
doctor-scoped ``patients`` rows via ``patient_links``. The existing schema
must keep working untouched.
"""
import re

import pytest
from sqlalchemy.exc import IntegrityError


def _mk_doctor(db):
    from models import User
    doc = User(email="doc@test.tj", password_hash="x", full_name="Др. Тест")
    db.add(doc)
    db.commit()
    return doc


def _mk_account(db, **kw):
    from models import PatientAccount
    from patient_ids import new_avris_patient_id
    defaults = dict(
        avris_patient_id=new_avris_patient_id(),
        phone="+992900000001",
        full_name="Пациент Тестов",
    )
    defaults.update(kw)
    acc = PatientAccount(**defaults)
    db.add(acc)
    db.commit()
    return acc


class TestAvrisPatientId:
    def test_format(self):
        from patient_ids import new_avris_patient_id
        pid = new_avris_patient_id()
        # AV-XXXX-XXXX, unambiguous alphabet (no 0/O/1/I/L)
        assert re.fullmatch(r"AV-[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}", pid), pid

    def test_uniqueness_burst(self):
        from patient_ids import new_avris_patient_id
        ids = {new_avris_patient_id() for _ in range(1000)}
        assert len(ids) == 1000

    def test_db_unique_constraint(self, db_session):
        _mk_account(db_session, avris_patient_id="AV-2222-3333", phone="+992900000010")
        with pytest.raises(IntegrityError):
            _mk_account(db_session, avris_patient_id="AV-2222-3333", phone="+992900000011")


class TestPatientAccount:
    def test_create_minimal(self, db_session):
        acc = _mk_account(db_session)
        assert acc.id is not None
        assert acc.is_active is True
        assert acc.language_pref == "ru"

    def test_consent_defaults_to_none_and_is_settable(self, db_session):
        """Consent = a timestamp, given in-app at onboarding (not at the visit)."""
        from datetime import datetime
        acc = _mk_account(db_session)
        assert acc.consent_doctors_at is None
        acc.consent_doctors_at = datetime.utcnow()
        db_session.commit()
        db_session.refresh(acc)
        assert acc.consent_doctors_at is not None

    def test_medical_json_fields_default_to_lists(self, db_session):
        acc = _mk_account(db_session)
        assert acc.chronic_conditions == []
        assert acc.allergies == []
        assert acc.medications == []

    def test_phone_unique(self, db_session):
        _mk_account(db_session, phone="+992900000042")
        with pytest.raises(IntegrityError):
            _mk_account(db_session, phone="+992900000042")


class TestPatientLink:
    def test_link_account_to_doctor_patient_record(self, db_session):
        from models import Patient, PatientLink
        doc = _mk_doctor(db_session)
        acc = _mk_account(db_session)
        pat = Patient(doctor_id=doc.id, full_name="Пациент Тестов")
        db_session.add(pat)
        db_session.commit()

        link = PatientLink(patient_account_id=acc.id, patient_id=pat.id,
                           doctor_id=doc.id, method="qr")
        db_session.add(link)
        db_session.commit()
        assert link.id is not None
        assert link.created_at is not None

    def test_duplicate_link_rejected(self, db_session):
        """Same account ↔ same doctor-record must be one row (idempotency at DB level)."""
        from models import Patient, PatientLink
        doc = _mk_doctor(db_session)
        acc = _mk_account(db_session)
        pat = Patient(doctor_id=doc.id, full_name="Пациент Тестов")
        db_session.add(pat)
        db_session.commit()

        db_session.add(PatientLink(patient_account_id=acc.id, patient_id=pat.id,
                                   doctor_id=doc.id, method="qr"))
        db_session.commit()
        db_session.add(PatientLink(patient_account_id=acc.id, patient_id=pat.id,
                                   doctor_id=doc.id, method="name"))
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestVisitSummary:
    def test_one_summary_per_consultation(self, db_session):
        from models import Consultation, VisitSummary
        doc = _mk_doctor(db_session)
        acc = _mk_account(db_session)
        cons = Consultation(doctor_id=doc.id, soap_s="Жалобы", soap_a="Диагноз")
        db_session.add(cons)
        db_session.commit()

        db_session.add(VisitSummary(consultation_id=cons.id,
                                    patient_account_id=acc.id,
                                    summary="Понятный пересказ визита."))
        db_session.commit()
        db_session.add(VisitSummary(consultation_id=cons.id,
                                    patient_account_id=acc.id,
                                    summary="Дубль."))
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestSchemaCompat:
    def test_init_db_idempotent(self, db_session):
        """init_db() must be safe to run repeatedly over an existing DB."""
        from database import init_db
        init_db()
        init_db()

    def test_existing_tables_untouched(self, db_session):
        """The doctor cabinet keeps working: users/patients CRUD as before."""
        from models import Patient
        doc = _mk_doctor(db_session)
        pat = Patient(doctor_id=doc.id, full_name="Обычный Пациент")
        db_session.add(pat)
        db_session.commit()
        assert pat.id is not None
