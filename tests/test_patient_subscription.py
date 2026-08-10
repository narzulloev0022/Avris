"""Тарифные планы пациента: права, истечение, лимиты ассистента, гейт фич.

Тариф — серверная правда: клиентский paywall может врать (подменённая сборка,
DEV-флаг), сервер обязан отказать сам.
"""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("PATIENT_DEV_OTP", "424242")

import pytest
from fastapi.testclient import TestClient

import patient_labs as labs_module
import patient_subscription as ps_module
from models import PatientAccount

DEV_OTP = os.environ["PATIENT_DEV_OTP"]


@pytest.fixture()
def client(db_session):
    from rate_limit import limiter
    limiter.enabled = False
    import main
    with TestClient(main.app) as c:
        yield c


def _auth(client, phone):
    client.post("/api/patient/auth/request-otp", json={"contact": phone})
    r = client.post("/api/patient/auth/verify-otp", json={"contact": phone, "code": DEV_OTP})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _set_tier(db_session, phone, tier, *, days=30):
    """Выдать тариф напрямую в БД — как это сделает валидация чека IAP."""
    acc = db_session.query(PatientAccount).filter(PatientAccount.phone == phone).first()
    acc.subscription_tier = tier
    acc.subscription_expires_at = (
        None if tier == "free"
        else datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)
    )
    acc.subscription_source = None if tier == "free" else "manual"
    db_session.commit()
    return acc


class TestSubscriptionState:
    def test_requires_auth(self, client):
        assert client.get("/api/patient/subscription").status_code in (401, 403)

    def test_new_account_is_free(self, client):
        h = _auth(client, "+992906000001")
        body = client.get("/api/patient/subscription", headers=h).json()
        assert body["tier"] == "free"
        assert body["expires_at"] is None
        assert body["assistant_daily_cap"] == 3
        assert body["assistant_remaining_today"] == 3
        # Ядро бесплатно, платные фичи — нет.
        assert ps_module.FEATURE_ASSISTANT in body["features"]
        assert ps_module.FEATURE_LAB_BREAKDOWN not in body["features"]

    def test_plus_unlocks_features_and_bigger_cap(self, client, db_session):
        phone = "+992906000002"
        h = _auth(client, phone)
        _set_tier(db_session, phone, "plus")
        body = client.get("/api/patient/subscription", headers=h).json()
        assert body["tier"] == "plus"
        assert body["expires_at"] is not None
        assert body["assistant_daily_cap"] == ps_module.TIERS["plus"].assistant_daily
        assert ps_module.FEATURE_LAB_BREAKDOWN in body["features"]
        # Sonnet-тяжёлое остаётся в Pro — иначе течёт маржа Plus.
        assert ps_module.FEATURE_NUTRITION not in body["features"]

    def test_pro_has_everything_plus_has(self, client, db_session):
        phone = "+992906000003"
        h = _auth(client, phone)
        _set_tier(db_session, phone, "pro")
        body = client.get("/api/patient/subscription", headers=h).json()
        assert body["tier"] == "pro"
        assert set(ps_module.TIERS["plus"].features).issubset(set(body["features"]))
        assert ps_module.FEATURE_MONITORING in body["features"]

    def test_expired_subscription_falls_back_to_free(self, client, db_session):
        phone = "+992906000004"
        h = _auth(client, phone)
        acc = _set_tier(db_session, phone, "plus")
        acc.subscription_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        db_session.commit()
        body = client.get("/api/patient/subscription", headers=h).json()
        assert body["tier"] == "free"
        assert body["assistant_daily_cap"] == 3
        assert ps_module.FEATURE_LAB_BREAKDOWN not in body["features"]

    def test_unknown_tier_value_is_treated_as_free(self, client, db_session):
        phone = "+992906000005"
        h = _auth(client, phone)
        acc = _set_tier(db_session, phone, "plus")
        acc.subscription_tier = "platinum"  # мусор в колонке
        db_session.commit()
        assert client.get("/api/patient/subscription", headers=h).json()["tier"] == "free"

    def test_usage_counter_is_reported(self, client, db_session, monkeypatch):
        phone = "+992906000006"
        h = _auth(client, phone)

        async def _fake(system_prompt, user_msg, max_tokens=1024):
            return "Ответ помощника."

        import patient_assistant as pa_module
        monkeypatch.setattr(pa_module, "_llm_call", _fake)
        client.post("/api/patient/assistant",
                    json={"messages": [{"role": "user", "text": "Привет"}], "language": "ru"},
                    headers=h)
        body = client.get("/api/patient/subscription", headers=h).json()
        assert body["assistant_used_today"] == 1
        assert body["assistant_remaining_today"] == 2


class TestPlansCatalog:
    def test_catalog_carries_canonical_prices(self, client):
        h = _auth(client, "+992906000010")
        body = client.get("/api/patient/subscription/plans", headers=h).json()
        assert body["currency"] == "TJS"
        assert body["current_tier"] == "free"
        # Free карточкой не продаётся — это базовое состояние.
        assert [p["tier"] for p in body["plans"]] == ["plus", "pro"]
        plus, pro = body["plans"]
        assert (plus["price_tjs"], plus["price_usd"]) == (95, 9.99)
        assert (pro["price_tjs"], pro["price_usd"]) == (215, 22.99)


class TestFeatureGate:
    """Гейт на живой платной фиче — AI-разбор анализа (Plus)."""

    @pytest.fixture()
    def fake_llm(self, monkeypatch):
        async def _fake(system_prompt, user_msg, max_tokens=1024):
            return "Ваши показатели в пределах нормы. Обсудите результаты с врачом."

        monkeypatch.setattr(labs_module, "_llm_call", _fake)

    @pytest.fixture()
    def doctor(self, db_session):
        from auth import create_access_token, hash_password
        from models import User
        doc = User(email="doc-sub@test.tj", password_hash=hash_password("x"),
                   full_name="Др. Каримов", is_verified=True, is_approved=True)
        db_session.add(doc)
        db_session.commit()
        return {"Authorization": f"Bearer {create_access_token(doc.id)}"}, doc.id

    def _linked_lab(self, client, db_session, doctor, phone):
        """Пациент, связанный врачом по коду, и его заказ анализа —
        через реальные эндпоинты, а не вставкой связи мимо гейтов."""
        import uuid
        from models import LabOrder
        doc_headers, doc_id = doctor
        h = _auth(client, phone)
        client.post("/api/patient/consent", headers=h)
        code = client.post("/api/patient/link-code", headers=h).json()["code"]
        pid = client.post("/api/patient-links", headers=doc_headers,
                          json={"code": code}).json()["patient"]["id"]
        order = LabOrder(patient_id=pid, doctor_id=doc_id, qr_token=str(uuid.uuid4()),
                         tests=["Гемоглобин"], status="received",
                         results={"Гемоглобин": {"value": "140", "unit": "г/л"}})
        db_session.add(order)
        db_session.commit()
        return h, order.id

    def test_free_gets_402_with_upgrade_hint(self, client, db_session, doctor, fake_llm):
        h, oid = self._linked_lab(client, db_session, doctor, "+992906000020")
        r = client.post(f"/api/patient/labs/{oid}/breakdown", headers=h)
        # 402 «нужна оплата», а не 403: клиент по нему открывает «Тарифы».
        assert r.status_code == 402, r.text

    def test_plus_gets_the_breakdown(self, client, db_session, doctor, fake_llm):
        phone = "+992906000021"
        h, oid = self._linked_lab(client, db_session, doctor, phone)
        _set_tier(db_session, phone, "plus")
        r = client.post(f"/api/patient/labs/{oid}/breakdown", headers=h)
        assert r.status_code == 200, r.text
        assert "врач" in r.json()["breakdown"].lower()

    def test_expired_plus_loses_the_feature(self, client, db_session, doctor, fake_llm):
        phone = "+992906000022"
        h, oid = self._linked_lab(client, db_session, doctor, phone)
        acc = _set_tier(db_session, phone, "plus")
        acc.subscription_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db_session.commit()
        assert client.post(f"/api/patient/labs/{oid}/breakdown", headers=h).status_code == 402

    def test_breakdown_is_cached_and_not_paid_for_twice(self, client, db_session, doctor,
                                                        monkeypatch):
        """Повторное открытие карточки не должно стоить ещё одного вызова модели.

        Результаты анализа не меняются — а раньше каждый заход пациента в свой
        же анализ был новым оплаченным разбором.
        """
        calls = []

        async def _counting(system_prompt, user_msg, max_tokens=1024):
            calls.append(1)
            return "Показатели в норме. Обсудите с врачом."

        monkeypatch.setattr(labs_module, "_llm_call", _counting)

        phone = "+992906000030"
        h, oid = self._linked_lab(client, db_session, doctor, phone)
        _set_tier(db_session, phone, "plus")

        first = client.post(f"/api/patient/labs/{oid}/breakdown", headers=h)
        second = client.post(f"/api/patient/labs/{oid}/breakdown", headers=h)
        assert first.status_code == second.status_code == 200
        assert first.json()["breakdown"] == second.json()["breakdown"]
        assert len(calls) == 1, "второй разбор должен приходить из кэша"

    def test_new_results_invalidate_the_cached_breakdown(self, client, db_session, doctor,
                                                        monkeypatch):
        """Перезалили результаты — старое объяснение к ним уже не относится."""
        calls = []

        async def _counting(system_prompt, user_msg, max_tokens=1024):
            calls.append(1)
            return f"Разбор {len(calls)}"

        monkeypatch.setattr(labs_module, "_llm_call", _counting)

        phone = "+992906000031"
        h, oid = self._linked_lab(client, db_session, doctor, phone)
        _set_tier(db_session, phone, "plus")
        client.post(f"/api/patient/labs/{oid}/breakdown", headers=h)

        from models import LabOrder
        order = db_session.query(LabOrder).filter(LabOrder.id == oid).first()
        order.results = {"Гемоглобин": {"value": "90", "unit": "г/л"}}
        db_session.commit()

        again = client.post(f"/api/patient/labs/{oid}/breakdown", headers=h)
        assert again.status_code == 200
        assert len(calls) == 2, "изменившиеся результаты должны пересобрать разбор"

    def test_breakdown_of_foreign_lab_is_404(self, client, db_session, doctor, fake_llm):
        _, oid = self._linked_lab(client, db_session, doctor, "+992906000023")
        stranger = "+992906000024"
        h2 = _auth(client, stranger)
        _set_tier(db_session, stranger, "pro")
        # Оплаченный тариф не даёт доступа к чужим данным.
        assert client.post(f"/api/patient/labs/{oid}/breakdown", headers=h2).status_code == 404

    def test_foreign_lab_is_404_even_for_free(self, client, db_session, doctor, fake_llm):
        """Владение проверяется раньше оплаты: иначе по коду 402 vs 404 видно,
        что чужой анализ существует."""
        _, oid = self._linked_lab(client, db_session, doctor, "+992906000025")
        h2 = _auth(client, "+992906000026")  # free и не связан с этим заказом
        assert client.post(f"/api/patient/labs/{oid}/breakdown", headers=h2).status_code == 404

    def test_pro_carries_family_profiles_right(self, client, db_session):
        """Семья — аргумент Pro: право заведено, гейт готов к мультипрофилю."""
        phone = "+992906000027"
        h = _auth(client, phone)
        _set_tier(db_session, phone, "pro")
        body = client.get("/api/patient/subscription", headers=h).json()
        assert ps_module.FEATURE_FAMILY_PROFILES in body["features"]
        plans = client.get("/api/patient/subscription/plans", headers=h).json()["plans"]
        plus = next(p for p in plans if p["tier"] == "plus")
        assert ps_module.FEATURE_FAMILY_PROFILES not in plus["features"]


class TestDevEndpoint:
    def test_hidden_without_env_flag(self, client, monkeypatch):
        monkeypatch.delenv("PATIENT_DEV_SUBSCRIPTION", raising=False)
        h = _auth(client, "+992906000030")
        r = client.post("/api/patient/subscription/dev", json={"tier": "pro"}, headers=h)
        # 404, а не 403 — не подсказываем существование лазейки.
        assert r.status_code == 404

    def test_grants_tier_when_enabled(self, client, monkeypatch):
        monkeypatch.setenv("PATIENT_DEV_SUBSCRIPTION", "1")
        h = _auth(client, "+992906000031")
        r = client.post("/api/patient/subscription/dev", json={"tier": "pro", "days": 7}, headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tier"] == "pro"
        assert body["expires_at"] is not None
        assert client.get("/api/patient/subscription", headers=h).json()["tier"] == "pro"

    def test_rejects_unknown_tier(self, client, monkeypatch):
        monkeypatch.setenv("PATIENT_DEV_SUBSCRIPTION", "1")
        h = _auth(client, "+992906000032")
        r = client.post("/api/patient/subscription/dev", json={"tier": "platinum"}, headers=h)
        assert r.status_code == 422


class TestUsageAccounting:
    """Лимит списывается за ответ, а не за попытку."""

    def test_failed_model_call_does_not_consume_a_question(self, client, monkeypatch):
        import patient_assistant as pa_module

        from fastapi import HTTPException

        async def _boom(system_prompt, user_msg, max_tokens=1024):
            # Ровно так падает настоящий _llm_call: сеть/ключ/квота → 503.
            raise HTTPException(status_code=503, detail="Сервис AI временно недоступен")

        monkeypatch.setattr(pa_module, "_llm_call", _boom)
        h = _auth(client, "+992906000040")
        r = client.post("/api/patient/assistant",
                        json={"messages": [{"role": "user", "text": "Привет"}], "language": "ru"},
                        headers=h)
        assert r.status_code == 503
        # Из трёх бесплатных вопросов не потрачено ни одного.
        body = client.get("/api/patient/subscription", headers=h).json()
        assert body["assistant_used_today"] == 0
        assert body["assistant_remaining_today"] == 3

    def test_successful_call_consumes_one(self, client, monkeypatch):
        import patient_assistant as pa_module

        async def _ok(system_prompt, user_msg, max_tokens=1024):
            return "Расскажите подробнее, когда это началось?"

        monkeypatch.setattr(pa_module, "_llm_call", _ok)
        h = _auth(client, "+992906000041")
        r = client.post("/api/patient/assistant",
                        json={"messages": [{"role": "user", "text": "Привет"}], "language": "ru"},
                        headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["remaining"] == 2
        assert client.get("/api/patient/subscription", headers=h).json()["assistant_used_today"] == 1


class TestCapRace:
    """Слот занимается ДО вызова модели — параллельные запросы не пробивают кап."""

    def test_slot_is_taken_before_the_model_answers(self, client, db_session, monkeypatch):
        import patient_assistant as pa_module
        from models import AssistantUsage

        seen = {}

        async def _slow(system_prompt, user_msg, max_tokens=1024):
            # Пока модель «думает», счётчик уже должен быть увеличен: именно
            # это не даёт параллельному запросу пройти проверку лимита.
            db_session.expire_all()
            row = db_session.query(AssistantUsage).first()
            seen["count_mid_flight"] = row.count if row else 0
            return "Ответ помощника."

        monkeypatch.setattr(pa_module, "_llm_call", _slow)
        h = _auth(client, "+992906000050")
        r = client.post("/api/patient/assistant",
                        json={"messages": [{"role": "user", "text": "Привет"}], "language": "ru"},
                        headers=h)
        assert r.status_code == 200, r.text
        assert seen["count_mid_flight"] == 1
        assert r.json()["remaining"] == 2
