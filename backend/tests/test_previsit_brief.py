"""Pre-visit сводка: короткая AI-выжимка перед приёмом (мок Claude, scoping)."""
import llm as llm_module
from conftest import auth_headers


def _mk_patient(client, doctor, **overrides):
    payload = {"full_name": "Сводкина С.С.", "patient_type": "inpatient",
               "admission_diagnosis": "Пневмония", "allergies": ["Пенициллин"]}
    payload.update(overrides)
    r = client.post("/api/patients/", json=payload, headers=auth_headers(doctor))
    assert r.status_code == 201, r.text
    return r.json()


def test_brief_happy_path(client, doctor, monkeypatch):
    captured = {}

    async def fake_claude(system_prompt, user_msg, max_tokens=1024):
        captured["system"] = system_prompt
        captured["user"] = user_msg
        return "— Динамика положительная\n— Проверить ОАК\n— Аллергия: пенициллин"

    # patients.py импортирует _claude_call изнутри функции → патчим модуль llm
    monkeypatch.setattr(llm_module, "_claude_call", fake_claude)
    p = _mk_patient(client, doctor)
    client.post("/api/consultations/",
                json={"patient_id": p["id"], "soap_s": "Кашель", "visit_type": "daily"},
                headers=auth_headers(doctor))
    r = client.post(f"/api/patients/{p['id']}/previsit-brief",
                    json={"language": "ru"}, headers=auth_headers(doctor))
    assert r.status_code == 200, r.text
    assert "Проверить ОАК" in r.json()["brief"]
    # Контекст дошёл: паспорт + аллергии + запись
    assert "Сводкина С.С." in captured["user"]
    assert "Пенициллин" in captured["user"]
    assert "Кашель" in captured["user"]
    # Промпт — телеграфная сводка, без выдумок
    assert "не выдумывай" in captured["system"].lower() or "ничего не выдумывай" in captured["system"]


def test_brief_scoping(client, doctor, second_doctor):
    p = _mk_patient(client, doctor)
    r = client.post(f"/api/patients/{p['id']}/previsit-brief",
                    json={}, headers=auth_headers(second_doctor))
    assert r.status_code in (403, 404)


def test_brief_503_without_key(client, doctor):
    p = _mk_patient(client, doctor)
    r = client.post(f"/api/patients/{p['id']}/previsit-brief",
                    json={}, headers=auth_headers(doctor))
    assert r.status_code == 503
