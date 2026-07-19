"""AI-ассистент пациента: гардрейлы промпта, дневной кап, auth, мок Claude."""
import os

os.environ.setdefault("PATIENT_DEV_OTP", "424242")

import pytest
from fastapi.testclient import TestClient

import patient_assistant as pa_module

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


@pytest.fixture()
def fake_claude(monkeypatch):
    captured = {}

    async def _fake(system_prompt, user_msg, max_tokens=1024):
        captured["system"] = system_prompt
        captured["user"] = user_msg
        return "Понимаю вас. Когда началась головная боль? Если боль внезапная и очень сильная — позвоните в 103."

    monkeypatch.setattr(pa_module, "_claude_call", _fake)
    return captured


def _chat(client, headers, messages, language="ru"):
    return client.post("/api/patient/assistant",
                       json={"messages": messages, "language": language},
                       headers=headers)


def test_requires_auth(client):
    r = client.post("/api/patient/assistant",
                    json={"messages": [{"role": "user", "text": "Привет"}]})
    assert r.status_code in (401, 403)


def test_happy_path_and_prompt_guardrails(client, fake_claude):
    h = _auth(client, "+992905000001")
    r = _chat(client, h, [{"role": "user", "text": "У меня болит голова"}])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "головная боль" in body["reply"]
    assert body["remaining"] == pa_module.DAILY_CAP - 1

    # Гардрейлы в системном промпте
    sys_p = fake_claude["system"]
    assert "не ставь диагнозы" in sys_p.lower() or "НИКОГДА не ставь диагнозы" in sys_p
    assert "103" in sys_p
    assert "не назначай лекарства" in sys_p.lower() or "НИКОГДА не назначай" in sys_p
    # История и язык дошли до модели
    assert "болит голова" in fake_claude["user"]
    assert "русском" in fake_claude["user"]


def test_history_passed(client, fake_claude):
    h = _auth(client, "+992905000002")
    msgs = [
        {"role": "user", "text": "Кашель третий день"},
        {"role": "assistant", "text": "Есть ли температура?"},
        {"role": "user", "text": "Да, 37.8"},
    ]
    r = _chat(client, h, msgs)
    assert r.status_code == 200
    assert "Кашель третий день" in fake_claude["user"]
    assert "37.8" in fake_claude["user"]


def test_last_message_must_be_user(client, fake_claude):
    h = _auth(client, "+992905000003")
    r = _chat(client, h, [{"role": "assistant", "text": "Здравствуйте"}])
    assert r.status_code == 422


def test_daily_cap(client, fake_claude, monkeypatch):
    monkeypatch.setattr(pa_module, "DAILY_CAP", 3)
    h = _auth(client, "+992905000004")
    for i in range(3):
        r = _chat(client, h, [{"role": "user", "text": f"Вопрос {i}"}])
        assert r.status_code == 200
        assert r.json()["remaining"] == 3 - i - 1
    r = _chat(client, h, [{"role": "user", "text": "Ещё вопрос"}])
    assert r.status_code == 429


def test_cap_is_per_account(client, fake_claude, monkeypatch):
    monkeypatch.setattr(pa_module, "DAILY_CAP", 1)
    h1 = _auth(client, "+992905000005")
    h2 = _auth(client, "+992905000006")
    assert _chat(client, h1, [{"role": "user", "text": "Вопрос"}]).status_code == 200
    assert _chat(client, h1, [{"role": "user", "text": "Вопрос"}]).status_code == 429
    # Другой аккаунт — свой счётчик
    assert _chat(client, h2, [{"role": "user", "text": "Вопрос"}]).status_code == 200


def test_validation_limits(client, fake_claude):
    h = _auth(client, "+992905000007")
    # Пустой список
    assert _chat(client, h, []).status_code == 422
    # Слишком длинное сообщение
    r = _chat(client, h, [{"role": "user", "text": "х" * 3000}])
    assert r.status_code == 422


def test_503_without_key(client):
    """Без мока Claude и без ANTHROPIC_API_KEY (пуст в тестах) — честный 503."""
    h = _auth(client, "+992905000008")
    r = _chat(client, h, [{"role": "user", "text": "Привет"}])
    assert r.status_code == 503
