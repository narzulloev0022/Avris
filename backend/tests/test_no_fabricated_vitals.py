"""Отсутствующее измерение не должно выглядеть нормой.

Пустой vitals превращался в пульс 80, давление 120/80, SpO2 97%, дыхание 16
и температуру 36,6. Врач в реанимации видел спокойные цифры там, где данных
нет вообще, и время «обновлено» показывало момент отрисовки экрана, а не
измерения.

Проверяем по отданному файлу: тест ловит возврат подстановок в прод.
"""
import re


def _icu_fn(js: str) -> str:
    i = js.index("function patientToIcu(")
    return js[i:js.index("\nfunction ", i + 10)]


class TestNoDefaults:
    def test_no_normal_values_as_fallbacks(self, client):
        body = _icu_fn(client.get("/app.js").text)
        for bad in ("hr,80", "sys,120", "spo2,97", "temp,36.6", "resp,16",
                    ",80)", ",120)", ",97)", ",36.6)", ",16)"):
            assert bad not in body.replace(" ", ""), f"подстановка нормы вернулась: {bad}"

    def test_every_measurement_is_guarded_for_absence(self, client):
        """Проверяем существо, а не имена внутренних переменных: каждое из
        шести измерений проходит через явную проверку на отсутствие."""
        body = _icu_fn(client.get("/app.js").text).replace(" ", "")
        assert body.count("==null?null:") >= 5, "измерения перестали проверяться на отсутствие"
        assert "temp==null?null:" in body

    def test_days_in_unit_are_counted_not_zeroed(self, client):
        """days:0 читается как «поступил сегодня»; без даты поступления
        это неизвестно."""
        body = _icu_fn(client.get("/app.js").text)
        assert "days:0" not in body.replace(" ", "")
        assert "adm_date" in body


class TestHonestScreen:
    def test_update_time_is_labelled_as_screen_not_data(self, client):
        """Времени измерения в модели нет: Patient.vitals — массивы чисел
        без меток. Значит «обновлено» может относиться только к экрану."""
        js = client.get("/app.js").text
        assert "icu_upd_screen" in js
        assert "экран обновлён" in js

    def test_average_spo2_skips_missing(self, client):
        """Отсутствие, посчитанное нулём, завышает тревогу; посчитанное
        нормой — занижает."""
        js = client.get("/app.js").text
        assert "p.vitals.spo2||0" not in js.replace(" ", "")
        assert "spVals" in js

    def test_missing_value_has_its_own_style(self, client):
        css = client.get("/styles.css").text
        assert ".icu-k.vnone" in css.replace(" ", "")
