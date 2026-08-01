"""AI-инсайты по визитам: разбор ответа модели, тариф, границы и кэш.

Живой вызов модели здесь не проверить, поэтому разбор вынесен в чистую
функцию и покрыт отдельно. Особое внимание — фильтру вероятностных
диагнозов: промпт их запрещает, но пациенту нельзя показать «вероятно, у
вас…» даже если модель однажды нарушит запрет.
"""
from datetime import datetime, timedelta

import pytest

from database import SessionLocal
from models import (Consultation, Patient, PatientAccount, PatientLink, User,
                    VisitInsight)
from patient_auth import create_patient_access_token
from patient_insights import InsightParseError, parse_insight


# ---------- разбор ответа модели ----------

def test_parses_plain_json():
    parsed = parse_insight('{"picture": "Врачи возвращаются к давлению.", '
                           '"watch": ["головокружение"], "questions": ["повторить ЭКГ?"]}')
    assert parsed["picture"].startswith("Врачи")
    assert parsed["watch"] == ["головокружение"]
    assert parsed["questions"] == ["повторить ЭКГ?"]


def test_parses_json_in_fence():
    parsed = parse_insight('```json\n{"picture": "Картина стабильная.", "watch": [], '
                           '"questions": []}\n```')
    assert parsed["picture"] == "Картина стабильная."


def test_empty_lists_are_allowed():
    parsed = parse_insight('{"picture": "Один вывод."}')
    assert parsed["watch"] == [] and parsed["questions"] == []


def test_blank_items_are_dropped_and_list_is_capped():
    parsed = parse_insight('{"picture": "Есть картина.", "watch": ["", "  ", "давление", '
                           '"сон", "вес", "пульс", "аппетит", "шаги"]}')
    assert parsed["watch"] == ["давление", "сон", "вес", "пульс", "аппетит"]


@pytest.mark.parametrize("phrase", [
    "Вероятно, у вас гипертония.",
    "Скорее всего, у вас анемия — сдайте кровь.",
    "Похоже, у вас проблемы со щитовидкой.",
])
def test_probable_diagnosis_in_picture_is_refused(phrase):
    with pytest.raises(InsightParseError):
        parse_insight('{"picture": "%s", "watch": [], "questions": []}' % phrase)


def test_probable_diagnosis_in_list_is_refused():
    with pytest.raises(InsightParseError):
        parse_insight('{"picture": "Нормальный текст.", '
                      '"watch": ["вероятно, у вас диабет"], "questions": []}')


@pytest.mark.parametrize("bad", ["", "   ", "просто текст без json", "{", '{"watch": []}',
                                 '{"picture": "   "}', '["не объект"]'])
def test_garbage_raises(bad):
    with pytest.raises(InsightParseError):
        parse_insight(bad)


# ---------- доступ, границы, кэш ----------

def _linked_account(db, phone, tier, visits: int):
    """Аккаунт с картой врача и заданным числом визитов."""
    doctor = User(email=f"doc{phone[-4:]}@avris.test", password_hash="x",
                  full_name="Врач Пробный", is_verified=True)
    db.add(doctor)
    db.commit()
    db.refresh(doctor)

    patient = Patient(doctor_id=doctor.id, full_name="Пациент Пробный", age=40)
    db.add(patient)
    db.commit()
    db.refresh(patient)

    account = PatientAccount(phone=phone, avris_patient_id=f"AV-INS-{phone[-4:]}",
                             full_name="Пациент Пробный", subscription_tier=tier)
    if tier != "free":
        account.subscription_expires_at = datetime.utcnow() + timedelta(days=30)
    db.add(account)
    db.commit()
    db.refresh(account)

    db.add(PatientLink(patient_account_id=account.id, patient_id=patient.id,
                       doctor_id=doctor.id))
    for i in range(visits):
        db.add(Consultation(patient_id=patient.id, doctor_id=doctor.id,
                            soap_s=f"Жалоба {i}", soap_p=f"План {i}",
                            created_at=datetime.utcnow() - timedelta(days=30 * (visits - i))))
    db.commit()
    return account


def _token(account):
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


@pytest.mark.parametrize("tier,expected", [("free", 402), ("plus", 409), ("pro", 409)])
def test_gate_is_plus_and_above(client, tier, expected):
    """free упирается в тариф; платные проходят гейт и упираются уже в
    отсутствие истории (409) — а не в оплату."""
    db = SessionLocal()
    try:
        account = _linked_account(db, f"+99291000{abs(hash(tier)) % 9000 + 1000}", tier, visits=1)
        headers = _token(account)
    finally:
        db.close()
    assert client.post("/api/patient/insights", headers=headers).status_code == expected


def test_read_returns_nothing_before_first_build(client):
    db = SessionLocal()
    try:
        account = _linked_account(db, "+992911110001", "plus", visits=2)
        headers = _token(account)
    finally:
        db.close()
    resp = client.get("/api/patient/insights", headers=headers)
    assert resp.status_code == 200
    assert resp.json() is None


def test_read_is_gated_by_tier(client):
    db = SessionLocal()
    try:
        account = _linked_account(db, "+992911110002", "free", visits=2)
        headers = _token(account)
    finally:
        db.close()
    assert client.get("/api/patient/insights", headers=headers).status_code == 402


def test_saved_insight_is_returned_and_isolated(client):
    db = SessionLocal()
    try:
        mine = _linked_account(db, "+992911110003", "plus", visits=2)
        other = _linked_account(db, "+992911110004", "plus", visits=2)
        db.add(VisitInsight(patient_account_id=mine.id, source_key="k1",
                            picture="Ваша картина", watch=["сон"],
                            questions=["когда повтор?"], visits_used=2))
        db.add(VisitInsight(patient_account_id=other.id, source_key="k2",
                            picture="Чужая картина", watch=[], questions=[], visits_used=2))
        db.commit()
        headers = _token(mine)
    finally:
        db.close()

    body = client.get("/api/patient/insights", headers=headers).json()
    assert body["picture"] == "Ваша картина"
    assert body["visits_used"] == 2


def test_cached_insight_is_served_without_calling_the_model(client, monkeypatch):
    """Совпал отпечаток истории — модель не зовём. Иначе каждое открытие
    экрана было бы новым оплаченным вызовом."""
    import patient_insights as pi

    db = SessionLocal()
    try:
        account = _linked_account(db, "+992911110005", "plus", visits=3)
        visits, labs = pi._build_context(db, account, [l.patient_id for l in account_links(db, account)])
        key = pi._source_key(visits, labs)
        db.add(VisitInsight(patient_account_id=account.id, source_key=key,
                            picture="Сохранённая картина", watch=[], questions=[],
                            visits_used=3))
        db.commit()
        headers = _token(account)
    finally:
        db.close()

    async def _boom(*a, **kw):  # pragma: no cover — не должен вызваться
        raise AssertionError("модель не должна вызываться при совпавшем ключе")

    monkeypatch.setattr(pi, "_claude_call", _boom)
    body = client.post("/api/patient/insights", headers=headers).json()
    assert body["picture"] == "Сохранённая картина"


def account_links(db, account):
    return db.query(PatientLink).filter(
        PatientLink.patient_account_id == account.id,
        PatientLink.revoked_at.is_(None)).all()


def test_stale_cache_is_rebuilt_and_replaced(client, monkeypatch):
    """История изменилась — старый разбор заменяется новым, а не копится."""
    import patient_insights as pi

    db = SessionLocal()
    try:
        account = _linked_account(db, "+992911110006", "plus", visits=2)
        db.add(VisitInsight(patient_account_id=account.id, source_key="устарел",
                            picture="Старая картина", watch=[], questions=[], visits_used=2))
        db.commit()
        headers = _token(account)
        account_id = account.id
    finally:
        db.close()

    async def _fake(system, user, **kw):
        return '{"picture": "Новая картина", "watch": ["вес"], "questions": ["когда контроль?"]}'

    monkeypatch.setattr(pi, "_claude_call", _fake)
    body = client.post("/api/patient/insights", headers=headers).json()
    assert body["picture"] == "Новая картина"

    db = SessionLocal()
    try:
        rows = db.query(VisitInsight).filter(
            VisitInsight.patient_account_id == account_id).all()
        assert len(rows) == 1, "старый разбор должен быть удалён, а не накапливаться"
    finally:
        db.close()


def test_unparseable_model_reply_is_not_shown(client, monkeypatch):
    import patient_insights as pi

    db = SessionLocal()
    try:
        account = _linked_account(db, "+992911110007", "plus", visits=2)
        headers = _token(account)
        account_id = account.id
    finally:
        db.close()

    async def _fake(system, user, **kw):
        return "Вероятно, у вас гипертония — принимайте эналаприл."

    monkeypatch.setattr(pi, "_claude_call", _fake)
    assert client.post("/api/patient/insights", headers=headers).status_code == 502

    db = SessionLocal()
    try:
        assert db.query(VisitInsight).filter(
            VisitInsight.patient_account_id == account_id).count() == 0
    finally:
        db.close()


def test_without_linked_card_there_is_nothing_to_analyse(client):
    db = SessionLocal()
    try:
        account = PatientAccount(phone="+992911110008", avris_patient_id="AV-INS-0008",
                                 full_name="Без карты", subscription_tier="plus",
                                 subscription_expires_at=datetime.utcnow() + timedelta(days=30))
        db.add(account)
        db.commit()
        db.refresh(account)
        headers = _token(account)
    finally:
        db.close()
    assert client.post("/api/patient/insights", headers=headers).status_code == 409
