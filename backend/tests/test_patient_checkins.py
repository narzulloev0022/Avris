"""Отметка самочувствия: одна в день, задним числом только по памяти.

Ценность этой записи в том, что она субъективна и принадлежит пациенту.
Поэтому здесь проверяется не «правильность» уровня, а то, что дневник
остаётся дневником: одна отметка на день, будущим днём не отмечают, а
слишком давним — не помнят.

Вторая половина файла — врачебная сторона. Карточка обещает пациенту «врач
увидит это на приёме», и проверять тут надо в первую очередь границу доступа:
отметки уходят чужому человеку, и только пока согласие живо.
"""
from datetime import date, datetime, timedelta

import llm as llm_module
from conftest import auth_headers
from database import SessionLocal
from models import PatientAccount, PatientCheckin, PatientLink
from patient_auth import create_patient_access_token
from patient_checkins import history_block


def _account(db, suffix):
    account = PatientAccount(phone=f"+9929460{suffix}", avris_patient_id=f"AV-CHK-{suffix}",
                             full_name="Самочувствие Проба")
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _headers(account):
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


def test_mark_and_read_back(client):
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0001"))
    finally:
        db.close()

    out = client.put("/api/patient/checkins",
                     json={"level": 4, "note": "Кашель меньше"}, headers=headers).json()
    assert out["level"] == 4 and out["note"] == "Кашель меньше"
    assert out["day"] == date.today().isoformat()

    assert client.get("/api/patient/checkins", headers=headers).json()[0]["level"] == 4


def test_second_mark_replaces_todays(client):
    """Дневник, который можно вести десять раз в сутки, перестают вести."""
    db = SessionLocal()
    try:
        account = _account(db, "0002")
        headers = _headers(account)
        account_id = account.id
    finally:
        db.close()

    client.put("/api/patient/checkins", json={"level": 2}, headers=headers)
    client.put("/api/patient/checkins", json={"level": 5}, headers=headers)

    db = SessionLocal()
    try:
        rows = db.query(PatientCheckin).filter(
            PatientCheckin.patient_account_id == account_id).all()
        assert len(rows) == 1 and rows[0].level == 5
    finally:
        db.close()


def test_yesterday_is_allowed_but_not_the_future(client):
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0003"))
    finally:
        db.close()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert client.put("/api/patient/checkins", json={"level": 3, "day": yesterday},
                      headers=headers).status_code == 200
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert client.put("/api/patient/checkins", json={"level": 3, "day": tomorrow},
                      headers=headers).status_code == 400


def test_too_old_is_refused(client):
    """Через неделю человек не помнит, как себя чувствовал — это выдумка."""
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0004"))
    finally:
        db.close()
    old = (date.today() - timedelta(days=30)).isoformat()
    assert client.put("/api/patient/checkins", json={"level": 3, "day": old},
                      headers=headers).status_code == 400


def test_level_outside_the_scale_is_refused(client):
    db = SessionLocal()
    try:
        headers = _headers(_account(db, "0005"))
    finally:
        db.close()
    for bad in (0, 6, -1):
        assert client.put("/api/patient/checkins", json={"level": bad},
                          headers=headers).status_code == 422


def test_checkins_are_private(client):
    db = SessionLocal()
    try:
        mine_headers = _headers(_account(db, "0006"))
        other_headers = _headers(_account(db, "0007"))
    finally:
        db.close()
    client.put("/api/patient/checkins", json={"level": 5}, headers=mine_headers)
    assert client.get("/api/patient/checkins", headers=other_headers).json() == []


# ---------- врачебная сторона ----------

def _linked_pair(client, db, doctor, suffix, revoked=False):
    """Пациент с аккаунтом, карточка у врача и связь между ними."""
    account = _account(db, suffix)
    card = client.post("/api/patients/", json={"full_name": "Самочувствие Проба"},
                       headers=auth_headers(doctor)).json()
    db.add(PatientLink(patient_account_id=account.id, patient_id=card["id"],
                       doctor_id=doctor["user"]["id"], method="qr",
                       revoked_at=datetime.utcnow() if revoked else None))
    db.commit()
    return account, card


def _mark(db, account_id, days_ago, level, note=None):
    db.add(PatientCheckin(patient_account_id=account_id,
                          day=date.today() - timedelta(days=days_ago),
                          level=level, note=note))
    db.commit()


def test_doctor_sees_marks_of_a_linked_patient(client, doctor):
    """То самое обещание с карточки: «врач увидит это на приёме»."""
    db = SessionLocal()
    try:
        account, card = _linked_pair(client, db, doctor, "0008")
        _mark(db, account.id, 2, 2, "Слабость третий день")
        _mark(db, account.id, 1, 4)
    finally:
        db.close()

    body = client.get(f"/api/patients/{card['id']}/checkins",
                      headers=auth_headers(doctor)).json()
    assert body["linked"] is True
    assert [d["level"] for d in body["days"]] == [4, 2]  # свежие сверху
    assert body["days"][1]["note"] == "Слабость третий день"


def test_revoked_consent_closes_the_door(client, doctor):
    """Отзыв согласия гасит связь — вместе с ней врач теряет и отметки."""
    db = SessionLocal()
    try:
        account, card = _linked_pair(client, db, doctor, "0009", revoked=True)
        _mark(db, account.id, 1, 1, "Совсем плохо")
    finally:
        db.close()

    body = client.get(f"/api/patients/{card['id']}/checkins",
                      headers=auth_headers(doctor)).json()
    assert body["linked"] is False
    assert body["days"] == []


def test_unlinked_card_is_not_an_error(client, doctor):
    """«Не подключил приложение» и «подключил, но не отмечал» — разные вещи,
    и ни одна из них не сбой."""
    card = client.post("/api/patients/", json={"full_name": "Без приложения"},
                       headers=auth_headers(doctor)).json()
    r = client.get(f"/api/patients/{card['id']}/checkins", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert r.json() == {"linked": False, "days": []}


def test_another_doctor_gets_nothing(client, doctor, second_doctor):
    db = SessionLocal()
    try:
        account, card = _linked_pair(client, db, doctor, "0010")
        _mark(db, account.id, 1, 3, "Личное")
    finally:
        db.close()

    r = client.get(f"/api/patients/{card['id']}/checkins",
                   headers=auth_headers(second_doctor))
    assert r.status_code in (403, 404)
    assert "Личное" not in r.text


# ---------- то, что уходит в модель ----------

def test_history_block_speaks_the_patients_words(client):
    db = SessionLocal()
    try:
        account = _account(db, "0011")
        _mark(db, account.id, 1, 2, "Кашель ночью")
        rows = db.query(PatientCheckin).filter(
            PatientCheckin.patient_account_id == account.id).all()
        block = history_block(rows)
    finally:
        db.close()
    # Словами, а не цифрой: врач читает ту же шкалу, что пациент выбирал.
    assert "так себе" in block
    assert "Кашель ночью" in block
    assert "== САМООЦЕНКА" in block


def test_history_block_is_empty_without_marks():
    """Пустой блок не должен доехать до промпта заголовком без содержимого."""
    assert history_block([]) == ""


def test_brief_carries_the_marks_to_the_model(client, doctor, monkeypatch):
    captured = {}

    async def fake_llm(system_prompt, user_msg, max_tokens=1024):
        captured["system"] = system_prompt
        captured["user"] = user_msg
        return "— Со слов пациента, слабость третий день"

    monkeypatch.setattr(llm_module, "_llm_call", fake_llm)
    db = SessionLocal()
    try:
        account, card = _linked_pair(client, db, doctor, "0012")
        _mark(db, account.id, 3, 2, "Слабость")
    finally:
        db.close()

    r = client.post(f"/api/patients/{card['id']}/previsit-brief",
                    json={"language": "ru"}, headers=auth_headers(doctor))
    assert r.status_code == 200, r.text
    assert "Слабость" in captured["user"]
    # Модели сказано, что это жалоба, а не измерение — иначе она посчитает по
    # субъективной шкале «тяжесть» и врач прочитает это как факт.
    assert "не измерение" in captured["system"]
