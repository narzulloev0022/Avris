"""Скачивание файла анализа с не-латинским именем.

Лаборатория называет сканы по-русски. Заголовок Content-Disposition с таким
именем не кодировался в latin-1, и вместо анализа приходил 500 — и пациенту,
и врачу.
"""
import pytest

from database import SessionLocal
from http_files import content_disposition
from models import LabFile, LabOrder, PatientAccount, PatientLink, Patient, User
from patient_auth import create_patient_access_token


@pytest.mark.parametrize("name,expect_ascii", [
    ("Анализ крови.pdf", 'filename="file.pdf"'),
    ("Таҳлили хун.jpg", 'filename="file.jpg"'),
    ("result.pdf", 'filename="result.pdf"'),
    ('стран"ное\rимя.png', 'filename="file.png"'),
])
def test_header_is_latin1_safe(name, expect_ascii):
    header = content_disposition(name)
    header.encode("latin-1")  # именно это раньше и падало
    assert expect_ascii in header
    assert "filename*=UTF-8''" in header


def test_patient_downloads_file_with_cyrillic_name(client):
    db = SessionLocal()
    try:
        doctor = User(email="lab.doc@avris.local", password_hash="x",
                      full_name="Др. Тест", is_verified=True)
        db.add(doctor)
        account = PatientAccount(phone="+992900000555", avris_patient_id="AV-FILE-TEST",
                                 full_name="Файлова Проба")
        db.add(account)
        db.flush()
        patient = Patient(doctor_id=doctor.id, full_name=account.full_name)
        db.add(patient)
        db.flush()
        db.add(PatientLink(patient_account_id=account.id, patient_id=patient.id,
                           doctor_id=doctor.id, method="qr"))
        order = LabOrder(patient_id=patient.id, doctor_id=doctor.id,
                         qr_token="file-test-token", tests=["Гемоглобин"], status="received")
        db.add(order)
        db.flush()
        rec = LabFile(lab_order_id=order.id, filename="Анализ крови.pdf",
                      content_type="application/pdf", result_type="lab",
                      size_bytes=10, data=b"%PDF-1.4\n%")
        db.add(rec)
        db.commit()
        token = create_patient_access_token(account.id)
        order_id, file_id = order.id, rec.id
    finally:
        db.close()

    resp = client.get(f"/api/patient/labs/{order_id}/files/{file_id}",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(b"%PDF")
    disposition = resp.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
