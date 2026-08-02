"""Фото профиля пациента: загрузка, отдача, удаление, границы.

Аватар — единственное место, где пациент кладёт на сервер файл, который там
и остаётся (фото еды разбирается и выбрасывается). Поэтому здесь проверяется
не только «работает», но и «не принимает что попало» и «уходит вместе с
аккаунтом».
"""
import io

from database import SessionLocal
from models import PatientAccount, PatientAvatar
from patient_auth import create_patient_access_token

# Минимальный валидный PNG 1×1 — содержимое неважно, важен тип.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a"
    "49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _account(db, suffix):
    account = PatientAccount(phone=f"+9929410{suffix}", avris_patient_id=f"AV-AVA-{suffix}",
                             full_name="Фото Проба")
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _headers(account):
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


def _upload(client, headers, data=PNG_1PX, content_type="image/png"):
    return client.put(
        "/api/patient/profile/avatar",
        files={"file": ("avatar.png", io.BytesIO(data), content_type)},
        headers=headers,
    )


def test_upload_then_read_then_delete(client):
    db = SessionLocal()
    try:
        account = _account(db, "0001")
        headers = _headers(account)
    finally:
        db.close()

    # Пока фото нет — профиль честно молчит, а картинка отдаёт 404.
    assert client.get("/api/patient/profile", headers=headers).json()["avatar_updated_at"] is None
    assert client.get("/api/patient/profile/avatar", headers=headers).status_code == 404

    body = _upload(client, headers).json()
    assert body["avatar_updated_at"] is not None

    got = client.get("/api/patient/profile/avatar", headers=headers)
    assert got.status_code == 200
    assert got.content == PNG_1PX
    assert got.headers["content-type"].startswith("image/png")
    # Медданные не должны оседать в общих кэшах.
    assert "private" in got.headers["cache-control"]

    after = client.delete("/api/patient/profile/avatar", headers=headers).json()
    assert after["avatar_updated_at"] is None
    assert client.get("/api/patient/profile/avatar", headers=headers).status_code == 404


def test_delete_without_photo_is_not_an_error(client):
    """Удалять нечего — это не ошибка: кнопка не должна падать на повторный тап."""
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0002"))
    finally:
        db.close()
    assert client.delete("/api/patient/profile/avatar", headers=headers).status_code == 200


def test_second_upload_replaces_the_first(client):
    """Фото ровно одно: вторая загрузка заменяет, а не копит строки."""
    db = SessionLocal()
    try:
        account = _account(db, "0003")
        headers = _headers(account)
        account_id = account.id
    finally:
        db.close()

    _upload(client, headers)
    _upload(client, headers, data=PNG_1PX + b"\x00", content_type="image/jpeg")

    db = SessionLocal()
    try:
        rows = db.query(PatientAvatar).filter(PatientAvatar.patient_id == account_id).all()
        assert len(rows) == 1
        assert rows[0].content_type == "image/jpeg"
    finally:
        db.close()


def test_rejects_non_image_and_empty(client):
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0004"))
    finally:
        db.close()
    assert _upload(client, headers, data=b"%PDF-1.4", content_type="application/pdf").status_code == 415
    assert _upload(client, headers, data=b"").status_code == 400


def test_avatar_is_private_to_its_owner(client):
    """Чужого фото не существует: адресовать можно только своё."""
    db = SessionLocal()
    try:
        mine = _account(db, "0005")
        other = _account(db, "0006")
        mine_headers, other_headers = _headers(mine), _headers(other)
    finally:
        db.close()

    _upload(client, mine_headers)
    # У соседа своего фото нет — и путь к чужому попросту не существует.
    assert client.get("/api/patient/profile/avatar", headers=other_headers).status_code == 404


def test_avatar_dies_with_the_account(client):
    """Удаление аккаунта должно уносить и файл — иначе останется сирота."""
    db = SessionLocal()
    try:
        account = _account(db, "0007")
        headers = _headers(account)
        account_id = account.id
    finally:
        db.close()

    _upload(client, headers)
    client.delete("/api/patient/account", headers=headers)

    db = SessionLocal()
    try:
        assert db.query(PatientAvatar).filter(
            PatientAvatar.patient_id == account_id).count() == 0
    finally:
        db.close()
