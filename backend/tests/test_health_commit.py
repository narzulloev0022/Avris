"""Снаружи должно быть видно, какой коммит работает.

Дважды за день выкат объявлялся состоявшимся, когда его не было:
приложение падало на старте, Railway оставлял работать прежний контейнер,
а версия файлов приходила из кэша Cloudflare. Проверить было нечем —
`version` в ответе статична и одинакова для любого коммита.
"""
import re


def test_health_reports_the_commit(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert "commit" in d
    # либо короткий хэш, либо честное «unknown» — но не пустота
    assert re.fullmatch(r"[0-9a-f]{7}|unknown", d["commit"]), d["commit"]


def test_health_reports_when_the_process_started(client):
    """Совпадение коммита ещё не значит, что контейнер перезапустился."""
    d = client.get("/api/health").json()
    assert "started_at" in d
    assert d["started_at"].endswith("Z")


def test_old_fields_are_intact(client):
    """По этому ответу ходит мониторинг и healthcheck Railway."""
    d = client.get("/api/health").json()
    assert d["status"] == "ok"
    assert d["service"] == "avris-backend"
    assert "pdf_font" in d


def test_commit_is_read_once(client):
    """Вызов git на каждый запрос к health — лишний процесс на ровном месте."""
    import main
    assert isinstance(main.BUILD_SHA, str)
    a = client.get("/api/health").json()["commit"]
    b = client.get("/api/health").json()["commit"]
    assert a == b == main.BUILD_SHA
