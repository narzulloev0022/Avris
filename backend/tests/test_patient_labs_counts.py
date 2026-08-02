"""Сводка анализа в списке: сколько значений в норме, сколько вне неё.

Пациент листает список ровно с одним вопросом — «всё ли хорошо». Раньше
ответ был только внутри анализа, и на него уходило открытие плюс перебор
строк глазами.
"""
from datetime import datetime

from database import SessionLocal
from models import LabOrder, Patient, PatientAccount, PatientLink, User
from patient_auth import create_patient_access_token


def _setup(db, suffix, results):
    doctor = User(email=f"lab{suffix}@x.tj", password_hash="x", full_name="Др. Проба")
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    patient = Patient(doctor_id=doctor.id, full_name="Анализы Проба")
    account = PatientAccount(phone=f"+9929470{suffix}", avris_patient_id=f"AV-LAB-{suffix}",
                             full_name="Анализы Проба")
    db.add_all([patient, account])
    db.commit()
    db.refresh(patient)
    db.refresh(account)
    db.add(PatientLink(patient_account_id=account.id, patient_id=patient.id,
                       doctor_id=doctor.id, method="qr"))
    db.add(LabOrder(patient_id=patient.id, doctor_id=doctor.id, qr_token=f"tok-{suffix}",
                    tests=list(results or []), status="received" if results else "pending",
                    results=results,
                    received_at=datetime.utcnow()))
    db.commit()
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


def test_counts_reach_the_list(client):
    db = SessionLocal()
    try:
        headers = _setup(db, "0001", {
            "Гемоглобин": {"value": "128", "unit": "г/л", "range": "120–150"},
            "Лейкоциты": {"value": "9.8", "unit": "×10⁹/л", "range": "4.0-9.0"},
            "Тромбоциты": {"value": "265", "unit": "×10⁹/л", "range": "180-320"},
        })
    finally:
        db.close()
    item = client.get("/api/patient/labs", headers=headers).json()[0]
    assert (item["in_range"], item["out_of_range"]) == (2, 1)


def test_unreadable_range_counts_as_neither(client):
    """Норму, которую не разобрали, нельзя записать ни в «хорошо», ни в «плохо»."""
    db = SessionLocal()
    try:
        headers = _setup(db, "0002", {
            "Заключение": {"value": "отрицательно", "range": "отриц."},
            "Глюкоза": {"value": "5.1", "unit": "ммоль/л", "range": "3.9-6.1"},
        })
    finally:
        db.close()
    item = client.get("/api/patient/labs", headers=headers).json()[0]
    assert (item["in_range"], item["out_of_range"]) == (1, 0)


def test_pending_order_has_no_counts(client):
    db = SessionLocal()
    try:
        headers = _setup(db, "0003", None)
    finally:
        db.close()
    item = client.get("/api/patient/labs", headers=headers).json()[0]
    assert (item["in_range"], item["out_of_range"]) == (0, 0)
