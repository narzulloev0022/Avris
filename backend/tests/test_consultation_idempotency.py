"""Повтор сохранения осмотра не должен заводить второй.

Офлайн-очередь снимала запись до подтверждения сервера: закрытие вкладки в
этот момент теряло осмотр, а потерянный ответ приводил к повторной отправке
и второму осмотру в карте. Врач такой дубль не заметит — он видит список,
где две одинаковые записи выглядят как одна ошибка глаза.

Одного идентификатора на клиенте мало: сервер обязан вернуть уже созданную
запись, а не создать новую.
"""
from conftest import auth_headers


def _patient(client, doctor, name="Пациент Идемпотентности"):
    return client.post("/api/patients/", headers=auth_headers(doctor),
                       json={"full_name": name, "age": 33, "gender": "Ж"}).json()["id"]


class TestRepeatIsSafe:
    def test_same_key_returns_the_same_consultation(self, client, doctor):
        pid = _patient(client, doctor)
        body = {"patient_id": pid, "soap_s": "Кашель", "language": "ru",
                "client_id": "exam-key-1"}
        first = client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
        second = client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
        assert first.status_code == 201
        assert second.status_code == 200, "повтор должен возвращать существующую запись"
        assert first.json()["id"] == second.json()["id"]

    def test_repeat_does_not_add_a_second_record(self, client, doctor):
        pid = _patient(client, doctor, "Пациент Без Дублей")
        body = {"patient_id": pid, "soap_s": "x", "language": "ru", "client_id": "exam-key-2"}
        for _ in range(3):
            client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
        rows = client.get(f"/api/consultations/?patient_id={pid}",
                          headers=auth_headers(doctor)).json()
        assert len(rows) == 1, rows

    def test_replay_returns_the_original_content(self, client, doctor):
        """Повтор возвращает то, что сохранено, а не то, что прислали
        второй раз: иначе «повтор» тихо переписывал бы осмотр."""
        pid = _patient(client, doctor, "Пациент Содержимого")
        key = "exam-key-3"
        client.post("/api/consultations/", headers=auth_headers(doctor),
                    json={"patient_id": pid, "soap_s": "Первый текст",
                          "language": "ru", "client_id": key})
        again = client.post("/api/consultations/", headers=auth_headers(doctor),
                            json={"patient_id": pid, "soap_s": "Подменённый текст",
                                  "language": "ru", "client_id": key})
        assert again.json()["soap_s"] == "Первый текст"

    def test_accuracy_counters_are_not_bumped_twice(self, client, doctor):
        """Повтор не должен накручивать счётчики: иначе статистика врача
        зависит от качества связи."""
        me = lambda: client.get("/api/auth/me", headers=auth_headers(doctor)).json()
        before = me().get("soap_accurate_count") or 0
        pid = _patient(client, doctor, "Пациент Счётчиков")
        body = {"patient_id": pid, "soap_s": "x", "language": "ru",
                "client_id": "exam-key-4", "soap_was_edited": False}
        client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
        client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
        assert (me().get("soap_accurate_count") or 0) == before + 1


class TestKeysAreScoped:
    def test_another_doctor_key_does_not_collide(self, client, doctor, second_doctor):
        """Ключ клиента — не глобальный: чужой идентификатор не должен
        указывать на чужой осмотр."""
        p1 = _patient(client, doctor, "Пациент А")
        r1 = client.post("/api/consultations/", headers=auth_headers(doctor),
                         json={"patient_id": p1, "soap_s": "А", "language": "ru",
                               "client_id": "shared-key"})
        r2 = client.post("/api/consultations/", headers=auth_headers(second_doctor),
                         json={"soap_s": "Б", "language": "ru", "client_id": "shared-key"})
        assert r2.status_code == 201
        assert r1.json()["id"] != r2.json()["id"]

    def test_without_a_key_each_save_is_a_new_record(self, client, doctor):
        pid = _patient(client, doctor, "Пациент Без Ключа Осмотр")
        body = {"patient_id": pid, "soap_s": "x", "language": "ru"}
        client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
        client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
        rows = client.get(f"/api/consultations/?patient_id={pid}",
                          headers=auth_headers(doctor)).json()
        assert len(rows) == 2


class TestExamSessionContract:
    """Ключ бесполезен, если фронт его не выдаёт и не удерживает."""

    def test_key_is_born_with_the_recording(self, client):
        """Осмотр получает идентификатор в момент начала записи, а не при
        сохранении: иначе связывать аудио и текст уже не с чем."""
        js = client.get("/app.js").text
        assert "function examStart()" in js
        i = js.index("function startRec(")
        assert "examBegin()" in js[i:i + 1500]

    def test_the_same_key_goes_to_the_server(self, client):
        js = client.get("/app.js").text.replace(" ", "")
        assert "client_id:ex.id" in js

    def test_audio_carries_the_exam_it_belongs_to(self, client):
        """Распознавание, доехавшее после перезагрузки, должно знать,
        к какому осмотру относится наговорённое."""
        js = client.get("/app.js").text
        i = js.index("function savePendingAudio(")
        assert "examId" in js[i:i + 700]

    def test_patient_switch_asks_instead_of_guessing(self, client):
        """Список пациентов мог смениться, пока врач диктовал. Молчаливая
        запись в чужую карту — то же, чем была утечка: не та карта."""
        js = client.get("/app.js").text
        i = js.index("function saveConsult(")
        block = js[i:i + 1800]
        assert "exam_patient_changed" in block
        # спрашиваем своим диалогом, а не системным окном браузера
        assert "confirm2(" in block and "window.confirm" not in block
        # пациент берётся из сессии, а не из выпадающего списка
        assert "ex.patientId||selNow" in block.replace(" ", "")

    def test_queue_waits_for_confirmation(self, client):
        """Запись снималась с очереди до отправки — из страха дубля. Дубля
        больше нет, а закрытая вкладка теряла осмотр насовсем."""
        js = client.get("/app.js").text
        i = js.index("var flushing=false;")
        block = js[i:js.index("function netRetry(", i)]
        # снятие с очереди — только внутри обработки ответа
        before = block[:block.index("apiFetch")]
        before = "\n".join(l for l in before.split("\n")
                            if not l.startswith("function drop(it){"))
        assert "drop(" not in before, "снимает запись до ответа сервера"
        assert "if(r.ok){sent++;drop(it)" in block.replace(" ", "")

    def test_key_outlives_the_save(self, client):
        """Врач нажал «Подтвердить» дважды. Если ключ обнулять сразу после
        сохранения, второе нажатие выпишет новый — и в карте будет две
        одинаковые записи. Ключ уступает место только новому осмотру."""
        js = client.get("/app.js").text
        assert "function examBegin()" in js
        assert "examEnd" not in js, "сессия всё ещё закрывается по сохранению"
        # ротация — на начале работы, не на её конце
        i = js.index("function startRec(")
        assert "examBegin()" in js[i:i + 1500]
        assert "function generateSoap(){examBegin();" in js

    def test_concurrent_sends_do_not_break(self, client, doctor, monkeypatch):
        """Две отправки, ушедшие одновременно, обе не находят записи и обе
        пишут. Уникальный индекс останавливает вторую — врачу надо вернуть
        уже созданный осмотр, а не ошибку сервера."""
        from conftest import auth_headers
        import consultations as mod
        pid = _patient(client, doctor, "Пациент Гонка")
        body = {"patient_id": pid, "client_id": "race-1", "soap_a": "Бронхит"}
        first = client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
        assert first.status_code == 201

        # Окно гонки: проверка «уже есть?» проходит вслепую, как если бы
        # первая запись ещё не была видна второму запросу.
        real, blind = mod._find_twin, {"n": 0}

        def racy(db, doctor_id, client_id):
            blind["n"] += 1
            return None if blind["n"] == 1 else real(db, doctor_id, client_id)

        monkeypatch.setattr(mod, "_find_twin", racy)
        again = client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
        assert again.status_code == 200, "гонка вернула ошибку вместо записи"
        assert again.json()["id"] == first.json()["id"]
        assert blind["n"] == 2, "восстановление после конфликта не сработало"

        rows = client.get(f"/api/consultations/?patient_id={pid}",
                          headers=auth_headers(doctor)).json()
        assert len(rows) == 1

    def test_queue_actually_empties_after_sending(self, client):
        """Сравнение объектов не работает: очередь каждый раз разбирается из
        localStorage заново. Запись оставалась в ней навсегда, а полоса
        «не отправлено» висела над врачом, у которого всё отправилось."""
        js = client.get("/app.js").text
        i = js.index("var flushing=false;")
        block = js[i:js.index("function netRetry(", i)]
        assert "x.body!==it.body" not in block.replace(" ", ""), "сравнение по ссылке"
        assert "client_id" in block

    def test_a_stuck_flag_cannot_freeze_the_queue(self, client):
        """Синхронный сбой поднимал flushing навсегда: до конца сессии
        очередь молча переставала отправляться."""
        js = client.get("/app.js").text
        i = js.index("var flushing=false;")
        block = js[i:js.index("function netRetry(", i)]
        assert "catch(e){done()}" in block.replace(" ", "")

    def test_save_marks_the_session_instead_of_dropping_it(self, client):
        """Помеченный осмотр уступит место следующему при новой записи —
        но до тех пор повтор нажатия попадёт в ту же карточку."""
        js = client.get("/app.js").text
        i = js.index("function saveConsult(")
        ok = js.index('"t_save_ok"', i)          # тост об успехе — конец пути
        assert "examSaved()" in js[i:ok]
        flat = js.replace(" ", "")
        assert "functionexamSaved(){if(_exam)_exam.saved=true}" in flat
        assert "functionexamBegin(){return(!_exam||_exam.saved)?examStart():_exam}" in flat
