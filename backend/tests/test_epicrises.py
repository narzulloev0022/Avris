"""Эпикризы: черновик Claude (мок), сохранение версий, scoping, PDF."""
import pytest

from conftest import auth_headers

import epicrises as epi_module


FAKE_DRAFT = """ПАСПОРТНАЯ ЧАСТЬ
Пациент Стационаров С.С., 65 лет.

ДИАГНОЗ ПРИ ПОСТУПЛЕНИИ
Внебольничная пневмония.

ДИНАМИКА СОСТОЯНИЯ
За время наблюдения состояние улучшилось.

СОСТОЯНИЕ ПРИ ВЫПИСКЕ
Стабильное.

РЕКОМЕНДАЦИИ
Контроль ОАК через 10 дней."""


def _mk_patient(client, doctor, **overrides):
    payload = {
        "full_name": "Эпикризов Э.Э.",
        "patient_type": "inpatient",
        "date_of_birth": "1961-03-15",
        "record_number": "ИБ-2026/0777",
        "admission_date": "2026-07-01T10:00:00",
        "admission_diagnosis": "Внебольничная пневмония",
        "admission_status": "serious",
    }
    payload.update(overrides)
    r = client.post("/api/patients/", json=payload, headers=auth_headers(doctor))
    assert r.status_code == 201, r.text
    return r.json()


def _mk_consultation(client, doctor, pid, **overrides):
    payload = {"patient_id": pid, "soap_s": "Кашель, слабость",
               "soap_p": "Цефтриаксон 2г в/в", "visit_type": "daily"}
    payload.update(overrides)
    r = client.post("/api/consultations/", json=payload, headers=auth_headers(doctor))
    assert r.status_code == 201, r.text
    return r.json()


# ---------- Черновик ----------

def test_draft_503_without_api_key(client, doctor):
    """Без ANTHROPIC_API_KEY (в тестах он пуст) — честный 503 от _claude_call."""
    p = _mk_patient(client, doctor)
    r = client.post("/api/epicrises/draft",
                    json={"patient_id": p["id"], "kind": "discharge"},
                    headers=auth_headers(doctor))
    assert r.status_code == 503


def test_draft_with_mocked_claude(client, doctor, monkeypatch):
    p = _mk_patient(client, doctor)
    _mk_consultation(client, doctor, p["id"], visit_type="primary")
    _mk_consultation(client, doctor, p["id"])

    captured = {}

    async def fake_claude(system_prompt, user_msg, max_tokens=1024):
        captured["system"] = system_prompt
        captured["user"] = user_msg
        return FAKE_DRAFT

    monkeypatch.setattr(epi_module, "_claude_call", fake_claude)
    r = client.post("/api/epicrises/draft",
                    json={"patient_id": p["id"], "kind": "discharge", "language": "ru"},
                    headers=auth_headers(doctor))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"] == FAKE_DRAFT
    assert body["counts"]["consultations"] == 2

    # Контекст собран: паспорт, поступление, обе записи, снимок статуса
    assert "Эпикризов Э.Э." in captured["user"]
    assert "ИБ-2026/0777" in captured["user"]
    assert "Внебольничная пневмония" in captured["user"]
    assert "первичный осмотр" in captured["user"]
    assert "Цефтриаксон 2г в/в" in captured["user"]
    # Промпт — выписной, с разделом выписки
    assert "выписной" in captured["system"]
    assert "СОСТОЯНИЕ ПРИ ВЫПИСКЕ" in captured["system"]


def test_draft_interim_prompt(client, doctor, monkeypatch):
    p = _mk_patient(client, doctor)
    captured = {}

    async def fake_claude(system_prompt, user_msg, max_tokens=1024):
        captured["system"] = system_prompt
        return "ТЕКУЩЕЕ СОСТОЯНИЕ\nСтабильное."

    monkeypatch.setattr(epi_module, "_claude_call", fake_claude)
    r = client.post("/api/epicrises/draft",
                    json={"patient_id": p["id"], "kind": "interim"},
                    headers=auth_headers(doctor))
    assert r.status_code == 200
    assert "этапный" in captured["system"]
    assert "ПЛАН ДАЛЬНЕЙШЕГО ЛЕЧЕНИЯ" in captured["system"]


def test_draft_invalid_kind(client, doctor):
    p = _mk_patient(client, doctor)
    r = client.post("/api/epicrises/draft",
                    json={"patient_id": p["id"], "kind": "final"},
                    headers=auth_headers(doctor))
    assert r.status_code == 422


# ---------- Сохранение / версии / scoping ----------

def test_save_list_get(client, doctor):
    p = _mk_patient(client, doctor)
    r = client.post("/api/epicrises/",
                    json={"patient_id": p["id"], "kind": "discharge",
                          "body": FAKE_DRAFT, "language": "ru"},
                    headers=auth_headers(doctor))
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["kind"] == "discharge"
    assert "РЕКОМЕНДАЦИИ" in e["body"]

    # Второе сохранение = новая версия (новая запись)
    r2 = client.post("/api/epicrises/",
                     json={"patient_id": p["id"], "kind": "discharge",
                           "body": FAKE_DRAFT + "\nДополнено."},
                     headers=auth_headers(doctor))
    assert r2.status_code == 201
    assert r2.json()["id"] != e["id"]

    r = client.get(f"/api/epicrises/?patient_id={p['id']}", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert len(r.json()) == 2
    assert r.headers["X-Total-Count"] == "2"

    r = client.get(f"/api/epicrises/{e['id']}", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert r.json()["id"] == e["id"]


def test_save_empty_body_rejected(client, doctor):
    p = _mk_patient(client, doctor)
    r = client.post("/api/epicrises/",
                    json={"patient_id": p["id"], "kind": "discharge", "body": ""},
                    headers=auth_headers(doctor))
    assert r.status_code == 422


def test_doctor_scoping(client, doctor, second_doctor):
    p = _mk_patient(client, doctor)
    e = client.post("/api/epicrises/",
                    json={"patient_id": p["id"], "kind": "interim", "body": "Текст."},
                    headers=auth_headers(doctor)).json()

    # Чужой врач: черновик по чужому пациенту и чтение чужого эпикриза — 403/404
    r = client.post("/api/epicrises/draft",
                    json={"patient_id": p["id"], "kind": "discharge"},
                    headers=auth_headers(second_doctor))
    assert r.status_code in (403, 404)
    r = client.get(f"/api/epicrises/{e['id']}", headers=auth_headers(second_doctor))
    assert r.status_code in (403, 404)
    # И сохранение эпикриза на чужого пациента запрещено
    r = client.post("/api/epicrises/",
                    json={"patient_id": p["id"], "kind": "interim", "body": "Взлом."},
                    headers=auth_headers(second_doctor))
    assert r.status_code in (403, 404)
    # В списке чужого ничего нет
    r = client.get(f"/api/epicrises/?patient_id={p['id']}", headers=auth_headers(second_doctor))
    assert r.json() == []


def test_requires_auth(client):
    assert client.get("/api/epicrises/").status_code == 401
    assert client.post("/api/epicrises/draft", json={"patient_id": 1}).status_code == 401


# ---------- PDF ----------

def test_pdf_export(client, doctor):
    p = _mk_patient(client, doctor)
    e = client.post("/api/epicrises/",
                    json={"patient_id": p["id"], "kind": "discharge", "body": FAKE_DRAFT},
                    headers=auth_headers(doctor)).json()
    r = client.get(f"/api/epicrises/{e['id']}/pdf", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:5] == b"%PDF-"
    assert len(r.content) > 1500
