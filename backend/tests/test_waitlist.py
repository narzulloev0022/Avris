"""Waitlist endpoint: signup, dedupe without enumeration, honeypot, export auth,
owner email notification."""
import os

import email_service


def test_signup_creates_entry(client):
    r = client.post("/api/waitlist", json={"email": "wl-one@clinic.tj", "role": "clinic", "lang": "ru", "website": ""})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_duplicate_is_indistinguishable(client):
    payload = {"email": "wl-dup@clinic.tj", "role": "doctor", "lang": "ru", "website": ""}
    first = client.post("/api/waitlist", json=payload).json()
    second = client.post("/api/waitlist", json=payload).json()
    assert first == second == {"ok": True}  # no "already" oracle


def test_honeypot_drops_silently(client):
    r = client.post("/api/waitlist", json={"email": "bot@spam.io", "role": "doctor", "lang": "ru", "website": "http://spam"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    os.environ["ADMIN_RESET_KEY"] = "test-export-key"
    rows = client.get("/api/waitlist/export", headers={"X-Admin-Reset-Key": "test-export-key"}).json()
    assert all(e["email"] != "bot@spam.io" for e in rows["entries"])


def test_garbage_email_rejected(client):
    assert client.post("/api/waitlist", json={"email": "not-an-email", "role": "doctor", "lang": "ru", "website": ""}).status_code == 422


def test_unknown_role_and_lang_fall_back(client):
    os.environ["ADMIN_RESET_KEY"] = "test-export-key"
    client.post("/api/waitlist", json={"email": "wl-role@clinic.tj", "role": "hacker", "lang": "xx", "website": ""})
    rows = client.get("/api/waitlist/export", headers={"X-Admin-Reset-Key": "test-export-key"}).json()
    row = next(e for e in rows["entries"] if e["email"] == "wl-role@clinic.tj")
    assert row["role"] == "doctor" and row["lang"] == "ru"


def test_export_requires_key(client):
    os.environ["ADMIN_RESET_KEY"] = "test-export-key"
    assert client.get("/api/waitlist/export").status_code == 403
    assert client.get("/api/waitlist/export", headers={"X-Admin-Reset-Key": "wrong"}).status_code == 403
    os.environ.pop("ADMIN_RESET_KEY")
    assert client.get("/api/waitlist/export").status_code == 503


def test_owner_notification_sent(client, monkeypatch):
    sent = {}
    monkeypatch.setenv("WAITLIST_NOTIFY_EMAIL", "owner@example.com")
    monkeypatch.setattr(email_service, "send_waitlist_alert",
                        lambda to, email, role, lang: sent.update(to=to, email=email) or True)
    client.post("/api/waitlist", json={"email": "wl-notify@clinic.tj", "role": "doctor", "lang": "en", "website": ""})
    assert sent == {"to": "owner@example.com", "email": "wl-notify@clinic.tj"}
