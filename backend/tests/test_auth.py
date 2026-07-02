"""Auth flow: register → OTP → login → refresh, plus the DB-backed OTP store
(auth_codes table) that replaced the in-memory dicts."""
from datetime import datetime, timedelta

from conftest import SENT_CODES, auth_headers, register_and_verify

from database import SessionLocal
from models import AuthCode


def _db():
    return SessionLocal()


def test_register_verify_login_flow(client):
    tok = register_and_verify(client, email="flow@test.tj", password="pass1234")
    assert tok["access_token"] and tok["refresh_token"]
    assert tok["user"]["email"] == "flow@test.tj"
    assert tok["user"]["is_verified"] is True

    r = client.post("/api/auth/login", json={"email": "flow@test.tj", "password": "pass1234"})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_first_user_is_admin_second_is_pending(doctor, second_doctor):
    assert doctor["user"]["is_admin"] is True
    assert doctor["user"]["is_approved"] is True
    assert second_doctor["user"]["is_admin"] is False
    assert second_doctor["user"]["is_approved"] is False


def test_otp_is_stored_in_db_not_memory(client):
    """Task: OTP must live in the auth_codes table (restart/multi-instance safe)."""
    r = client.post("/api/auth/register", json={"email": "dbotp@test.tj", "password": "pass1234"})
    assert r.status_code == 201
    db = _db()
    try:
        row = db.query(AuthCode).filter_by(purpose="verify", key="dbotp@test.tj").first()
        assert row is not None
        assert row.code_hash and len(row.code_hash) == 64  # sha256 hex, not plaintext
        assert row.expires_at > datetime.utcnow()
        assert row.resend_after is not None
    finally:
        db.close()


def test_wrong_otp_then_lockout(client):
    email = "lockout@test.tj"
    r = client.post("/api/auth/register", json={"email": email, "password": "pass1234"})
    assert r.status_code == 201
    # 4 wrong attempts → 400, 5th → 429 and the code is annulled
    for _ in range(4):
        r = client.post("/api/auth/verify-email", json={"email": email, "code": "000000"})
        assert r.status_code == 400
    r = client.post("/api/auth/verify-email", json={"email": email, "code": "000000"})
    assert r.status_code == 429
    # Even the correct code is now dead
    good = SENT_CODES[("verify", email)]
    r = client.post("/api/auth/verify-email", json={"email": email, "code": good})
    assert r.status_code == 400


def test_expired_otp_rejected(client):
    email = "expired@test.tj"
    r = client.post("/api/auth/register", json={"email": email, "password": "pass1234"})
    assert r.status_code == 201
    db = _db()
    try:
        row = db.query(AuthCode).filter_by(purpose="verify", key=email).first()
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    good = SENT_CODES[("verify", email)]
    r = client.post("/api/auth/verify-email", json={"email": email, "code": good})
    assert r.status_code == 400
    assert "истёк" in r.json()["detail"]


def test_login_unverified_403(client):
    email = "noverify@test.tj"
    client.post("/api/auth/register", json={"email": email, "password": "pass1234"})
    r = client.post("/api/auth/login", json={"email": email, "password": "pass1234"})
    assert r.status_code == 403


def test_login_wrong_password_401(doctor, client):
    r = client.post("/api/auth/login", json={"email": doctor["email"], "password": "wrong-pass"})
    assert r.status_code == 401


def test_refresh_rotates_tokens(client):
    tok = register_and_verify(client, email="refresh@test.tj")
    r = client.post("/api/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["access_token"]
    # An access token must not be usable as a refresh token
    r = client.post("/api/auth/refresh", json={"refresh_token": tok["access_token"]})
    assert r.status_code == 401


def test_resend_cooldown(client):
    email = "cooldown@test.tj"
    client.post("/api/auth/register", json={"email": email, "password": "pass1234"})
    r = client.post("/api/auth/resend-code", json={"email": email})
    assert r.status_code == 429
    assert "Подождите" in r.json()["detail"]


def test_forgot_reset_password_flow(client):
    tok = register_and_verify(client, email="resetme@test.tj", password="oldpass1")
    r = client.post("/api/auth/forgot-password", json={"email": "resetme@test.tj"})
    assert r.status_code == 200
    code = SENT_CODES[("reset", "resetme@test.tj")]
    r = client.post("/api/auth/reset-password", json={
        "email": "resetme@test.tj", "code": code, "new_password": "newpass2",
    })
    assert r.status_code == 200
    assert client.post("/api/auth/login", json={
        "email": "resetme@test.tj", "password": "oldpass1"}).status_code == 401
    assert client.post("/api/auth/login", json={
        "email": "resetme@test.tj", "password": "newpass2"}).status_code == 200
    # Reset code is single-use
    r = client.post("/api/auth/reset-password", json={
        "email": "resetme@test.tj", "code": code, "new_password": "thirdpass3",
    })
    assert r.status_code == 400


def test_me_requires_auth(client, doctor):
    assert client.get("/api/auth/me").status_code == 401
    r = client.get("/api/auth/me", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert r.json()["email"] == doctor["email"]


def test_oauth_state_helpers_roundtrip(client):
    """OAuth state is stored in auth_codes and is strictly single-use."""
    import auth as auth_module
    db = _db()
    try:
        state = auth_module._issue_oauth_state(db)
        row = db.query(AuthCode).filter_by(purpose="oauth", key=state).first()
        assert row is not None
        assert auth_module._consume_oauth_state(db, state) is True
        assert auth_module._consume_oauth_state(db, state) is False  # consumed
        assert auth_module._consume_oauth_state(db, "bogus-state") is False
    finally:
        db.close()


def test_admin_approve_flow(client, doctor, second_doctor):
    r = client.get("/api/auth/admin/pending-doctors", headers=auth_headers(doctor))
    assert r.status_code == 200
    pending_ids = [d["id"] for d in r.json()]
    assert second_doctor["user"]["id"] in pending_ids

    # Non-admin must not access admin endpoints
    r = client.get("/api/auth/admin/pending-doctors", headers=auth_headers(second_doctor))
    assert r.status_code == 403

    r = client.post(
        f"/api/auth/admin/approve/{second_doctor['user']['id']}",
        headers=auth_headers(doctor),
    )
    assert r.status_code == 200
    assert r.json()["is_approved"] is True
