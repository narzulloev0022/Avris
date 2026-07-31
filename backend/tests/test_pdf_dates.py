"""Даты в документах — местное время клиники, а не UTC из базы.

В БД все отметки времени наивно-UTC. Печатать их как есть значило сдвинуть
каждый приём на пять часов назад, а у вечерних записей — ещё и на день:
осмотр вечера 18 июля становился утренним осмотром 18-го, а ночной осмотр
19-го — вчерашним. Документ, который пациент несёт другому врачу или в
страховую, обязан показывать время приёма.
"""
from datetime import datetime

import pdf_export


class TestDocumentDates:
    def test_evening_visit_keeps_its_hour(self):
        # 19:30 в Душанбе = 14:30 UTC в базе.
        assert pdf_export._format_dt(datetime(2026, 7, 18, 14, 30)) == "18.07.2026, 19:30"

    def test_night_visit_keeps_its_day(self):
        """Самый опасный случай: смещение переносит запись на другую дату."""
        # 02:00 19 июля по Душанбе = 21:00 18 июля UTC.
        assert pdf_export._format_dt(datetime(2026, 7, 18, 21, 0)) == "19.07.2026, 02:00"

    def test_timezone_aware_input_is_not_shifted_twice(self):
        from datetime import timezone
        aware = datetime(2026, 7, 18, 14, 30, tzinfo=timezone.utc)
        assert pdf_export._format_dt(aware) == "18.07.2026, 19:30"

    def test_missing_date_stays_a_dash(self):
        assert pdf_export._format_dt(None) == "—"
