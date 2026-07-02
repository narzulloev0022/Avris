"""Public reference endpoints (ICD-10, drugs), night rounds, dashboard stats."""
from conftest import auth_headers


def test_icd10_search(client):
    r = client.get("/api/icd10/search", params={"q": "I10"})
    assert r.status_code == 200
    hits = r.json()
    assert hits and hits[0]["code"].startswith("I10")
    assert hits[0]["name_ru"]


def test_icd10_get_by_code(client):
    r = client.get("/api/icd10/I10")
    assert r.status_code == 200
    assert r.json()["code"] == "I10"
    assert client.get("/api/icd10/ZZZ99").status_code == 404


def test_drugs_search(client):
    r = client.get("/api/drugs/search", params={"q": "амлодипин"})
    assert r.status_code == 200
    assert r.json(), "expected at least one drug hit for 'амлодипин'"


def test_night_round_crud(client, doctor):
    r = client.post(
        "/api/night-rounds/",
        json={
            "ward": "A1",
            "vitals": {"hr": 78, "bp": "120/80", "spo2": 97},
            "notes": "Состояние стабильное",
            "status": "stable",
        },
        headers=auth_headers(doctor),
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]

    r = client.get("/api/night-rounds/", headers=auth_headers(doctor))
    assert any(x["id"] == rid for x in r.json())

    r = client.get(f"/api/night-rounds/{rid}", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert r.json()["vitals"]["hr"] == 78


def test_night_round_scoping(client, doctor, second_doctor):
    r = client.post("/api/night-rounds/", json={"ward": "B2"}, headers=auth_headers(doctor))
    rid = r.json()["id"]
    r = client.get(f"/api/night-rounds/{rid}", headers=auth_headers(second_doctor))
    assert r.status_code == 403


def test_dashboard_stats(client, doctor):
    r = client.get("/api/stats/dashboard", headers=auth_headers(doctor))
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("active_patients", "critical_patients", "consultations_today",
                "soap_total", "time_saved_minutes", "recent_activity"):
        assert key in body
    assert body["active_patients"] >= 1  # patients created by this suite
    assert isinstance(body["recent_activity"], list)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_stt_and_llm_503_without_keys(client, doctor):
    """With empty API keys the proxies must fail closed with 503, not crash."""
    r = client.post(
        "/api/llm/generate-soap",
        json={"transcript": "тест", "language": "ru"},
        headers=auth_headers(doctor),
    )
    assert r.status_code == 503


def test_llm_icd10_validation(client):
    """Invented ICD-10 codes from the LLM must be dropped or mapped to a known parent."""
    from llm import _validate_icd10, _VALID_ICD10
    assert len(_VALID_ICD10) > 100
    assert _validate_icd10("I10") == "I10"          # exact known code
    assert _validate_icd10(" i10 ") == "I10"        # normalization
    known_parent = "I11" if "I11" in _VALID_ICD10 else next(iter(_VALID_ICD10))
    assert _validate_icd10(known_parent + ".9") == known_parent  # subcode -> parent
    assert _validate_icd10("QZ99.99") is None       # invented -> dropped
    assert _validate_icd10(None) is None
    assert _validate_icd10("") is None


def test_email_retry_on_failure(monkeypatch):
    """_send_via_resend must retry RESEND_MAX_ATTEMPTS times with backoff."""
    import email_service

    calls = {"n": 0}

    class _FakeEmails:
        @staticmethod
        def send(payload):
            calls["n"] += 1
            raise RuntimeError("boom")

    import types
    fake_resend = types.SimpleNamespace(api_key=None, Emails=_FakeEmails)
    import sys
    monkeypatch.setitem(sys.modules, "resend", fake_resend)
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(email_service, "RESEND_BACKOFF_SECONDS", (0, 0))

    ok = email_service._send_via_resend("x@test.tj", "subj", "<p>hi</p>", "Test", "123456")
    assert ok is False
    assert calls["n"] == email_service.RESEND_MAX_ATTEMPTS

    # Success on 2nd attempt → True, no further calls
    calls["n"] = 0

    class _FlakyEmails:
        @staticmethod
        def send(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")

    fake_resend.Emails = _FlakyEmails
    ok = email_service._send_via_resend("x@test.tj", "subj", "<p>hi</p>", "Test", "123456")
    assert ok is True
    assert calls["n"] == 2


def test_email_html_escaping():
    """User-supplied values must be HTML-escaped in email bodies."""
    import email_service
    html_out = email_service._render_plain("Test", "<p>ok</p>", full_name="<script>alert(1)</script>")
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_pagination_and_total_count(client, doctor):
    """limit/offset + X-Total-Count on list endpoints; no params → old behavior."""
    from conftest import auth_headers as _ah
    for i in range(3):
        r = client.post("/api/patients/", json={"full_name": f"Пагинация {i}"},
                        headers=_ah(doctor))
        assert r.status_code == 201

    r_all = client.get("/api/patients/", headers=_ah(doctor))
    assert r_all.status_code == 200
    total = int(r_all.headers["x-total-count"])
    assert total == len(r_all.json())  # no limit → full list, header matches

    r_page = client.get("/api/patients/", params={"limit": 2, "offset": 1},
                        headers=_ah(doctor))
    assert len(r_page.json()) == 2
    assert int(r_page.headers["x-total-count"]) == total
    assert r_page.json()[0]["id"] == r_all.json()[1]["id"]  # offset respected

    # Same contract on consultations
    r = client.get("/api/consultations/", params={"limit": 1}, headers=_ah(doctor))
    assert r.status_code == 200
    assert len(r.json()) <= 1
    assert "x-total-count" in r.headers


def test_admin_page_served(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Avris" in r.text and "pending-doctors" in r.text
