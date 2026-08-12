"""История разговоров пациента с AI и пред-визитное интервью.

Разговор — это слова пациента о своём здоровье, то есть PHI: он обязан быть
виден только владельцу и обязан переживать закрытие приложения.
"""
import os

os.environ.setdefault("PATIENT_DEV_OTP", "424242")

import pytest
from fastapi.testclient import TestClient

import patient_assistant as pa_module
import patient_intake as intake_module

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
def fake_assistant(monkeypatch):
    async def _fake(system_prompt, user_msg, max_tokens=1024, **_):
        return "Расскажите подробнее, когда это началось?"

    monkeypatch.setattr(pa_module, "_llm_call", _fake)


def _ask(client, headers, text, conversation_id=None):
    body = {"messages": [{"role": "user", "text": text}], "language": "ru"}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return client.post("/api/patient/assistant", json=body, headers=headers)


class TestConversationHistory:
    def test_assistant_reply_starts_a_conversation(self, client, fake_assistant):
        h = _auth(client, "+992908000001")
        r = _ask(client, h, "Болит голова второй день")
        assert r.status_code == 200, r.text
        cid = r.json()["conversation_id"]
        assert cid is not None

        detail = client.get(f"/api/patient/conversations/{cid}", headers=h).json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        assert detail["messages"][0]["text"] == "Болит голова второй день"

    def test_title_comes_from_the_first_patient_message(self, client, fake_assistant):
        h = _auth(client, "+992908000002")
        cid = _ask(client, h, "Болит голова второй день").json()["conversation_id"]
        rows = client.get("/api/patient/conversations", headers=h).json()
        assert rows[0]["id"] == cid
        assert rows[0]["title"] == "Болит голова второй день"

    def test_long_title_is_cut_on_a_word(self, client, fake_assistant):
        h = _auth(client, "+992908000003")
        long_text = ("Кашель не проходит уже третью неделю особенно сильно "
                     "по ночам и мешает спать всей семье")
        _ask(client, h, long_text)
        title = client.get("/api/patient/conversations", headers=h).json()[0]["title"]
        assert title.endswith("…")
        assert len(title) <= 61
        # Обрубков посреди слова быть не должно.
        assert not title[:-1].endswith(" ")
        assert long_text.startswith(title[:-1].rstrip("…"))

    def test_second_turn_continues_the_same_conversation(self, client, fake_assistant):
        h = _auth(client, "+992908000004")
        cid = _ask(client, h, "Первый вопрос").json()["conversation_id"]
        again = _ask(client, h, "Второй вопрос", conversation_id=cid).json()["conversation_id"]
        assert again == cid
        detail = client.get(f"/api/patient/conversations/{cid}", headers=h).json()
        assert len(detail["messages"]) == 4

    def test_failed_model_call_leaves_no_dangling_conversation(self, client, monkeypatch):
        """Обрыв не должен оставлять в истории вопрос без ответа."""
        from fastapi import HTTPException

        async def _boom(system_prompt, user_msg, max_tokens=1024, **_):
            raise HTTPException(status_code=503, detail="Сервис AI временно недоступен")

        monkeypatch.setattr(pa_module, "_llm_call", _boom)
        h = _auth(client, "+992908000005")
        assert _ask(client, h, "Вопрос").status_code == 503
        assert client.get("/api/patient/conversations", headers=h).json() == []

    def test_conversations_are_private(self, client, fake_assistant):
        h1 = _auth(client, "+992908000006")
        h2 = _auth(client, "+992908000007")
        cid = _ask(client, h1, "Моя жалоба").json()["conversation_id"]
        # Чужой разговор — 404, а не 403: существование чужих не подтверждаем.
        assert client.get(f"/api/patient/conversations/{cid}", headers=h2).status_code == 404
        assert client.get("/api/patient/conversations", headers=h2).json() == []

    def test_patient_can_delete_own_conversation(self, client, fake_assistant):
        h = _auth(client, "+992908000008")
        cid = _ask(client, h, "Сотри это").json()["conversation_id"]
        assert client.delete(f"/api/patient/conversations/{cid}", headers=h).status_code == 204
        assert client.get("/api/patient/conversations", headers=h).json() == []

    def test_stranger_cannot_delete(self, client, fake_assistant):
        h1 = _auth(client, "+992908000009")
        h2 = _auth(client, "+992908000010")
        cid = _ask(client, h1, "Моё").json()["conversation_id"]
        assert client.delete(f"/api/patient/conversations/{cid}", headers=h2).status_code == 404
        assert client.get(f"/api/patient/conversations/{cid}", headers=h1).status_code == 200


class TestIntakeInterview:
    """AI-интервью, из которого рождается заметка врачу."""

    @pytest.fixture()
    def fake_question(self, monkeypatch):
        async def _fake(system_prompt, user_msg, max_tokens=1024, **_):
            return '{"reply": "Когда это началось?", "done": false, "verdict": "ok", "note": null}'

        monkeypatch.setattr(intake_module, "_llm_call", _fake)

    @pytest.fixture()
    def fake_finish(self, monkeypatch):
        async def _fake(system_prompt, user_msg, max_tokens=1024, **_):
            return ('{"reply": "Спасибо, этого достаточно", "done": true, "verdict": "ok",'
                    ' "note": "Кашель 3 недели, ночью сильнее.\\nТемпературы нет.\\n'
                    'Вопрос: можно ли делать прививку"}')

        monkeypatch.setattr(intake_module, "_llm_call", _fake)

    def test_empty_start_asks_first_question(self, client, fake_question):
        h = _auth(client, "+992908000020")
        r = client.post("/api/patient/intake", json={"messages": [], "language": "ru"}, headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["done"] is False
        assert body["conversation_id"] is not None

    def test_interview_lands_in_its_own_history(self, client, fake_question):
        h = _auth(client, "+992908000021")
        client.post("/api/patient/intake",
                    json={"messages": [{"role": "user", "text": "Болит горло"}], "language": "ru"},
                    headers=h)
        # Подготовки к приёму — отдельная история от вопросов о здоровье.
        assert client.get("/api/patient/conversations?kind=intake", headers=h).json() != []
        assert client.get("/api/patient/conversations?kind=assistant", headers=h).json() == []

    def test_finished_interview_returns_a_note(self, client, fake_finish):
        h = _auth(client, "+992908000022")
        r = client.post("/api/patient/intake",
                        json={"messages": [{"role": "user", "text": "Это всё"}], "language": "ru"},
                        headers=h)
        body = r.json()
        assert body["done"] is True
        assert "Кашель 3 недели" in body["note"]

    def test_broken_json_becomes_a_plain_question(self, client, monkeypatch):
        """Сломанный формат ответа модели не должен обрывать разговор."""
        async def _plain(system_prompt, user_msg, max_tokens=1024, **_):
            return "А когда это началось?"

        monkeypatch.setattr(intake_module, "_llm_call", _plain)
        h = _auth(client, "+992908000023")
        body = client.post("/api/patient/intake", json={"messages": [], "language": "ru"},
                           headers=h).json()
        assert body["reply"] == "А когда это началось?"
        assert body["done"] is False

    def test_daily_cap(self, client, fake_question, monkeypatch):
        monkeypatch.setattr(intake_module, "DAILY_CAP", 2)
        h = _auth(client, "+992908000024")
        for _ in range(2):
            assert client.post("/api/patient/intake", json={"messages": []}, headers=h).status_code == 200
        assert client.post("/api/patient/intake", json={"messages": []}, headers=h).status_code == 429

    def test_intake_cap_does_not_eat_assistant_limit(self, client, fake_question, fake_assistant):
        """Подготовка к приёму и вопросы о здоровье считаются раздельно —
        иначе интервью съедало бы бесплатные вопросы пациента."""
        h = _auth(client, "+992908000025")
        client.post("/api/patient/intake", json={"messages": []}, headers=h)
        sub = client.get("/api/patient/subscription", headers=h).json()
        assert sub["assistant_used_today"] == 0
        assert sub["assistant_remaining_today"] == 3

    def test_long_interview_keeps_going(self, client, fake_question):
        """Разговорчивый пациент не должен упираться в стену.

        Раньше 25-е сообщение отвергалось с 422, и интервью вставало намертво:
        пациент видел «не получилось» ровно за то, что подробно рассказывал.
        """
        h = _auth(client, "+992908000026")
        messages = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"реплика {i}"}
                    for i in range(39)]
        messages.append({"role": "user", "text": "и ещё вот что"})
        r = client.post("/api/patient/intake",
                        json={"messages": messages, "language": "ru"}, headers=h)
        assert r.status_code == 200, r.text

    def test_long_interview_keeps_the_first_complaint(self):
        """Урезаем середину, а не начало: первая жалоба — то, ради чего всё и
        затевалось, и модель должна видеть её до конца интервью."""
        msgs = [intake_module.IntakeMessage(role="user", text=f"m{i}") for i in range(60)]
        ctx = intake_module._context(msgs)
        assert len(ctx) == intake_module.MAX_TURNS
        assert ctx[0].text == "m0"
        assert ctx[-1].text == "m59"

    def test_requires_auth(self, client):
        assert client.post("/api/patient/intake", json={"messages": []}).status_code in (401, 403)


class TestSummaryForDoctor:
    """Обычный разговор о самочувствии — половина анамнеза. Собрать его для
    врача можно, но только с ведома пациента."""

    @pytest.fixture()
    def fake_summary(self, monkeypatch):
        import patient_conversations as pc_module

        async def _fake(system_prompt, user_msg, max_tokens=1024, **_):
            assert "Пациент:" in user_msg
            return "Кашель третью неделю, ночью сильнее.\nПринимал сироп.\nВопрос про прививку"

        monkeypatch.setattr(pc_module, "_llm_call", _fake)

    def test_draft_is_built_from_the_conversation(self, client, fake_assistant, fake_summary):
        h = _auth(client, "+992908000030")
        cid = _ask(client, h, "Кашель третью неделю").json()["conversation_id"]
        r = client.post(f"/api/patient/conversations/{cid}/summary-for-doctor", headers=h)
        assert r.status_code == 200, r.text
        assert "Кашель третью неделю" in r.json()["draft"]

    def test_draft_is_not_saved_anywhere(self, client, fake_assistant, fake_summary):
        """Черновик не уходит врачу сам — пациент сначала подтверждает."""
        h = _auth(client, "+992908000031")
        cid = _ask(client, h, "Болит живот").json()["conversation_id"]
        client.post(f"/api/patient/conversations/{cid}/summary-for-doctor", headers=h)
        assert client.get("/api/patient/pre-visit-note", headers=h).json()["note"] is None

    def test_foreign_conversation_is_404(self, client, fake_assistant, fake_summary):
        h1 = _auth(client, "+992908000032")
        h2 = _auth(client, "+992908000033")
        cid = _ask(client, h1, "Моё").json()["conversation_id"]
        r = client.post(f"/api/patient/conversations/{cid}/summary-for-doctor", headers=h2)
        assert r.status_code == 404


class TestIntakeNoteFits:
    """Заметку, собранную интервью, должно быть возможно сохранить.

    Раньше не получалось: интервью отдаёт до 600 символов, а поле заметки
    принимало 300 — пациент проходил разговор до конца и упирался в 422 ровно
    на кнопке «Сохранить».
    """

    def test_long_note_is_accepted(self, client):
        h = _auth(client, "+992908000040")
        note = ("Кашель третью неделю, ночью сильнее. " * 30)[:980]
        r = client.post("/api/patient/pre-visit-note", json={"note_text": note}, headers=h)
        assert r.status_code == 200, r.text
        assert client.get("/api/patient/pre-visit-note", headers=h).json()["note"] is not None

    def test_over_limit_is_still_rejected(self, client):
        h = _auth(client, "+992908000041")
        r = client.post("/api/patient/pre-visit-note", json={"note_text": "х" * 1200}, headers=h)
        assert r.status_code == 422


class TestIntakeCounterIsolation:
    """Счётчик интервью живёт в своей таблице: ключ «intake:YYYY-MM-DD» не
    влезал в VARCHAR(10) и уронил бы Postgres в проде."""

    def test_day_key_fits_the_column(self, client, monkeypatch):
        async def _fake(system_prompt, user_msg, max_tokens=1024, **_):
            return '{"reply": "Когда началось?", "done": false, "verdict": "ok", "note": null}'

        monkeypatch.setattr(intake_module, "_llm_call", _fake)
        h = _auth(client, "+992908000042")
        assert client.post("/api/patient/intake", json={"messages": []}, headers=h).status_code == 200

        from models import IntakeUsage
        import database
        db = database.SessionLocal()
        try:
            row = db.query(IntakeUsage).first()
            assert row is not None
            assert len(row.day) == 10, f"ключ дня должен быть YYYY-MM-DD, а не {row.day!r}"
        finally:
            db.close()
