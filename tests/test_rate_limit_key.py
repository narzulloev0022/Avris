"""Ключ rate-limit должен различать пациента, врача и аноним.

Пациентские токены несут ``aud="patient"``, и врачебный ``decode_token`` их
отвергает — до этого каждый запрос из приложения попадал в общий IP-бакет.
Мобильные операторы Таджикистана сидят на CGNAT: тысячи абонентов делят один
публичный адрес, и десяток пациентов выбирал бы лимит ассистента на всех
остальных.
"""
import os

os.environ.setdefault("PATIENT_DEV_OTP", "424242")

import auth as doctor_auth
import patient_auth
from rate_limit import _auth_aware_key


class _Client:
    host = "203.0.113.7"


class _Request:
    def __init__(self, token=None):
        self.headers = {"authorization": f"Bearer {token}"} if token else {}
        self.client = _Client()


def test_patient_gets_its_own_bucket():
    key = _auth_aware_key(_Request(patient_auth.create_patient_access_token(42)))
    assert key == "patient:42"


def test_doctor_bucket_unchanged():
    assert _auth_aware_key(_Request(doctor_auth.create_access_token(42))) == "user:42"


def test_same_id_in_both_doors_does_not_share_a_bucket():
    """id пациента и id врача совпадают запросто — префикс разводит их."""
    patient = _auth_aware_key(_Request(patient_auth.create_patient_access_token(7)))
    doctor = _auth_aware_key(_Request(doctor_auth.create_access_token(7)))
    assert patient != doctor


def test_anonymous_falls_back_to_ip():
    assert _auth_aware_key(_Request()) == "ip:203.0.113.7"


def test_garbage_token_falls_back_to_ip_instead_of_exploding():
    assert _auth_aware_key(_Request("not-a-jwt")) == "ip:203.0.113.7"
