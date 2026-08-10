"""Демо-кейс для пациентского приложения: связанный пациент с прожитой историей.

Дополняет seed_demo_doctor.py. Тот заводит врача «Др. Демо Каримов», этот —
всё, что пациент видит в приложении: связь с врачом, состоявшийся приём с
человеческой сводкой и назначениями, готовый анализ с результатами и
заметку врачу.

Зачем отдельный скрипт: демо-состояние жило только в чьей-то запущенной базе и
исчезло вместе с ней. Теперь его можно поднять одной командой на любой БД.

Идемпотентно — повторный запуск ничего не задваивает.

Запуск (из каталога backend, на ТОЙ ЖЕ БД, что и сервер):

    .venv/bin/python seed_demo_doctor.py
    .venv/bin/python seed_patient_demo.py --phone +992900000009

Данные вымышленные: совпадения с реальными людьми случайны.
"""
import argparse
import os
import sys
import uuid
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal, init_db  # noqa: E402
from models import (Consultation, LabOrder, Patient, PatientAccount,  # noqa: E402
                    PatientLink, PatientPreVisitNote, User, VisitSummary)
from patient_ids import new_avris_patient_id  # noqa: E402

DOCTOR_EMAIL = "demo.doctor@avris.ai"

PROFILE = dict(
    full_name="Носирова Мехрангез",
    date_of_birth=date(1992, 3, 14),
    gender="female",
    height=164.0,
    weight=58.5,
    blood_type="A(II) Rh+",
    allergies=["Пенициллин"],
    chronic_conditions=["Гипотиреоз"],
)

SOAP = dict(
    soap_s="Жалобы на кашель третью неделю, слабость, вечером температура до 37.4.",
    soap_o="Состояние удовлетворительное. Т 37.1. В лёгких дыхание везикулярное, "
           "хрипов нет. ЧД 17, ЧСС 82, АД 115/75.",
    soap_a="ОРВИ, лёгкое течение. Динамика положительная.",
    soap_p="Обильное тёплое питьё. Амброксол 30 мг 3 раза в день после еды, 7 дней. "
           "Контрольный приём через неделю или раньше при ухудшении.",
)

SUMMARY = ("Вы были на приёме с жалобами на кашель и слабость. Врач осмотрел вас — "
           "состояние улучшается, температура почти в норме. Это ОРВИ (простуда), "
           "лёгкое течение. Продолжайте лечение и приходите на контроль через неделю, "
           "а если станет хуже — раньше.")

PRESCRIPTIONS = ("Амброксол 30 мг — 3 раза в день после еды, 7 дней\n"
                 "Обильное тёплое питьё\n"
                 "Контрольный приём через неделю")

LAB_TESTS = ["Гемоглобин", "Лейкоциты", "Тромбоциты", "Глюкоза"]
# Ключи ровно те, что пишет портал лаборатории (lab.html, data-f): value/unit/range.
# Норму лаборант вводит свободной строкой — здесь она такая же, как в жизни.
LAB_RESULTS = {
    "Гемоглобин": {"value": "128", "unit": "г/л", "range": "120–150"},
    "Лейкоциты": {"value": "9.8", "unit": "×10⁹/л", "range": "4.0–9.0"},
    "Тромбоциты": {"value": "265", "unit": "×10⁹/л", "range": "180–320"},
    "Глюкоза": {"value": "5.1", "unit": "ммоль/л", "range": "3.9–6.1"},
}
LAB_COMMENT = ("Лейкоциты незначительно выше нормы — картина согласуется с недавней "
               "вирусной инфекцией. Остальные показатели в пределах нормы. "
               "Обсудите результат с врачом на контрольном приёме.")

NOTE = ("Кашель не проходит третью неделю, ночью сильнее. Спросить, можно ли делать "
        "прививку и нужно ли повторно сдавать кровь.")


def _get_or_create_account(db, phone: str) -> PatientAccount:
    account = db.query(PatientAccount).filter(PatientAccount.phone == phone).first()
    if account is None:
        account = PatientAccount(phone=phone, avris_patient_id=new_avris_patient_id(db))
        db.add(account)
    for field, value in PROFILE.items():
        setattr(account, field, value)
    # Согласие — обязательное условие связи с врачом; ставим только если его нет,
    # чтобы не переписывать юридически значимую отметку времени.
    if account.consent_doctors_at is None:
        account.consent_doctors_at = datetime.utcnow()
        account.consent_version = "1.0"
    db.commit()
    db.refresh(account)
    return account


def _get_or_create_link(db, account: PatientAccount, doctor: User) -> Patient:
    link = db.query(PatientLink).filter(
        PatientLink.patient_account_id == account.id,
        PatientLink.doctor_id == doctor.id,
    ).first()
    if link is not None:
        link.revoked_at = None
        db.commit()
        return db.query(Patient).filter(Patient.id == link.patient_id).first()

    patient = Patient(
        doctor_id=doctor.id,
        full_name=PROFILE["full_name"],
        date_of_birth=PROFILE["date_of_birth"],
        age=34,
        gender="Ж",
        blood_type=PROFILE["blood_type"],
        height=PROFILE["height"],
        weight=PROFILE["weight"],
        bmi="21.8",
        initials="НМ",
        department="therapy",
        status="stable",
        patient_type="outpatient",
        allergies=list(PROFILE["allergies"]),
        diagnoses=list(PROFILE["chronic_conditions"]),
    )
    db.add(patient)
    db.flush()
    db.add(PatientLink(patient_account_id=account.id, patient_id=patient.id,
                       doctor_id=doctor.id, method="qr"))
    db.commit()
    db.refresh(patient)
    return patient


def _seed_visit(db, account: PatientAccount, patient: Patient, doctor: User, when: datetime):
    consultation = (db.query(Consultation)
                    .filter(Consultation.patient_id == patient.id)
                    .order_by(Consultation.id.desc()).first())
    if consultation is None:
        consultation = Consultation(doctor_id=doctor.id, patient_id=patient.id,
                                    language="ru", visit_type="visit", **SOAP)
        db.add(consultation)
        db.flush()
        consultation.created_at = when
        db.commit()
        db.refresh(consultation)

    summary = db.query(VisitSummary).filter(
        VisitSummary.consultation_id == consultation.id).first()
    if summary is None:
        db.add(VisitSummary(
            consultation_id=consultation.id,
            patient_account_id=account.id,
            summary=SUMMARY,
            prescriptions=PRESCRIPTIONS,
            language="ru",
            model="seed",
        ))
        db.commit()
    return consultation


def _seed_lab(db, patient: Patient, doctor: User, when: datetime):
    order = db.query(LabOrder).filter(LabOrder.patient_id == patient.id).first()
    if order is not None:
        # Догоняем показатели: скрипт сходится к эталону, а не «уже есть — не трогаю».
        order.results = dict(LAB_RESULTS)
        order.ai_comment = LAB_COMMENT
        db.commit()
        return order
    order = LabOrder(
        patient_id=patient.id,
        doctor_id=doctor.id,
        qr_token=str(uuid.uuid4()),
        tests=LAB_TESTS,
        status="received",
        results=LAB_RESULTS,
        ai_comment=LAB_COMMENT,
    )
    db.add(order)
    db.flush()
    order.created_at = when
    order.received_at = when + timedelta(hours=4)
    db.commit()
    return order


def _seed_note(db, account: PatientAccount):
    existing = db.query(PatientPreVisitNote).filter(
        PatientPreVisitNote.patient_account_id == account.id,
        PatientPreVisitNote.seen_at.is_(None),
    ).first()
    if existing is not None:
        return existing
    note = PatientPreVisitNote(patient_account_id=account.id, note_text=NOTE)
    db.add(note)
    db.commit()
    return note


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", default="+992900000009",
                    help="телефон демо-пациента (тот же, что в DEV_AUTOLOGIN)")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        doctor = db.query(User).filter(User.email == DOCTOR_EMAIL).first()
        if doctor is None:
            print(f"Врач {DOCTOR_EMAIL} не найден — сначала запустите seed_demo_doctor.py")
            return 1

        account = _get_or_create_account(db, args.phone)
        patient = _get_or_create_link(db, account, doctor)
        when = datetime.utcnow() - timedelta(days=14)
        consultation = _seed_visit(db, account, patient, doctor, when)
        order = _seed_lab(db, patient, doctor, when)
        _seed_note(db, account)

        print(f"пациент      {account.avris_patient_id}  {account.full_name}  ({args.phone})")
        print(f"врач         {doctor.full_name}  (id={doctor.id})")
        print(f"карточка     patients.id={patient.id}")
        print(f"приём        consultations.id={consultation.id}  сводка + {len(PRESCRIPTIONS.splitlines())} назначения")
        print(f"анализ       lab_orders.id={order.id}  {', '.join(LAB_TESTS)}")
        print("заметка      активная, ждёт приёма")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
