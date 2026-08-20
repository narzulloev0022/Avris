"""Глубокая проверка живости — то, по чему наблюдатель снаружи судит о проде.

Её легко сделать бесполезной двумя способами: отдать наружу карту
конфигурации или ответить 200 с «ok: false», который наблюдатель не заметит.
"""
import os

import pytest


class TestAccess:
    def test_without_token_is_forbidden(self, client, monkeypatch):
        monkeypatch.setenv("HEALTH_TOKEN", "s3cret")
        import health
        monkeypatch.setattr(health, "HEALTH_TOKEN", "s3cret")
        assert client.get("/api/health/deep").status_code == 403

    def test_wrong_token_is_forbidden(self, client, monkeypatch):
        import health
        monkeypatch.setattr(health, "HEALTH_TOKEN", "s3cret")
        r = client.get("/api/health/deep", headers={"X-Health-Token": "nope"})
        assert r.status_code == 403

    def test_unconfigured_token_refuses_rather_than_opens(self, client, monkeypatch):
        """Пустой секрет не должен означать «пускать всех»: это ровно тот
        случай, когда карта конфигурации утекает по недосмотру."""
        import health
        monkeypatch.setattr(health, "HEALTH_TOKEN", "")
        assert client.get("/api/health/deep").status_code == 503

    def test_public_health_stays_public_and_shallow(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        # Публичная проверка не должна рассказывать про ключи и базу.
        assert "checks" not in body and "failed" not in body


class TestVerdict:
    @pytest.fixture()
    def token(self, monkeypatch):
        import health
        monkeypatch.setattr(health, "HEALTH_TOKEN", "s3cret")
        return {"X-Health-Token": "s3cret"}

    def test_missing_key_answers_503_not_200(self, client, token, monkeypatch):
        """Наблюдатель смотрит на код ответа. «ok: false» с кодом 200 он
        пропустит, и падение ключей мы узнаем от врача."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        r = client.get("/api/health/deep", headers=token)
        assert r.status_code == 503
        assert "stt_key" in r.json()["detail"]["failed"]

    def test_everything_configured_answers_200(self, client, token, monkeypatch):
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
                  "RESEND_API_KEY"):
            monkeypatch.setenv(k, "x")
        monkeypatch.setenv("SECRET_KEY", "not-the-default")
        r = client.get("/api/health/deep", headers=token)
        assert r.status_code == 200, r.json()
        assert r.json()["ok"] is True
        assert r.json()["checks"]["db"] is True

    def test_default_jwt_secret_counts_as_failure(self, client, token, monkeypatch):
        """Дефолтный ключ подписи опубликован в исходниках: с ним любой
        подделает админский токен."""
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
                  "RESEND_API_KEY"):
            monkeypatch.setenv(k, "x")
        monkeypatch.setenv("SECRET_KEY", "dev-secret-change-me")
        r = client.get("/api/health/deep", headers=token)
        assert r.status_code == 503
        assert "jwt_secret" in r.json()["detail"]["failed"]

    def test_live_probe_is_opt_in(self, client, token, monkeypatch):
        """Настоящие вызовы внешних API стоят денег на каждой пробе —
        по умолчанию их быть не должно."""
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
                  "RESEND_API_KEY"):
            monkeypatch.setenv(k, "x")
        monkeypatch.setenv("SECRET_KEY", "not-the-default")
        checks = client.get("/api/health/deep", headers=token).json()["checks"]
        assert "stt_live" not in checks and "llm_live" not in checks
