"""Pytest fixtures for the Avris backend test suite.

Environment is pinned BEFORE importing the app: a throwaway SQLite file for
the DB and empty API keys so no real emails/LLM/STT calls can ever fire from
tests (load_dotenv never overrides pre-set env vars).
"""
import os
import sys
import tempfile
from pathlib import Path

_TMPDIR = tempfile.mkdtemp(prefix="avris-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDIR}/test_avris.db"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
os.environ["RESEND_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["RATELIMIT_STORAGE_URL"] = "memory://"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import auth as auth_module
from main import app
from rate_limit import limiter

# Per-endpoint limits (5/min register etc.) would 429 the suite — the limiter
# itself is not under test here.
limiter.enabled = False

# Codes "sent by email", captured per (purpose, email).
SENT_CODES: dict = {}


def _capture_verify(email, code, name=None):
    SENT_CODES[("verify", email)] = code


def _capture_reset(email, code, name=None):
    SENT_CODES[("reset", email)] = code


auth_module.send_verification_code = _capture_verify
auth_module.send_password_reset_code = _capture_reset


_fixtures_cache: dict = {}


@pytest.fixture(scope="session")
def client():
    # Context manager runs the lifespan → init_db() creates the schema.
    with TestClient(app) as c:
        # The chief must be the very first registered user in the DB — that's
        # what makes them admin + auto-approved. Register before any test runs.
        _fixtures_cache["doctor"] = register_and_verify(c, email="chief@test.tj")
        yield c


_doctor_seq = {"n": 0}


def register_and_verify(client, email=None, password="secret123"):
    """Full happy-path signup. Returns the Token payload (access/refresh/user)."""
    if email is None:
        _doctor_seq["n"] += 1
        email = f"doctor{_doctor_seq['n']}@test.tj"
    r = client.post("/api/auth/register", json={
        "email": email, "password": password, "full_name": "Dr Test",
    })
    assert r.status_code == 201, r.text
    code = SENT_CODES[("verify", email)]
    r = client.post("/api/auth/verify-email", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    data = r.json()
    data["email"] = email
    data["password"] = password
    return data


def auth_headers(token_payload):
    return {"Authorization": "Bearer " + token_payload["access_token"]}


@pytest.fixture(scope="session")
def doctor(client):
    """First registered doctor — becomes admin + auto-approved."""
    return _fixtures_cache["doctor"]


@pytest.fixture(scope="session")
def second_doctor(client, doctor):
    """Second doctor — requires admin approval (is_approved=False)."""
    return register_and_verify(client, email="resident@test.tj")
