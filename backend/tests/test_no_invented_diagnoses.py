"""В интерфейсе не должно быть выдуманного медицинского содержания.

Кнопка «Дифф. диагноз» рядом с полем «Оценка» подбирала диагнозы сама: по
ключевым словам подставляла коды МКБ-10 с процентами вида 85/45/30, а если
слова не совпали — брала два диагноза из справочника через Math.random() и
рисовала им 55% и 35%. Подавалось под бейджем «AI · Avris», и врач мог одним
нажатием вписать это в карту.

Проверяем по отданному файлу: тест ловит возврат такой ветки в прод.
"""


class TestNoClientSideDiagnosis:
    def test_no_random_in_the_differential(self, client):
        js = client.get("/app.js").text
        i = js.find("function ddxFromModel(")
        assert i != -1, "функция дифдиагноза исчезла — проверка потеряла смысл"
        j = js.find("function ddxRender(", i)
        assert "Math.random" not in js[i:j], "случайный выбор диагноза вернулся"

    def test_generator_is_gone(self, client):
        js = client.get("/app.js").text
        assert "function ddxGenerate(" not in js
        assert "function ddxByName(" not in js, "остался подбор кода в обход модели"

    def test_differential_comes_from_the_model(self, client):
        """Единственный источник — ответ сервера, уже показанный в панели
        рекомендаций."""
        js = client.get("/app.js").text
        i = js.index("function ddxFromModel(")
        body = js[i:js.index("}", js.index("return rec.map", i))]
        assert "_aiRecsState" in body and "differential_diagnosis" in body

    def test_empty_state_says_so_instead_of_inventing(self, client):
        js = client.get("/app.js").text
        assert "ddx_none" in js
        assert 'ddx-empty' in js

    def test_percentages_are_labelled_as_model_estimate(self, client):
        """Процент от модели — не статистика; в backend это записано прямо
        в комментарии к полю probability."""
        js = client.get("/app.js").text
        assert "ddx_note" in js
        ru = js[js.index('\nru:{'):]
        note = ru[ru.index('ddx_note:"'):][:200]
        assert "не статист" in note.lower() or "не статистическая" in note
