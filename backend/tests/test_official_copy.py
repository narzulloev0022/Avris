"""Что попадает в официальную копию документа, а что — нет.

Два требования из аудита медицинских стандартов (7 сентября):
дословной речи в копии для карты быть не должно, а отметка о согласии
на запись приёма — должна.
"""
from conftest import auth_headers


def _patient(client, doctor, name="Пациент Копия"):
    r = client.post("/api/patients/", headers=auth_headers(doctor),
                    json={"full_name": name, "age": 44, "ward": "Терапия D1"})
    assert r.status_code == 201
    return r.json()["id"]


def _consult(client, doctor, pid, transcript="Здравствуйте, на что жалуетесь"):
    r = client.post("/api/consultations/", headers=auth_headers(doctor),
                    json={"patient_id": pid, "transcript": transcript,
                          "soap_s": "Кашель", "soap_a": "Бронхит"})
    assert r.status_code == 201
    return r.json()["id"]


def _pdf_text(client, doctor, cid, query=""):
    r = client.get(f"/api/consultations/{cid}/pdf{query}", headers=auth_headers(doctor))
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"
    from pdfminer.high_level import extract_text
    from io import BytesIO
    return extract_text(BytesIO(r.content))


class TestTranscript:
    def test_official_copy_has_no_verbatim_speech(self, client, doctor):
        """Стандартные формы дословной речи не содержат: подшитый в карту
        документ с расшифровкой разговора — уже не форма, а стенограмма."""
        pid = _patient(client, doctor)
        cid = _consult(client, doctor, pid, transcript="Дословная речь пациента")
        text = _pdf_text(client, doctor, cid)
        assert "Дословная речь пациента" not in text
        assert "ТРАНСКРИПТ" not in text
        # сам осмотр при этом на месте
        assert "Бронхит" in text

    def test_working_copy_carries_it_as_an_appendix(self, client, doctor):
        """Врачу расшифровка нужна — отдельным приложением по запросу."""
        pid = _patient(client, doctor)
        cid = _consult(client, doctor, pid, transcript="Дословная речь пациента")
        text = _pdf_text(client, doctor, cid, "?transcript=1")
        assert "Дословная речь пациента" in text
        assert "ПРИЛОЖЕНИЕ" in text

    def test_the_appendix_says_it_is_not_the_document(self, client, doctor):
        """Лист без оговорки подошьют в карту вместе с остальными."""
        pid = _patient(client, doctor)
        cid = _consult(client, doctor, pid)
        text = _pdf_text(client, doctor, cid, "?transcript=1")
        assert "не подшивается" in text or "не является частью" in text.lower()

    def test_appendix_comes_after_the_signature(self, client, doctor):
        """Иначе расшифровку прочтут как часть заверенного текста."""
        pid = _patient(client, doctor)
        cid = _consult(client, doctor, pid, transcript="Дословная речь пациента")
        text = _pdf_text(client, doctor, cid, "?transcript=1")
        assert text.index("Врач") < text.index("Дословная речь пациента")


class TestConsentMark:
    def _grant(self, client, doctor, pid):
        r = client.post("/api/consents/", headers=auth_headers(doctor),
                        json={"patient_id": pid, "kind": "recording",
                              "text": "Согласие на аудиозапись приёма"})
        assert r.status_code in (200, 201)

    def test_visit_shows_the_recording_consent(self, client, doctor):
        """Ст. 49 закрывает даже факт визита, а приём записывался голосом.
        Отметка о согласии — то, чем врач это обосновывает."""
        pid = _patient(client, doctor)
        self._grant(client, doctor, pid)
        cid = _consult(client, doctor, pid)
        text = _pdf_text(client, doctor, cid)
        assert "согласие пациента от" in text.lower()

    def test_absence_is_not_printed(self, client, doctor):
        """Документ уходит в карту пациента: пустая графа там читается
        как обвинение врачу, а не как напоминание."""
        pid = _patient(client, doctor)
        cid = _consult(client, doctor, pid)
        text = _pdf_text(client, doctor, cid)
        assert "согласие" not in text.lower()

    def test_later_consent_does_not_cover_an_earlier_visit(self, client, doctor):
        """Согласие, данное после приёма, не узаконивает запись задним числом."""
        pid = _patient(client, doctor)
        cid = _consult(client, doctor, pid)
        self._grant(client, doctor, pid)     # согласие пришло позже осмотра
        text = _pdf_text(client, doctor, cid)
        assert "согласие пациента от" not in text.lower()

    def test_training_consent_is_not_a_recording_consent(self, client, doctor):
        """Разрешение на обучение моделей не разрешает запись приёма."""
        pid = _patient(client, doctor)
        client.post("/api/consents/", headers=auth_headers(doctor),
                    json={"patient_id": pid, "kind": "training", "text": "Обучение"})
        cid = _consult(client, doctor, pid)
        text = _pdf_text(client, doctor, cid)
        assert "согласие пациента от" not in text.lower()


class TestVisitCardContract:
    """Отметка о согласии должна быть там, где врач работает, а не только
    внутри формы, которую он открывает раз в приём."""

    def test_mark_lives_in_the_visit_header(self, client):
        js = client.get("/app.js").text
        assert "function renderConsentMark(" in js
        assert "consent_mark_no" in js
        i = js.index("function updatePatCtx()")
        assert "renderConsentMark(p)" in js[i:i + 3000]

    def test_absence_is_shown_quietly(self, client):
        """Врач мог ещё не спросить — это напоминание, а не ошибка."""
        css = client.get("/styles.css").text
        i = css.index(".pat-ctx-consent.none{")
        block = css[i:i + 200]
        assert "--text-muted" in block
        assert "danger" not in block

    def test_saving_consent_refreshes_the_mark(self, client):
        """Иначе врач видит «согласия нет» сразу после того, как его
        зафиксировал, и перестаёт верить отметке вовсе."""
        js = client.get("/app.js").text
        i = js.index('t("consent_saved")')
        assert "delete _consentMark[sid]" in js[i:i + 400]

    def test_working_copy_button_appears_only_with_a_transcript(self, client):
        js = client.get("/app.js").text
        assert "hdPdfTrBtn" in js
        i = js.index('var pdfTr=$("hdPdfTrBtn")')
        assert "it.transcript" in js[i:i + 300]
        assert "transcript=1" in js[i:i + 400]


class TestAppendixSheet:
    """Приложение остаётся отдельным листом — таким его и надо проверять."""

    def test_it_points_back_to_the_record(self, client, doctor):
        pid = _patient(client, doctor)
        cid = _consult(client, doctor, pid)
        text = _pdf_text(client, doctor, cid, "?transcript=1")
        assert f"№ {cid}" in text

    def test_it_does_not_name_the_patient(self, client, doctor):
        """Ст. 49 закрывает даже факт визита: потерянный лист с дословной
        речью не должен сообщать, чей это разговор."""
        pid = _patient(client, doctor, "Уникальнов Тестбек")
        cid = _consult(client, doctor, pid)
        r = client.get(f"/api/consultations/{cid}/pdf?transcript=1",
                       headers=auth_headers(doctor))
        from pdfminer.high_level import extract_text
        from io import BytesIO
        pages = extract_text(BytesIO(r.content)).split("\f")
        appendix = pages[-2] if len(pages) > 1 else pages[-1]
        assert "ПРИЛОЖЕНИЕ" in appendix
        assert "Уникальнов" not in appendix


class TestPatientNameOnMobile:
    """ФИО пациента на телефоне должно читаться целиком."""

    def test_name_is_not_truncated_on_narrow_screens(self, client):
        css = client.get("/styles.css").text
        base = css.index(".pat-ctx-name{font-weight:600")
        tail = css[base:base + 900]
        assert "@media(max-width:768px)" in tail, "перенос объявлен до базового правила"
        assert "white-space:normal" in tail

    def test_the_override_comes_after_the_base_rule(self, client):
        """Медиазапрос не повышает специфичность: правило, стоящее раньше
        базового, проигрывает ему и молча ничего не делает."""
        css = client.get("/styles.css").text
        base = css.index(".pat-ctx-name{font-weight:600")
        override = css.index("@media(max-width:768px){\n/* Имя переносится")
        assert override > base

    def test_name_wraps_by_words_not_letters(self, client):
        """«Нос-иров-а» в столбик читается хуже обрезанной фамилии."""
        css = client.get("/styles.css").text
        i = css.index("@media(max-width:768px){\n/* Имя переносится")
        block = css[i:i + 600]
        assert "word-break:normal" in block
        assert "min-width:150px" in block, "пилюля статуса снова сожмёт имя"
