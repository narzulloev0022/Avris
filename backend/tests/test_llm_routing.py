"""Разведение вызовов по двум моделям: клинической и лёгкой.

Смысл теста не в том, что код компилируется, а в том, что карта вызовов
зафиксирована. Клиническое суждение стоит дороже ошибки в цене: если кто-то
однажды добавит `tier=LIGHT` к разбору анализов или к сводке для врача, это
должно упасть здесь, а не выясниться на приёме.
"""
import importlib

import pytest

import llm


@pytest.fixture
def reload_llm(monkeypatch):
    """Модуль читает env на импорте — для проверки конфигурации перечитываем."""
    def _reload(model: str = "clinical-model", light: str = ""):
        monkeypatch.setenv("ANTHROPIC_MODEL", model)
        monkeypatch.setenv("ANTHROPIC_MODEL_LIGHT", light)
        return importlib.reload(llm)
    yield _reload
    # Возвращаем модуль в состояние теста-окружения, иначе следующие тесты
    # увидят подменённые ключи.
    monkeypatch.undo()
    importlib.reload(llm)


# ---------- выбор модели ----------

def test_light_falls_back_to_clinical_when_not_configured(reload_llm):
    """Главная гарантия: выкатка кода сама по себе ничего не понижает.

    Лёгкая модель включается ТОЛЬКО отдельной переменной окружения. Пока её
    нет, все вызовы идут туда же, куда шли вчера.
    """
    m = reload_llm(model="clinical-model", light="")
    assert m._model_for(m.LIGHT) == "clinical-model"
    assert m._model_for(m.CLINICAL) == "clinical-model"


def test_light_is_used_once_configured(reload_llm):
    m = reload_llm(model="clinical-model", light="light-model")
    assert m._model_for(m.LIGHT) == "light-model"
    assert m._model_for(m.CLINICAL) == "clinical-model"


def test_unknown_tier_gets_the_clinical_model(reload_llm):
    """Опечатка в уровне не должна тихо удешевлять вызов."""
    m = reload_llm(model="clinical-model", light="light-model")
    assert m._model_for("chaep") == "clinical-model"
    assert m._model_for("") == "clinical-model"


def test_default_tier_is_clinical():
    """Забыть указать уровень безопасно: умолчание — дорогая модель."""
    import inspect
    assert inspect.signature(llm._llm_call).parameters["tier"].default == llm.CLINICAL


# ---------- карта вызовов ----------

# Что обязано остаться на клинической модели и почему. Врач или пациент
# прочитает это как медицинское содержание и будет по нему действовать.
CLINICAL_SITES = {
    "llm.py": "генерация SOAP и комментарий врачу к анализам",
    "epicrises.py": "черновик эпикриза",
    "patients.py": "сводка к приёму",
    "lab_orders.py": "клинический комментарий к результатам",
    "patient_labs.py": "разбор анализа пациенту",
    "patient_visits.py": "пересказ визита и назначений пациенту",
    "patient_insights.py": "инсайты по визитам",
}

# Что переведено на лёгкую: форматирование, извлечение и разговор под
# гардрейлами с пост-фильтром.
LIGHT_SITES = {
    "patient_assistant.py": "ассистент — весь объём пациентских тарифов",
    "patient_conversations.py": "сжатие собственного разговора пациента",
    "patient_monitoring.py": "фраза дайджеста, 200 токенов + пост-фильтр",
    "patient_intake.py": "вопросы интервью и раскладка ответов",
}


def _source(name: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / name).read_text()


@pytest.mark.parametrize("module,why", sorted(CLINICAL_SITES.items()))
def test_clinical_sites_never_ask_for_the_light_model(module, why):
    src = _source(module)
    # В llm.py один осознанный LIGHT — разбор речи в поля карточки; он не
    # клинический и живёт в том же файле, что и врачебные эндпоинты.
    allowed = 1 if module == "llm.py" else 0
    assert src.count("tier=LIGHT") == allowed, f"{module}: {why} должно идти на клинической"


@pytest.mark.parametrize("module,why", sorted(LIGHT_SITES.items()))
def test_light_sites_stay_on_the_light_model(module, why):
    assert "tier=LIGHT" in _source(module), f"{module}: {why} — ожидался лёгкий уровень"


def test_vision_has_no_tier_yet():
    """Зрение осталось на клинической намеренно.

    Оценка калорий по фото и так самое слабое утверждение приложения; ухудшать
    её ради экономии — продуктовое решение, а не правка конфигурации. Когда
    решение будет принято, этот тест придётся переписать осознанно.
    """
    import inspect
    assert "tier" not in inspect.signature(llm._llm_vision_call).parameters
