"""Audit trail: mutations must leave PHI-free rows in audit_log."""
from conftest import auth_headers, register_and_verify

from database import SessionLocal
from models import AuditLog


def _rows(**filters):
    db = SessionLocal()
    try:
        q = db.query(AuditLog)
        for k, v in filters.items():
            q = q.filter(getattr(AuditLog, k) == v)
        return q.order_by(AuditLog.id.asc()).all()
    finally:
        db.close()


def test_login_is_audited(client):
    tok = register_and_verify(client, email="audit-login@test.tj", password="pass1234")
    before = len(_rows(action="login", user_id=tok["user"]["id"]))
    r = client.post("/api/auth/login", json={"email": "audit-login@test.tj", "password": "pass1234"})
    assert r.status_code == 200
    assert len(_rows(action="login", user_id=tok["user"]["id"])) == before + 1


def test_patient_lifecycle_is_audited(client, doctor):
    uid = doctor["user"]["id"]
    r = client.post("/api/patients/", json={"full_name": "Аудитова А.А."},
                    headers=auth_headers(doctor))
    pid = r.json()["id"]
    client.put(f"/api/patients/{pid}", json={"avris_score": 55, "ward": "B2"},
               headers=auth_headers(doctor))
    client.delete(f"/api/patients/{pid}", headers=auth_headers(doctor))

    rows = [x for x in _rows(entity="patient", user_id=uid) if x.entity_id == str(pid)]
    actions = [x.action for x in rows]
    assert actions == ["create", "update", "delete"]
    upd = rows[1]
    # meta carries field NAMES only — never values (PHI-free requirement)
    assert upd.meta == {"fields": ["avris_score", "ward"]}
    joined = str(upd.meta)
    assert "55" not in joined and "B2" not in joined


def test_lab_results_by_token_audited_without_user(client, doctor):
    r = client.post("/api/lab-orders/", json={"tests": ["cbc"]}, headers=auth_headers(doctor))
    o = r.json()
    r = client.put(f"/api/lab-orders/by-token/{o['qr_token']}/results",
                   json={"results": {"hemoglobin": 140}})
    assert r.status_code == 200
    rows = [x for x in _rows(entity="lab_order", action="results") if x.entity_id == str(o["id"])]
    assert len(rows) == 1
    assert rows[0].user_id is None            # lab tech has no account
    assert rows[0].meta == {"via": "qr_token"}
    assert "140" not in str(rows[0].meta)     # no result values in the trail


def test_consultation_create_audited(client, doctor):
    r = client.post("/api/consultations/",
                    json={"soap_s": "Жалобы на кашель", "language": "ru"},
                    headers=auth_headers(doctor))
    cid = r.json()["id"]
    rows = [x for x in _rows(entity="consultation", action="create") if x.entity_id == str(cid)]
    assert len(rows) == 1
    assert "кашель" not in str(rows[0].meta)  # transcript/SOAP never lands in audit


def test_admin_approve_audited(client, doctor):
    pending = register_and_verify(client, email="audit-pending@test.tj")
    r = client.post(f"/api/auth/admin/approve/{pending['user']['id']}",
                    headers=auth_headers(doctor))
    assert r.status_code == 200
    rows = [x for x in _rows(entity="user", action="approve")
            if x.entity_id == str(pending["user"]["id"])]
    assert len(rows) == 1
    assert rows[0].user_id == doctor["user"]["id"]  # who approved is recorded
