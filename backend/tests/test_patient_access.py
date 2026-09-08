"""Чужой patient_id не должен приниматься нигде.

Прямое чтение чужой карты сервер закрывал, а создание — нет: врач А
указывал patient_id пациента врача Б, и запись создавалась. Через публичную
ссылку направления наружу уходили имя, возраст и палата чужого пациента —
ссылка открывается без авторизации, её видит любой, кому попал QR.

OWASP называет это API1, Broken Object Level Authorization: проверка личности
есть, проверки прав на объект нет.
"""
import pytest

from conftest import auth_headers


@pytest.fixture()
def foreign_patient(client, second_doctor):
    """Пациент, заведённый вторым врачом через API — без обхода проверок."""
    r = client.post("/api/patients/", headers=auth_headers(second_doctor),
                    json={"full_name": "Чужой Пациент", "age": 44, "gender": "М",
                          "department": "therapy", "status": "stable", "ward": "7"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


class TestForeignPatientIsRefused:
    def test_direct_read_is_closed(self, client, doctor, foreign_patient):
        assert client.get(f"/api/patients/{foreign_patient}", headers=auth_headers(doctor)).status_code in (403, 404)

    def test_consultation_cannot_borrow_a_foreign_patient(self, client, doctor, foreign_patient):
        r = client.post("/api/consultations/", headers=auth_headers(doctor),
                        json={"patient_id": foreign_patient, "soap_s": "x", "language": "ru"})
        assert r.status_code in (403, 404), f"создалась чужая консультация: {r.text}"

    def test_lab_order_cannot_borrow_a_foreign_patient(self, client, doctor, foreign_patient):
        r = client.post("/api/lab-orders/", headers=auth_headers(doctor),
                        json={"patient_id": foreign_patient, "tests": ["ОАК"]})
        assert r.status_code in (403, 404), f"создалось чужое направление: {r.text}"

    def test_night_round_cannot_borrow_a_foreign_patient(self, client, doctor, foreign_patient):
        r = client.post("/api/night-rounds/", headers=auth_headers(doctor),
                        json={"patient_id": foreign_patient, "ward": "7", "notes": "x"})
        assert r.status_code in (403, 404), f"создался чужой обход: {r.text}"

    def test_call_doctor_cannot_page_about_a_foreign_patient(self, client, doctor, foreign_patient):
        r = client.post("/api/notifications/call-doctor", headers=auth_headers(doctor),
                        json={"patient_id": foreign_patient, "reason": "x"})
        assert r.status_code in (403, 404), f"вызов по чужому пациенту прошёл: {r.text}"


class TestNoLeakThroughTheLabPortal:
    def test_public_referral_never_exposes_a_foreign_patient(self, client, doctor, foreign_patient):
        """Главное последствие дыры: ссылка направления открывается без
        авторизации и показывала имя, возраст и палату чужого пациента."""
        r = client.post("/api/lab-orders/", headers=auth_headers(doctor),
                        json={"patient_id": foreign_patient, "tests": ["ОАК"]})
        if r.status_code in (403, 404):
            return  # создать не дали — утечки неоткуда взяться
        token = r.json()["qr_token"]
        pub = client.get(f"/api/lab-orders/by-token/{token}")
        assert pub.status_code == 200
        assert "Чужой Пациент" not in pub.text, "утечка ФИО через публичную ссылку"


class TestOwnPatientStillWorks:
    def test_doctor_can_still_use_their_own_patient(self, client, doctor):
        pid = client.post("/api/patients/", headers=auth_headers(doctor),
                          json={"full_name": "Свой Пациент", "age": 30, "gender": "Ж",
                                "department": "therapy", "status": "stable"}).json()["id"]
        assert client.post("/api/consultations/", headers=auth_headers(doctor),
                           json={"patient_id": pid, "soap_s": "x", "language": "ru"}).status_code == 201
        assert client.post("/api/lab-orders/", headers=auth_headers(doctor),
                           json={"patient_id": pid, "tests": ["ОАК"]}).status_code == 201
        assert client.post("/api/night-rounds/", headers=auth_headers(doctor),
                           json={"patient_id": pid, "ward": "3", "notes": "ок"}).status_code == 201

    def test_records_without_a_patient_are_still_allowed(self, client, doctor):
        """Осмотр без карты — обычный случай: врач диктует до заведения
        пациента. Ужесточение не должно его сломать."""
        assert client.post("/api/consultations/", headers=auth_headers(doctor),
                           json={"soap_s": "x", "language": "ru"}).status_code == 201
