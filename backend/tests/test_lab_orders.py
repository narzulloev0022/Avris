"""Lab Connect: order → public QR-token portal → results/files upload."""
from conftest import auth_headers

# 1x1 transparent PNG
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8ffff3f0300050001aa2bd0790000000049454e44ae426082"
)


def _mk_order(client, doctor, tests=None):
    r = client.post(
        "/api/lab-orders/",
        json={"tests": tests or ["cbc", "esr"]},
        headers=auth_headers(doctor),
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_create_order_has_qr_token(client, doctor):
    o = _mk_order(client, doctor)
    assert o["qr_token"]
    assert o["status"] == "pending"
    assert o["tests"] == ["cbc", "esr"]


def test_public_portal_by_token(client, doctor):
    o = _mk_order(client, doctor)
    # No auth header — lab tech only knows the token
    r = client.get(f"/api/lab-orders/by-token/{o['qr_token']}")
    assert r.status_code == 200
    assert r.json()["qr_token"] == o["qr_token"]
    assert client.get("/api/lab-orders/by-token/no-such-token").status_code == 404


def test_upload_results_by_token(client, doctor):
    o = _mk_order(client, doctor)
    r = client.put(
        f"/api/lab-orders/by-token/{o['qr_token']}/results",
        json={"results": {"hemoglobin": 132, "esr": 18}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "received"
    assert body["results"]["hemoglobin"] == 132
    # Re-submit must be refused (stale overwrite protection)
    r = client.put(
        f"/api/lab-orders/by-token/{o['qr_token']}/results",
        json={"results": {"hemoglobin": 1}},
    )
    assert r.status_code == 409


def test_file_upload_flow(client, doctor):
    o = _mk_order(client, doctor)
    r = client.post(
        f"/api/lab-orders/by-token/{o['qr_token']}/files",
        data={"result_type": "lab"},
        files={"file": ("result.png", PNG_BYTES, "image/png")},
    )
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["filename"] == "result.png"
    assert meta["size_bytes"] == len(PNG_BYTES)

    # Doctor sees the file list and can download
    r = client.get(f"/api/lab-orders/{o['id']}/files", headers=auth_headers(doctor))
    assert r.status_code == 200 and len(r.json()) == 1
    fid = r.json()[0]["id"]
    r = client.get(f"/api/lab-orders/{o['id']}/files/{fid}", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert r.content == PNG_BYTES


def test_file_upload_rejects_bad_ext(client, doctor):
    o = _mk_order(client, doctor)
    r = client.post(
        f"/api/lab-orders/by-token/{o['qr_token']}/files",
        data={"result_type": "lab"},
        files={"file": ("evil.exe", b"MZ....", "application/octet-stream")},
    )
    assert r.status_code == 415


def test_order_scoping(client, doctor, second_doctor):
    o = _mk_order(client, doctor)
    r = client.get(f"/api/lab-orders/{o['id']}", headers=auth_headers(second_doctor))
    assert r.status_code in (403, 404)
