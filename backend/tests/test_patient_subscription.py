"""Тарифы и два потолка ассистента — месячный и дневной.

Месячный держит экономику (тариф обязан оставаться прибыльным, даже если
пациент выберет его до последнего вопроса), дневной держит всплеск. Проверяется
именно граница: ошибка на ней стоит денег и в проде не видна до счёта от
поставщика модели.

Лимиты проверяются на `_reserve`, а не через HTTP: сам эндпоинт ассистента
идёт в модель, а нам нужна арифметика потолков, а не ответ модели.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from database import SessionLocal
from models import AssistantUsage, PatientAccount
from patient_assistant import _reserve
from patient_auth import create_patient_access_token
from patient_subscription import (FREE, PLUS, PRO, TIERS, month_key,
                                  month_total)


def _account(db, phone, tier=FREE):
    account = PatientAccount(phone=phone, avris_patient_id=f"AV-SUB-{phone[-4:]}",
                             full_name="Тарифная Проба", subscription_tier=tier)
    if tier != FREE:
        account.subscription_expires_at = datetime.utcnow() + timedelta(days=30)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _token(account):
    return {"Authorization": f"Bearer {create_patient_access_token(account.id)}"}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _prev_month_key() -> str:
    first_of_this = datetime.now(timezone.utc).replace(day=1)
    return (first_of_this - timedelta(days=1)).strftime("%Y-%m")


def _fill_month(db, account_id, total, month=None):
    """Разложить `total` обращений по дням месяца, не трогая сегодня.

    Сегодняшний день пропускается намеренно: иначе месячный потолок нельзя
    отличить от дневного — упрёмся в тот, что сработает раньше, и тест будет
    зелёным по неверной причине.
    """
    prefix = month or month_key()
    today, day_num, left = _today(), 1, total
    while left > 0:
        day = f"{prefix}-{day_num:02d}"
        day_num += 1
        if day == today:
            continue
        chunk = min(left, 100)
        db.add(AssistantUsage(patient_account_id=account_id, day=day, count=chunk))
        left -= chunk
    db.commit()


# ---------- месячная сумма ----------

def test_month_total_sums_days_of_current_month(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992900010001")
        _fill_month(db, account.id, 17)
        assert month_total(db, AssistantUsage, account.id) == 17
    finally:
        db.close()


def test_month_total_ignores_previous_month(client):
    """Потолок обязан обнуляться первого числа, иначе он одноразовый."""
    db = SessionLocal()
    try:
        account = _account(db, "+992900010002")
        _fill_month(db, account.id, 500, month=_prev_month_key())
        assert month_total(db, AssistantUsage, account.id) == 0
    finally:
        db.close()


def test_month_total_is_per_account(client):
    db = SessionLocal()
    try:
        mine = _account(db, "+992900010003")
        other = _account(db, "+992900010004")
        _fill_month(db, other.id, 40)
        assert month_total(db, AssistantUsage, mine.id) == 0
    finally:
        db.close()


# ---------- потолки ассистента ----------

def test_monthly_cap_blocks_even_on_a_fresh_day(client):
    """Главная проверка: месяц выбран, сегодня ноль вопросов — всё равно отказ.

    До появления месячного потолка этот случай проходил: дневной счётчик
    обнулялся каждые сутки, и месяц был ничем не ограничен.
    """
    db = SessionLocal()
    try:
        account = _account(db, "+992900010005", PLUS)
        _fill_month(db, account.id, TIERS[PLUS].assistant_monthly)
        with pytest.raises(HTTPException) as err:
            _reserve(db, account)
        assert err.value.status_code == 429
        assert "месяц" in err.value.detail
    finally:
        db.close()


def test_daily_cap_still_blocks_inside_the_month(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992900010006", PLUS)
        db.add(AssistantUsage(patient_account_id=account.id, day=_today(),
                              count=TIERS[PLUS].assistant_daily))
        db.commit()
        with pytest.raises(HTTPException) as err:
            _reserve(db, account)
        assert err.value.status_code == 429
        assert "Дневной" in err.value.detail
    finally:
        db.close()


def test_free_is_pushed_to_plus_not_to_tomorrow_when_month_runs_out(client):
    """Бесплатному предлагаем тариф, платному — дату. Обратное бессмысленно."""
    db = SessionLocal()
    try:
        account = _account(db, "+992900010007", FREE)
        _fill_month(db, account.id, TIERS[FREE].assistant_monthly)
        with pytest.raises(HTTPException) as err:
            _reserve(db, account)
        assert "Plus" in err.value.detail
    finally:
        db.close()


def test_remaining_is_the_nearer_of_the_two_ceilings(client):
    """Наружу отдаём тот остаток, который кончится раньше."""
    db = SessionLocal()
    try:
        account = _account(db, "+992900010008", FREE)
        monthly = TIERS[FREE].assistant_monthly
        _fill_month(db, account.id, monthly - 2)
        # Дневного запаса ещё 3, месячного — 2; после списания остаётся 1.
        assert _reserve(db, account) == 1
    finally:
        db.close()


def test_expired_subscription_falls_back_to_free_ceilings(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992900010009", PRO)
        account.subscription_expires_at = datetime.utcnow() - timedelta(days=1)
        db.commit()
        _fill_month(db, account.id, TIERS[FREE].assistant_monthly)
        with pytest.raises(HTTPException) as err:
            _reserve(db, account)
        assert err.value.status_code == 429
    finally:
        db.close()


# ---------- то, что видит клиент ----------

def test_subscription_reports_both_ceilings(client):
    db = SessionLocal()
    try:
        account = _account(db, "+992900010010", PRO)
        _fill_month(db, account.id, 12)
        headers = _token(account)
    finally:
        db.close()
    body = client.get("/api/patient/subscription", headers=headers).json()
    assert body["assistant_monthly_cap"] == TIERS[PRO].assistant_monthly
    assert body["assistant_used_month"] == 12
    assert body["assistant_remaining_month"] == TIERS[PRO].assistant_monthly - 12


def test_plans_carry_prices_and_monthly_ceiling(client):
    db = SessionLocal()
    try:
        headers = _token(_account(db, "+992900010011"))
    finally:
        db.close()
    plans = {p["tier"]: p for p in
             client.get("/api/patient/subscription/plans", headers=headers).json()["plans"]}
    assert (plans[PLUS]["price_tjs"], plans[PLUS]["price_usd"]) == (95, 9.99)
    assert (plans[PRO]["price_tjs"], plans[PRO]["price_usd"]) == (215, 22.99)
    assert plans[PRO]["assistant_monthly_cap"] == TIERS[PRO].assistant_monthly


# ---------- экономика ----------

@pytest.mark.parametrize("tier,net_usd", [(PLUS, 8.49), (PRO, 19.54)])
def test_tier_stays_profitable_at_a_fully_spent_ceiling(tier, net_usd):
    """Смысл месячного потолка: он подобран так, что даже пациент, выбравший
    его до последнего вопроса, остаётся прибыльным.

    `net_usd` — цена за вычетом 15% комиссии магазина. Стоимость вопроса
    ($0.011 в среднем) и снимка ($0.008) — оценка по замеру промптов; если
    счёт от поставщика модели окажется иным, этот тест первым и упадёт.
    """
    spend = TIERS[tier].assistant_monthly * 0.011
    if tier == PRO:
        spend += 300 * 0.008  # месячный потолок разборов фото
    assert spend < net_usd, f"{tier}: потолок стоит ${spend:.2f} при выручке ${net_usd}"
