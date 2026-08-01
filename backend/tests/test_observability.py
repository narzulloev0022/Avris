"""Отчёт об ошибке сервера не должен выносить наружу медданные.

Тело запроса к этому серверу — это симптомы, заметки врачу и результаты
анализов. В трекер ошибок уходит только диагностика.
"""
from observability import scrub_event, scrub_path


def test_path_loses_identifiers():
    assert scrub_path("/api/patient/labs/3/files/1") == "/api/patient/labs/{id}/files/{id}"
    assert scrub_path("/api/patient/profile") == "/api/patient/profile"


def test_event_keeps_diagnostics_and_drops_everything_else():
    event = {
        "exception": {"values": [{"type": "ValueError", "value": "boom"}]},
        "request": {
            "method": "POST",
            "url": "https://theavris.ai/api/patient/pre-visit-note?token=secret",
            "query_string": "token=secret",
            "headers": {"Authorization": "Bearer eyJhbGciOi..."},
            "cookies": {"session": "abc"},
            "data": {"note_text": "Кашель третью неделю, ночью сильнее"},
        },
        "user": {"id": 42, "username": "+992900000009"},
        "server_name": "avris-prod-1",
        "breadcrumbs": [{"message": "SELECT * FROM patient_accounts WHERE phone = ..."}],
        "extra": {"account": "AV-BJAQ-ME8X"},
    }

    cleaned = scrub_event(dict(event))
    flat = repr(cleaned)

    # Осталось то, ради чего всё это.
    assert cleaned["exception"]["values"][0]["type"] == "ValueError"
    assert cleaned["request"] == {"method": "POST", "url": "/api/patient/pre-visit-note"}

    # И не осталось ничего про человека.
    for secret in ("Кашель", "992900000009", "AV-BJAQ-ME8X", "Bearer", "token=secret",
                   "patient_accounts", "avris-prod-1"):
        assert secret not in flat, f"в отчёт просочилось: {secret}"
    assert "user" not in cleaned
    assert "breadcrumbs" not in cleaned


def test_init_without_dsn_is_a_noop(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    from observability import init_sentry

    assert init_sentry() is False
