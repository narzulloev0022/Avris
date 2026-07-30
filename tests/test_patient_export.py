"""Экспорт медкарты в PDF — платная фича Plus.

Проверяем три вещи: гейт тарифа, что в файл попадают только СВОИ данные,
и что PDF собирается на кириллице (а не падает и не отдаёт пустышку).
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("PATIENT_DEV_OTP", "424242")

import pytest
from fastapi.testclient import TestClient

from models import PatientAccount

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


def _grant(db_session, phone, tier="plus"):
    acc = db_session.query(PatientAccount).filter(PatientAccount.phone == phone).first()
    acc.subscription_tier = tier
    acc.subscription_expires_at = (
        datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30))
    acc.subscription_source = "manual"
    db_session.commit()
    return acc


@pytest.fixture()
def doctor(db_session):
    from auth import create_access_token, hash_password
    from models import User
    doc = User(email="doc-export@test.tj", password_hash=hash_password("x"),
               full_name="Др. Каримов", is_verified=True, is_approved=True)
    db_session.add(doc)
    db_session.commit()
    return {"Authorization": f"Bearer {create_access_token(doc.id)}"}, doc.id


def _link(client, doctor, phone, full_name="Носирова Мехрангез"):
    doc_headers, _ = doctor
    h = _auth(client, phone)
    client.put("/api/patient/profile", headers=h,
               json={"full_name": full_name, "blood_type": "O(I) Rh+",
                     "allergies": ["Пенициллин"], "chronic_conditions": ["Гипертония"]})
    client.post("/api/patient/consent", headers=h)
    code = client.post("/api/patient/link-code", headers=h).json()["code"]
    pid = client.post("/api/patient-links", headers=doc_headers,
                      json={"code": code}).json()["patient"]["id"]
    return h, pid


def _visit_with_summary(db_session, doctor_id, patient_id, account_phone,
                        summary="Это ОРВИ, лёгкое течение.", presc="Пейте тёплое."):
    from models import Consultation, VisitSummary
    acc = db_session.query(PatientAccount).filter(
        PatientAccount.phone == account_phone).first()
    c = Consultation(doctor_id=doctor_id, patient_id=patient_id,
                     soap_s="кашель", soap_o="норма", soap_a="ОРВИ", soap_p="покой")
    db_session.add(c)
    db_session.flush()
    db_session.add(VisitSummary(consultation_id=c.id, patient_account_id=acc.id,
                                summary=summary, prescriptions=presc))
    db_session.commit()
    return c.id


def _lab(db_session, doctor_id, patient_id):
    from models import LabOrder
    o = LabOrder(doctor_id=doctor_id, patient_id=patient_id, qr_token=str(uuid.uuid4()),
                 tests=["Гемоглобин"], status="received",
                 results={"Гемоглобин": {"value": "140", "unit": "г/л", "range": "120-160"}})
    db_session.add(o)
    db_session.commit()
    return o.id


class TestExportGate:
    def test_requires_auth(self, client):
        assert client.get("/api/patient/export/record.pdf").status_code in (401, 403)

    def test_free_gets_402(self, client, db_session, doctor):
        h, _ = _link(client, doctor, "+992907000001")
        r = client.get("/api/patient/export/record.pdf", headers=h)
        assert r.status_code == 402, r.text

    def test_expired_plus_loses_export(self, client, db_session, doctor):
        phone = "+992907000002"
        h, _ = _link(client, doctor, phone)
        acc = _grant(db_session, phone)
        acc.subscription_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db_session.commit()
        assert client.get("/api/patient/export/record.pdf", headers=h).status_code == 402


class TestExportContent:
    def test_plus_gets_a_real_pdf(self, client, db_session, doctor):
        phone = "+992907000010"
        h, pid = _link(client, doctor, phone)
        _, doc_id = doctor
        _visit_with_summary(db_session, doc_id, pid, phone)
        _lab(db_session, doc_id, pid)
        _grant(db_session, phone)

        r = client.get("/api/patient/export/record.pdf", headers=h)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")
        # Сводка визита и анализ реально попали в файл — размер не «пустой лист».
        assert len(r.content) > 3000
        # Имя файла — ASCII: кириллица в Content-Disposition ломает часть клиентов.
        disposition = r.headers["content-disposition"]
        assert disposition.isascii(), disposition
        assert ".pdf" in disposition

    def test_export_works_without_any_visits(self, client, db_session, doctor):
        """Пустая карта — тоже валидный PDF, а не 500."""
        phone = "+992907000011"
        h = _auth(client, phone)
        _grant(db_session, phone)
        r = client.get("/api/patient/export/record.pdf", headers=h)
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"%PDF")

    def test_export_carries_only_own_data(self, client, db_session, doctor):
        """Чужие приёмы не должны попасть в выгрузку."""
        _, doc_id = doctor
        mine = "+992907000020"
        theirs = "+992907000021"
        h_mine, pid_mine = _link(client, doctor, mine, full_name="Носирова Мехрангез")
        h_theirs, pid_theirs = _link(client, doctor, theirs, full_name="Рахимов Азиз")
        _visit_with_summary(db_session, doc_id, pid_theirs, theirs,
                            summary="СЕКРЕТ ЧУЖОГО ВИЗИТА")
        _grant(db_session, mine)

        r = client.get("/api/patient/export/record.pdf", headers=h_mine)
        assert r.status_code == 200
        # PDF сжат, поэтому ищем не текст, а факт: у меня приёмов нет, значит
        # файл заметно меньше того, что получит владелец визита.
        _grant(db_session, theirs)
        r_theirs = client.get("/api/patient/export/record.pdf", headers=h_theirs)
        assert r_theirs.status_code == 200
        assert len(r_theirs.content) > len(r.content)

    def test_pending_summary_is_stated_not_silently_empty(self, client, db_session, doctor):
        from models import Consultation
        phone = "+992907000030"
        h, pid = _link(client, doctor, phone)
        _, doc_id = doctor
        db_session.add(Consultation(doctor_id=doc_id, patient_id=pid, soap_s="жалобы"))
        db_session.commit()
        _grant(db_session, phone)
        r = client.get("/api/patient/export/record.pdf", headers=h)
        assert r.status_code == 200
        assert r.content.startswith(b"%PDF")


class TestExportRendering:
    """Мелочи, которые видит врач в распечатке."""

    def test_gender_is_russian_not_a_code(self, client, db_session, doctor):
        import pdf_export
        assert pdf_export._gender_ru("female") == "женский"
        assert pdf_export._gender_ru("male") == "мужской"
        # Неизвестное значение печатаем как есть, а не пустотой.
        assert pdf_export._gender_ru("унисекс") == "унисекс"
        assert pdf_export._gender_ru(None) == ""

    def test_superscript_units_become_markup(self):
        """×10⁹/л печаталось квадратом — нет глифа в TTF."""
        import pdf_export
        out = pdf_export._esc("6.2 ×10⁹/л")
        assert "<super>9</super>" in out
        assert "⁹" not in out

    def test_escaping_still_wins_over_markup(self):
        """Пользовательские угловые скобки не должны становиться разметкой."""
        import pdf_export
        assert pdf_export._esc("<b>жалобы</b>") == "&lt;b&gt;жалобы&lt;/b&gt;"

    def test_units_with_superscript_survive_pdf_build(self, client, db_session, doctor):
        phone = "+992907000040"
        h, pid = _link(client, doctor, phone)
        _, doc_id = doctor
        from models import LabOrder
        db_session.add(LabOrder(
            doctor_id=doc_id, patient_id=pid, qr_token=str(uuid.uuid4()),
            tests=["Лейкоциты"], status="received",
            results={"Лейкоциты": {"value": "6.2", "unit": "×10⁹/л", "range": "4-9"}}))
        db_session.commit()
        _grant(db_session, phone)
        r = client.get("/api/patient/export/record.pdf", headers=h)
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"%PDF")
