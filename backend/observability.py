"""Отчёты об ошибках сервера — Sentry, вычищенный от медданных.

Пятисотка в проде сейчас видна только в логах контейнера: никто не узнает, что
у пациента не открылся анализ, пока он не напишет в поддержку. Но тело запроса
к этому серверу — это симптомы, заметки врачу и результаты анализов, и в
трекер ошибок ему нельзя.

Поэтому наружу уходит только то, что нужно для починки: исключение, стек, метод
и путь без идентификаторов. Тело, строка запроса, заголовки, cookies и
пользователь вырезаются до отправки.

DSN приходит окружением (``SENTRY_DSN``); без него Sentry не инициализируется
вовсе — локальная разработка и тесты ничего никуда не шлют.

Правило для кода: не класть данные пациента в текст исключения. Сообщение
исключения уходит как есть — вычистить его автоматически нельзя, не потеряв
диагностику.
"""
import os
import re
from typing import Any, Optional

# /api/patient/labs/3/files/1 → /api/patient/labs/{id}/files/{id}
_ID_SEGMENT = re.compile(r"/\d+")


def scrub_path(path: str) -> str:
    """Убрать идентификаторы из пути: они указывают на конкретного человека."""
    return _ID_SEGMENT.sub("/{id}", path or "")


def scrub_event(event: dict, hint: Optional[dict] = None) -> Optional[dict]:
    """``before_send``: оставить диагностику, убрать всё про пациента."""
    request = event.get("request")
    if isinstance(request, dict):
        event["request"] = {
            "method": request.get("method"),
            "url": scrub_path(_path_of(request.get("url"))),
        }
    event.pop("user", None)
    event.pop("server_name", None)
    # Хлебные крошки сервера — это, как правило, SQL и HTTP-вызовы с
    # параметрами. Диагностической ценности мало, риска много.
    event.pop("breadcrumbs", None)
    extra = event.get("extra")
    if isinstance(extra, dict):
        event["extra"] = {}
    return event


def _path_of(url: Any) -> str:
    if not isinstance(url, str):
        return ""
    # Отрезаем схему, хост и строку запроса — остаётся только путь.
    without_scheme = url.split("://", 1)[-1]
    path = "/" + without_scheme.split("/", 1)[1] if "/" in without_scheme else "/"
    return path.split("?", 1)[0]


def init_sentry() -> bool:
    """Поднять Sentry, если задан DSN. Возвращает True, если поднялся."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:  # пакет не установлен — не повод падать на старте
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENV", "production"),
        send_default_pii=False,
        max_request_body_size="never",
        # Локальные переменные кадров стека — главная дыра на сервере с
        # медданными: в них лежат и строка пациента из базы, и его Bearer-токен.
        # Проверено на перехваченном конверте: с include_local_variables=True
        # токен уезжал целиком.
        include_local_variables=False,
        traces_sample_rate=0.0,
        before_send=scrub_event,
    )
    return True
