"""Даты на документе — по местному времени клиники, а не по UTC.

В БД время наивное и хранится в UTC. Напечатанное как есть, между 19:00 и
полуночью по Душанбе оно даёт вчерашнее число: ночной обход идёт ровно в эти
часы. Документ с чужой датой в карте хуже, чем документ без даты.
"""
from datetime import datetime

import pdf_export


# 21:30 UTC 8 сентября = 02:30 9 сентября в Душанбе.
# Печать «как есть» дала бы 08.09 — на сутки раньше приёма.
NIGHT = datetime(2026, 9, 8, 21, 30, 0)


def test_date_only_uses_clinic_timezone():
    assert pdf_export._format_d(NIGHT) == "09.09.2026"


def test_datetime_uses_clinic_timezone():
    assert pdf_export._format_dt(NIGHT).startswith("09.09.2026")


def test_empty_date_is_a_dash_not_a_crash():
    assert pdf_export._format_d(None) == "—"


def test_no_raw_strftime_on_timestamps():
    """Дата рождения — не момент времени, к ней пояс неприменим; всё
    остальное обязано идти через перевод."""
    import inspect
    src = inspect.getsource(pdf_export)
    raw = [l.strip() for l in src.split("\n")
           if ".strftime(" in l and "_to_local" not in l
           and "date_of_birth" not in l and "def " not in l
           and "_format_dob" not in src[max(0, src.index(l) - 400):src.index(l)]]
    assert not raw, "печать времени мимо часового пояса: " + "; ".join(raw)
