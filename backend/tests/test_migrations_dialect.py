"""Миграции пишутся сырым SQL — значит, обязаны говорить на языке обеих баз.

Разработка идёт на SQLite, прод живёт на PostgreSQL. Тип DATETIME есть
только в первой; вторая на нём падает при старте, и приложение не
поднимается вовсе. Ошибка не видна ни в одном тесте на SQLite — поймать её
можно только так: запретив писать типы, которых нет в PostgreSQL.
"""
import pathlib
import re

SQLITE_ONLY = ("DATETIME", "INT2", "INT8", "UNSIGNED BIG INT", "NVARCHAR", "CLOB")

DB = pathlib.Path(__file__).resolve().parent.parent / "database.py"


def test_no_sqlite_only_types_in_raw_sql():
    src = DB.read_text(encoding="utf-8")
    bad = []
    for m in re.finditer(r"ALTER TABLE[^\"']+", src):
        stmt = m.group(0).upper()
        for t in SQLITE_ONLY:
            if re.search(r"\b" + t + r"\b", stmt):
                bad.append((t, m.group(0).strip()[:90]))
    assert not bad, "типы, которых нет в PostgreSQL: " + "; ".join(
        f"{t} в «{s}»" for t, s in bad)


def test_timestamp_is_the_convention():
    """Все прежние колонки времени объявлены TIMESTAMP — новые тоже."""
    src = DB.read_text(encoding="utf-8")
    assert "confirmed_at TIMESTAMP" in src
    assert "updated_at TIMESTAMP" in src
