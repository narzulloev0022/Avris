"""Согласие пациента должно сохраняться, а не показывать «Сохранено».

Врачебная форма проверяла галочку, показывала успех и закрывалась. Записи
не было нигде. Для документа, которым обосновывают законность записи голоса,
это хуже отсутствия формы: врач уверен, что согласие получено.
"""
from conftest import auth_headers


def _patient(client, doctor, name="Согласный Пациент"):
    return client.post("/api/patients/", headers=auth_headers(doctor),
                       json={"full_name": name, "age": 40, "gender": "М"}).json()["id"]


class TestItActuallySaves:
    def test_consent_survives_and_is_readable(self, client, doctor):
        pid = _patient(client, doctor)
        r = client.post("/api/consents/", headers=auth_headers(doctor),
                        json={"patient_id": pid, "kind": "recording", "granted": True,
                              "text_version": "v1", "text": "Текст согласия"})
        assert r.status_code == 201, r.text
        got = client.get(f"/api/consents/?patient_id={pid}", headers=auth_headers(doctor))
        assert got.status_code == 200
        rows = got.json()
        assert len(rows) == 1
        assert rows[0]["kind"] == "recording" and rows[0]["granted"] is True

    def test_it_stores_who_when_and_what_exactly(self, client, doctor):
        """Формулировка со временем меняется, а доказывать придётся согласие
        на ту, что была на экране в тот день."""
        pid = _patient(client, doctor, "Пациент Хэш")
        r = client.post("/api/consents/", headers=auth_headers(doctor),
                        json={"patient_id": pid, "kind": "recording",
                              "text_version": "v1", "text": "Ровно этот текст"}).json()
        assert r["patient_id"] == pid
        assert r["created_at"]
        assert r["text_version"] == "v1"
        assert r["text_hash"] and len(r["text_hash"]) == 64

    def test_same_text_gives_the_same_hash(self, client, doctor):
        pid = _patient(client, doctor, "Пациент Два Хэша")
        mk = lambda cid: client.post("/api/consents/", headers=auth_headers(doctor),
                                     json={"patient_id": pid, "kind": "recording",
                                           "text": "  Один текст  ", "client_id": cid}).json()
        assert mk("a")["text_hash"] == mk("b")["text_hash"]


class TestTwoKindsAreSeparate:
    def test_recording_and_training_are_independent(self, client, doctor):
        """Согласиться на запись приёма и отказать в обучении моделей —
        нормальное решение пациента. Один флаг на оба подменял бы его волю."""
        pid = _patient(client, doctor, "Пациент Раздельный")
        client.post("/api/consents/", headers=auth_headers(doctor),
                    json={"patient_id": pid, "kind": "recording", "granted": True})
        client.post("/api/consents/", headers=auth_headers(doctor),
                    json={"patient_id": pid, "kind": "training", "granted": False})
        rows = client.get(f"/api/consents/?patient_id={pid}", headers=auth_headers(doctor)).json()
        by_kind = {r["kind"]: r["granted"] for r in rows}
        assert by_kind == {"recording": True, "training": False}

    def test_unknown_kind_is_refused(self, client, doctor):
        pid = _patient(client, doctor, "Пациент Чужой Вид")
        r = client.post("/api/consents/", headers=auth_headers(doctor),
                        json={"patient_id": pid, "kind": "marketing"})
        assert r.status_code == 422


class TestRetryDoesNotDuplicate:
    def test_same_client_id_returns_the_same_record(self, client, doctor):
        """Ответ потерялся в сети, врач нажал снова — вернуться должна та же
        запись, а не появиться вторая."""
        pid = _patient(client, doctor, "Пациент Повтор")
        body = {"patient_id": pid, "kind": "recording", "client_id": "fixed-key-1"}
        first = client.post("/api/consents/", headers=auth_headers(doctor), json=body)
        second = client.post("/api/consents/", headers=auth_headers(doctor), json=body)
        assert first.json()["id"] == second.json()["id"]
        rows = client.get(f"/api/consents/?patient_id={pid}", headers=auth_headers(doctor)).json()
        assert len(rows) == 1, rows

    def test_without_a_key_two_presses_make_two_records(self, client, doctor):
        """Без ключа идемпотентности повтор — это новое решение пациента,
        а не дубль: так фиксируется отзыв и повторное согласие."""
        pid = _patient(client, doctor, "Пациент Без Ключа")
        body = {"patient_id": pid, "kind": "recording"}
        client.post("/api/consents/", headers=auth_headers(doctor), json=body)
        client.post("/api/consents/", headers=auth_headers(doctor), json=body)
        rows = client.get(f"/api/consents/?patient_id={pid}", headers=auth_headers(doctor)).json()
        assert len(rows) == 2


class TestScoping:
    def test_foreign_patient_is_refused(self, client, doctor, second_doctor):
        pid = _patient(client, doctor, "Пациент Первого")
        r = client.post("/api/consents/", headers=auth_headers(second_doctor),
                        json={"patient_id": pid, "kind": "recording"})
        assert r.status_code == 404

    def test_foreign_consent_is_not_listed(self, client, doctor, second_doctor):
        pid = _patient(client, doctor, "Пациент Списка")
        client.post("/api/consents/", headers=auth_headers(doctor),
                    json={"patient_id": pid, "kind": "recording"})
        r = client.get(f"/api/consents/?patient_id={pid}", headers=auth_headers(second_doctor))
        assert r.status_code == 404

    def test_anonymous_is_refused(self, client, doctor):
        pid = _patient(client, doctor, "Пациент Аноним")
        assert client.get(f"/api/consents/?patient_id={pid}").status_code in (401, 403)


class TestFormContract:
    """Форма должна соответствовать критериям приёмки, а не «показывать успех»."""

    def test_success_only_after_the_server_answers(self, client):
        js = client.get("/app.js").text
        i = js.index('if($("consentSave"))')
        block = js[i:i + 2200]
        # тост об успехе живёт внутри обработки ответа, а не рядом с кликом
        assert "consent_saved" in block
        assert block.index("apiFetch") < block.index('t("consent_saved")')
        assert "if(!r.ok)throw" in block.replace(" ", "")

    def test_failure_keeps_the_form_open_with_the_data(self, client):
        js = client.get("/app.js").text
        i = js.index('if($("consentSave"))')
        block = js[i:i + 2200]
        # Границу ветки ошибки берём по markNetDown(): он есть только во
        # внешнем catch. Поиск по первому ".catch(" попадал во вложенный,
        # который висит на втором запросе.
        cat = block[block.index("markNetDown()"):block.index("finally")]
        assert "consent_save_err" in cat
        assert 'classList.remove("show")' not in cat, "окно закрылось при ошибке"

    def test_retry_reuses_one_key(self, client):
        js = client.get("/app.js").text
        assert "_consentKey=_fbUuid()" in js.replace(" ", "")
        assert "client_id:_consentKey" in js.replace(" ", "")

    def test_two_kinds_are_sent_separately(self, client):
        js = client.get("/app.js").text
        i = js.index('if($("consentSave"))')
        block = js[i:i + 2200]
        assert 'kind:"recording"' in block and 'kind:"training"' in block

    def test_existing_consent_is_shown_on_open(self, client):
        """Без этого врач не отличает «согласия не было» от «не сохранилось»."""
        js = client.get("/app.js").text
        assert "_consentLoadExisting" in js
        assert "/api/consents/?patient_id=" in js
