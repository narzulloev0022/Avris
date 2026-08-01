"""Дневник питания: разбор ответа модели, тариф, границы и итоги дня.

Живой вызов зрения здесь не проверить (нужен ключ Anthropic), поэтому разбор
ответа вынесен в чистую функцию и покрыт отдельно — именно там ошибка тише
всего и дороже всего: пациент увидит выдуманные калории и не усомнится.
"""
from datetime import datetime, timedelta

import pytest

from database import SessionLocal
from models import NutritionEntry, PatientAccount
from patient_auth import create_patient_access_token
from patient_nutrition import NutritionParseError, parse_nutrition


# ---------- разбор ответа модели ----------

def test_parses_plain_json():
    parsed = parse_nutrition('{"is_food": true, "title": "Плов", '
                             '"items": [{"name": "Плов", "grams": 250, "kcal": 520}], '
                             '"kcal": 520, "protein_g": 22.5, "fat_g": 18.0, '
                             '"carbs_g": 60.0, "confidence": "medium"}')
    assert parsed["title"] == "Плов"
    assert parsed["kcal"] == 520
    assert parsed["items"][0]["grams"] == 250
    assert parsed["confidence"] == "medium"


def test_parses_json_wrapped_in_fence():
    parsed = parse_nutrition('```json\n{"is_food": true, "title": "Суп", "items": [], '
                             '"kcal": 180, "confidence": "high"}\n```')
    assert parsed["kcal"] == 180


def test_not_food_is_recognised():
    assert parse_nutrition('{"is_food": false}') == {"is_food": False}


def test_total_is_summed_when_model_forgets_it():
    parsed = parse_nutrition('{"is_food": true, "title": "Обед", "items": ['
                             '{"name": "Суп", "kcal": 200}, {"name": "Хлеб", "kcal": 90}]}')
    assert parsed["kcal"] == 290


def test_absurd_numbers_are_dropped():
    # Галлюцинация модели не должна доехать до дневника пациента как факт.
    parsed = parse_nutrition('{"is_food": true, "title": "Тарелка", "items": [], '
                             '"kcal": 800, "protein_g": 99999, "fat_g": -5}')
    assert parsed["protein_g"] is None
    assert parsed["fat_g"] is None


def test_unknown_confidence_falls_back_to_low():
    parsed = parse_nutrition('{"is_food": true, "title": "Х", "items": [], '
                             '"kcal": 100, "confidence": "абсолютная"}')
    assert parsed["confidence"] == "low"


@pytest.mark.parametrize("bad", ["", "не еда, а текст", '{"kcal": "много"}', "{"])
def test_garbage_raises(bad):
    with pytest.raises(NutritionParseError):
        parse_nutrition(bad)


# ---------- доступ по тарифу и границы ----------

def _account(db, phone, tier):
    account = PatientAccount(phone=phone, avris_patient_id=f"AV-NUT-{phone[-4:]}",
                             full_name="Питающаяся Проба", subscription_tier=tier)
    if tier != "free":
        account.subscription_expires_at = datetime.utcnow() + timedelta(days=30)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _token(account):
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


@pytest.mark.parametrize("tier,expected", [("free", 402), ("plus", 402), ("pro", 200)])
def test_diary_is_pro_only(client, tier, expected):
    db = SessionLocal()
    try:
        account = _account(db, f"+99290000{hash(tier) % 9000 + 1000}", tier)
        headers = _token(account)
    finally:
        db.close()
    assert client.get("/api/patient/nutrition", headers=headers).status_code == expected


def test_day_totals_and_isolation(client):
    db = SessionLocal()
    try:
        mine = _account(db, "+992900001111", "pro")
        other = _account(db, "+992900002222", "pro")
        today = datetime.utcnow()
        db.add(NutritionEntry(patient_account_id=mine.id, eaten_at=today, title="Завтрак",
                              items=[], kcal=400, protein_g=20, fat_g=10, carbs_g=50))
        db.add(NutritionEntry(patient_account_id=mine.id, eaten_at=today, title="Обед",
                              items=[], kcal=650, protein_g=30, fat_g=25, carbs_g=70))
        # Чужая запись за тот же день — в мой итог попасть не должна.
        db.add(NutritionEntry(patient_account_id=other.id, eaten_at=today, title="Чужое",
                              items=[], kcal=9000))
        db.commit()
        headers = _token(mine)
    finally:
        db.close()

    body = client.get("/api/patient/nutrition", headers=headers).json()
    assert body["total_kcal"] == 1050
    assert body["total_protein_g"] == 50.0
    assert len(body["entries"]) == 2
    assert all(e["title"] != "Чужое" for e in body["entries"])


def test_manual_entry_and_delete(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992900003333", "pro")
        headers = _token(account)
    finally:
        db.close()

    created = client.post("/api/patient/nutrition", headers=headers,
                          json={"title": "Каша", "kcal": 320, "protein_g": 12})
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]
    assert created.json()["source"] == "manual"

    assert client.get("/api/patient/nutrition", headers=headers).json()["total_kcal"] == 320
    assert client.delete(f"/api/patient/nutrition/{entry_id}", headers=headers).status_code == 204
    assert client.get("/api/patient/nutrition", headers=headers).json()["total_kcal"] == 0


def test_cannot_delete_someone_elses_entry(client):
    db = SessionLocal()
    try:
        mine = _account(db, "+992900004444", "pro")
        other = _account(db, "+992900005555", "pro")
        entry = NutritionEntry(patient_account_id=other.id, eaten_at=datetime.utcnow(),
                               title="Чужой ужин", items=[], kcal=500)
        db.add(entry)
        db.commit()
        entry_id = entry.id
        headers = _token(mine)
    finally:
        db.close()

    assert client.delete(f"/api/patient/nutrition/{entry_id}", headers=headers).status_code == 404

    db = SessionLocal()
    try:
        assert db.query(NutritionEntry).filter_by(id=entry_id).count() == 1
    finally:
        db.close()


def test_photo_rejects_non_image(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992900006666", "pro")
        headers = _token(account)
    finally:
        db.close()
    resp = client.post("/api/patient/nutrition/photo", headers=headers,
                       files={"file": ("note.txt", b"hello", "text/plain")})
    assert resp.status_code == 415
