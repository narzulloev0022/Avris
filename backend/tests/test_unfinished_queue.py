"""Очередь незавершённых записей.

Врача отвлекают посреди осмотра — это норма работы в отделении, а не сбой.
Черновик хранит начатое, но до сих пор о нём знала только карта конкретного
пациента: чтобы вспомнить, что не дописано, врачу пришлось бы обойти всех.
"""
from conftest import auth_headers


def _patient(client, doctor, name="Пациент Очередь"):
    r = client.post("/api/patients/", headers=auth_headers(doctor),
                    json={"full_name": name, "age": 40, "ward": "Терапия D1"})
    assert r.status_code == 201
    return r.json()["id"]


def _mk(client, doctor, pid, status="draft", **kw):
    body = {"patient_id": pid, "soap_a": "Начато", "status": status}
    body.update(kw)
    r = client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
    assert r.status_code == 201
    return r.json()


class TestQueue:
    def test_drafts_are_listed_across_all_patients(self, client, doctor):
        """Иначе, чтобы вспомнить незаконченное, врачу надо обойти все карты."""
        a, b = _patient(client, doctor, "Первый"), _patient(client, doctor, "Второй")
        _mk(client, doctor, a, "draft")
        _mk(client, doctor, b, "draft")
        _mk(client, doctor, a, "confirmed")
        rows = client.get("/api/consultations/?status=draft&limit=200",
                          headers=auth_headers(doctor)).json()
        # в общей тестовой базе есть черновики и от соседних файлов —
        # смотрим только на двух своих пациентов
        mine = [r for r in rows if r["patient_id"] in (a, b)]
        assert len(mine) == 2
        assert {r["patient_id"] for r in mine} == {a, b}
        assert {r["status"] for r in rows} == {"draft"}

    def test_confirmed_filter_excludes_drafts(self, client, doctor):
        pid = _patient(client, doctor)
        _mk(client, doctor, pid, "draft")
        _mk(client, doctor, pid, "confirmed")
        rows = client.get(f"/api/consultations/?status=confirmed&patient_id={pid}",
                          headers=auth_headers(doctor)).json()
        assert [r["status"] for r in rows] == ["confirmed"]

    def test_bad_status_is_rejected(self, client, doctor):
        assert client.get("/api/consultations/?status=whatever",
                          headers=auth_headers(doctor)).status_code == 422

    def test_counter_is_on_the_dashboard(self, client, doctor):
        """Чтобы увидеть незаконченное, врач не должен никуда заходить."""
        pid = _patient(client, doctor)
        before = client.get("/api/stats/dashboard", headers=auth_headers(doctor)).json()
        _mk(client, doctor, pid, "draft")
        after = client.get("/api/stats/dashboard", headers=auth_headers(doctor)).json()
        assert after["drafts_open"] == before["drafts_open"] + 1

    def test_confirming_takes_it_out_of_the_queue(self, client, doctor):
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "draft")
        client.patch(f"/api/consultations/{c['id']}", headers=auth_headers(doctor),
                     json={"status": "confirmed"})
        rows = client.get(f"/api/consultations/?status=draft&patient_id={pid}",
                          headers=auth_headers(doctor)).json()
        assert rows == []

    def test_queue_is_per_doctor(self, client, doctor, second_doctor):
        pid = _patient(client, doctor)
        _mk(client, doctor, pid, "draft")
        rows = client.get("/api/consultations/?status=draft",
                          headers=auth_headers(second_doctor)).json()
        assert rows == []   # у второго врача своих черновиков нет вовсе


class TestDiscard:
    def test_draft_can_be_discarded(self, client, doctor):
        """Без этого очередь росла бы вечно, и врач перестал бы на неё
        смотреть — а тогда она хуже, чем её отсутствие."""
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "draft")
        r = client.delete(f"/api/consultations/{c['id']}", headers=auth_headers(doctor))
        assert r.status_code == 204
        assert client.get(f"/api/consultations/?status=draft&patient_id={pid}",
                          headers=auth_headers(doctor)).json() == []

    def test_confirmed_record_cannot_be_deleted(self, client, doctor):
        """Заверенная запись — документ карты. Ошибку в ней исправляют
        новой редакцией, а не исчезновением."""
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "confirmed")
        r = client.delete(f"/api/consultations/{c['id']}", headers=auth_headers(doctor))
        assert r.status_code == 409
        assert client.get(f"/api/consultations/?patient_id={pid}",
                          headers=auth_headers(doctor)).json()

    def test_foreign_draft_is_not_found(self, client, doctor, second_doctor):
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "draft")
        assert client.delete(f"/api/consultations/{c['id']}",
                             headers=auth_headers(second_doctor)).status_code == 404
        # и она осталась на месте
        assert len(client.get(f"/api/consultations/?status=draft&patient_id={pid}",
                              headers=auth_headers(doctor)).json()) == 1


class TestDoctorFacingQueue:
    """Очередь бесполезна, если из неё нельзя вернуться в работу."""

    def test_bar_appears_only_when_there_is_something(self, client):
        """Постоянный «0 не дописано» врач перестаёт замечать через день."""
        js = client.get("/app.js").text
        i = js.index("function renderUnfinBar(")
        assert "b.hidden=!n" in js[i:i + 200].replace(" ", "")

    def test_continue_binds_the_session_to_the_record(self, client):
        """Без привязки «Подтвердить» завело бы в карте вторую запись."""
        js = client.get("/app.js").text
        i = js.index("function continueDraft(")
        block = js[i:i + 900]
        assert "savedId:c.id" in block.replace(" ", "")
        assert "soapS" in block and "updatePatCtx()" in block

    def test_discard_asks_first(self, client):
        js = client.get("/app.js").text
        i = js.index("function discardDraft(")
        block = js[i:i + 900]
        assert "confirm2(" in block
        assert 'method:"DELETE"' in block
        # и своя формулировка, когда пациент не выбран
        assert "unfin_discard_q0" in block

    def test_discard_clears_a_session_pointing_at_it(self, client):
        """Иначе следующее «Подтвердить» уйдёт в удалённую запись."""
        js = client.get("/app.js").text
        i = js.index("function discardDraft(")
        assert "_exam.savedId===c.id" in js[i:i + 1200].replace(" ", "")

    def test_server_time_is_read_as_utc(self, client):
        """Сервер отдаёт UTC без пометки, браузер читает как местное — для
        Душанбе это пять часов ошибки в каждой дате приложения."""
        js = client.get("/app.js").text
        assert "function srvDate(" in js
        assert "new Date(c.created_at)" not in js
        assert "new Date(it.timestamp)" not in js
