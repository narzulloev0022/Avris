"""Время наружу уходит с пометкой о часовом поясе.

В базе оно наивное и хранится в UTC — на этом держатся все сравнения в коде,
включая проверки истечения токенов. Но строка без пометки, по стандарту,
читается клиентом как местное время: для врача в Душанбе каждая дата в
приложении была на пять часов раньше, и то же видел пациент.

Починено на границе: значения в питоне остались наивными, меняется только
формат ответа.
"""
import importlib
import inspect
import pathlib
import re
from datetime import datetime

import pytest
from pydantic import BaseModel

from api_model import ApiModel
from conftest import auth_headers

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _models_with_time():
    """Все загруженные модели ответа, у которых есть поле времени.

    Проверяется настоящая иерархия классов, а не текст объявления: база может
    достаться и через родителя, и такой класс ошибкой не является.
    """
    for f in sorted(BACKEND.glob("*.py")):
        if f.name in ("api_model.py", "main.py"):
            continue
        try:
            importlib.import_module(f.stem)
        except Exception:
            pass                      # модуль, который не поднимается сам по себе
    out = []
    for cls in _all_subclasses(BaseModel):
        fields = getattr(cls, "model_fields", {})
        # Именно datetime.datetime: у календарной даты — дня рождения, дня
        # записи — часового пояса нет, и помечать её нечем и незачем.
        if any("datetime.datetime" in str(fi.annotation) for fi in fields.values()):
            out.append(cls)
    return out


def _all_subclasses(base):
    for c in base.__subclasses__():
        yield c
        yield from _all_subclasses(c)


def test_every_model_with_time_inherits_the_base():
    """Единственная защита от возврата ошибки: следующий, кто заведёт модель
    с датой, узнает об этом здесь, а не от врача через полгода."""
    found = _models_with_time()
    assert found, "модели со временем не нашлись — проверка перестала проверять"
    wrong = [f"{c.__module__}.{c.__name__}" for c in found
             if not issubclass(c, ApiModel)]
    assert not wrong, "модели со временем мимо ApiModel: " + ", ".join(sorted(wrong))


def test_the_base_marks_naive_time_as_utc():
    class M(ApiModel):
        at: datetime

    assert M(at=datetime(2026, 9, 9, 8, 34, 19)).model_dump_json() \
        == '{"at":"2026-09-09T08:34:19Z"}'


def test_python_dump_keeps_a_real_datetime():
    """model_dump() уходит в колонки базы: строка вместо datetime валит запись."""
    class M(ApiModel):
        at: datetime

    assert isinstance(M(at=datetime(2026, 9, 9, 8, 0)).model_dump()["at"], datetime)


def test_aware_time_is_not_marked_twice():
    from datetime import timezone

    class M(ApiModel):
        at: datetime

    v = M(at=datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)).model_dump_json()
    assert v.count("+00:00") + v.count("Z") == 1


class TestOverTheWire:
    def test_consultation_time_carries_the_zone(self, client, doctor):
        r = client.post("/api/patients/", headers=auth_headers(doctor),
                        json={"full_name": "Пациент Время", "age": 40})
        pid = r.json()["id"]
        c = client.post("/api/consultations/", headers=auth_headers(doctor),
                        json={"patient_id": pid, "soap_a": "Проверка"}).json()
        assert c["created_at"].endswith("Z")
        assert c["confirmed_at"].endswith("Z")

    def test_nested_models_too(self, client, doctor):
        """Лента активности вложена в сводку дашборда — без своей базы она
        отдавала бы время без пометки."""
        d = client.get("/api/stats/dashboard", headers=auth_headers(doctor)).json()
        for item in d.get("recent_activity") or []:
            assert item["timestamp"].endswith("Z"), item


def test_frontend_does_not_correct_twice(client):
    """Клиентская поправка ставит Z только при её отсутствии. Теперь сервер
    присылает её сам, и поправка обязана стать бездействующей."""
    js = client.get("/app.js").text
    i = js.index("function srvDate(")
    assert "test(s)?s:s+" in js[i:i + 300].replace(" ", "")
