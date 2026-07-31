"""Удаление аккаунта пациента уносит его данные, а не только строку аккаунта.

Экран «Удалить аккаунт» обещает, что восстановления не будет. Полагаться на
ondelete="CASCADE" для этого нельзя: каскад исполняет БАЗА, а SQLite по
умолчанию внешние ключи не проверяет вовсе — заметки, разговоры с AI и сводки
визитов оставались лежать после удаления.
"""
from datetime import datetime

from database import SessionLocal
from models import (AssistantUsage, PatientAccount, PatientConversation,
                    PatientMessage, PatientPreVisitNote, PatientRefreshToken,
                    VisitSummary)
from patient_auth import create_patient_access_token


def _account(db, phone="+992900000777"):
    account = PatientAccount(phone=phone, avris_patient_id="AV-DEL-TEST",
                             full_name="Тестовая Пациентка")
    db.add(account)
    db.flush()
    return account


def test_delete_account_removes_patient_data(client):
    db = SessionLocal()
    try:
        account = _account(db)
        conversation = PatientConversation(patient_account_id=account.id, kind="assistant")
        db.add(conversation)
        db.flush()
        db.add(PatientMessage(conversation_id=conversation.id, role="user",
                              text="Болит горло третий день"))
        db.add(PatientPreVisitNote(patient_account_id=account.id,
                                   note_text="Спросить про прививку"))
        db.add(AssistantUsage(patient_account_id=account.id, day="2026-08-01", count=2))
        db.add(PatientRefreshToken(patient_account_id=account.id, jti="delete-test-jti",
                                   expires_at=datetime.utcnow()))
        db.commit()
        account_id = account.id
        conversation_id = conversation.id
        token = create_patient_access_token(account_id)
    finally:
        db.close()

    resp = client.delete("/api/patient/account",
                         headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204

    db = SessionLocal()
    try:
        assert db.query(PatientAccount).filter_by(id=account_id).count() == 0
        for model in (PatientConversation, PatientPreVisitNote, AssistantUsage,
                      PatientRefreshToken):
            left = db.query(model).filter_by(patient_account_id=account_id).count()
            assert left == 0, f"{model.__name__}: осталось {left} строк"
        left_messages = db.query(PatientMessage).filter_by(
            conversation_id=conversation_id).count()
        assert left_messages == 0, "реплики разговора пережили удаление аккаунта"
    finally:
        db.close()


def test_delete_account_keeps_doctor_records(client):
    """Сводка визита принадлежит пациенту и уходит, а запись приёма у врача —
    его медицинская документация и остаётся. Проверяем, что удаление не
    промахивается мимо этой границы."""
    db = SessionLocal()
    try:
        account = _account(db, phone="+992900000778")
        db.add(VisitSummary(consultation_id=999, patient_account_id=account.id,
                            summary="Сводка для пациента", language="ru", model="test"))
        db.commit()
        account_id = account.id
        token = create_patient_access_token(account_id)
    finally:
        db.close()

    assert client.delete("/api/patient/account",
                         headers={"Authorization": f"Bearer {token}"}).status_code == 204

    db = SessionLocal()
    try:
        assert db.query(VisitSummary).filter_by(patient_account_id=account_id).count() == 0
    finally:
        db.close()
