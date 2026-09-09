"""Черновик — не документ, а исправление документа не бесследно.

Две вещи, ради которых статус вообще заведён:

1. Незаверенная запись существует, чтобы работа врача не терялась, но
   никуда, кроме его собственного рабочего стола, не попадает. Пациент,
   эпикриз, счётчики и выгрузка данных её не видят.
2. Правка заверенной записи сохраняет прежний текст. В карте не затирают:
   исправление оговаривают, а написанное вчера остаётся читаемым.
"""
from conftest import auth_headers


def _patient(client, doctor, name="Пациент Черновиков"):
    r = client.post("/api/patients/", headers=auth_headers(doctor),
                    json={"full_name": name, "age": 40, "ward": "Терапия D1"})
    assert r.status_code == 201
    return r.json()["id"]


def _mk(client, doctor, pid, status="confirmed", **kw):
    body = {"patient_id": pid, "soap_a": "Бронхит", "status": status}
    body.update(kw)
    r = client.post("/api/consultations/", headers=auth_headers(doctor), json=body)
    assert r.status_code == 201, r.text
    return r.json()


class TestDraftIsNotADocument:
    def test_draft_is_created_and_marked(self, client, doctor):
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "draft")
        assert c["status"] == "draft"
        assert c["confirmed_at"] is None

    def test_confirmed_is_the_default(self, client, doctor):
        """Старые клиенты не присылают статус — и не должны внезапно
        начать писать черновики вместо записей."""
        pid = _patient(client, doctor)
        r = client.post("/api/consultations/", headers=auth_headers(doctor),
                        json={"patient_id": pid, "soap_a": "Бронхит"})
        assert r.json()["status"] == "confirmed"
        assert r.json()["confirmed_at"] is not None

    def test_doctor_still_sees_own_drafts(self, client, doctor):
        """Иначе черновик — то же, что потерянная работа."""
        pid = _patient(client, doctor)
        _mk(client, doctor, pid, "draft")
        rows = client.get(f"/api/consultations/?patient_id={pid}",
                          headers=auth_headers(doctor)).json()
        assert [r["status"] for r in rows] == ["draft"]

    def test_draft_is_not_counted_as_work_done(self, client, doctor):
        pid = _patient(client, doctor)
        before = client.get("/api/stats/dashboard", headers=auth_headers(doctor)).json()
        _mk(client, doctor, pid, "draft")
        after = client.get("/api/stats/dashboard", headers=auth_headers(doctor)).json()
        assert after["consultations_today"] == before["consultations_today"]

    def test_confirming_counts_it(self, client, doctor):
        pid = _patient(client, doctor)
        before = client.get("/api/stats/dashboard", headers=auth_headers(doctor)).json()
        c = _mk(client, doctor, pid, "draft")
        client.patch(f"/api/consultations/{c['id']}", headers=auth_headers(doctor),
                     json={"status": "confirmed"})
        after = client.get("/api/stats/dashboard", headers=auth_headers(doctor)).json()
        assert after["consultations_today"] == before["consultations_today"] + 1

    def test_draft_does_not_reach_the_epicrisis(self, client, doctor):
        """Эпикриз — выписной документ. Незаконченная мысль врача в нём
        становится фактом истории болезни."""
        import epicrises
        pid = _patient(client, doctor)
        _mk(client, doctor, pid, "draft", soap_a="ЧЕРНОВИКОВЫЙ ДИАГНОЗ")
        _mk(client, doctor, pid, "confirmed", soap_a="Заверенный диагноз")
        from database import SessionLocal
        db = SessionLocal()
        try:
            from models import Patient
            p = db.query(Patient).filter(Patient.id == pid).first()
            hist, _counts = epicrises._build_history(db, p)
        finally:
            db.close()
        assert "ЧЕРНОВИКОВЫЙ" not in hist
        assert "Заверенный диагноз" in hist

    def test_accuracy_is_measured_on_confirmed_only(self, client, doctor):
        """Точность считают по тому, что врач заверил, а не по тому,
        что он ещё правит."""
        pid = _patient(client, doctor)
        me = client.get("/api/auth/me", headers=auth_headers(doctor)).json()
        was = me.get("soap_accurate_count") or 0
        _mk(client, doctor, pid, "draft", soap_was_edited=False)
        me2 = client.get("/api/auth/me", headers=auth_headers(doctor)).json()
        assert (me2.get("soap_accurate_count") or 0) == was


class TestCorrectionsLeaveATrace:
    def test_editing_a_confirmed_record_keeps_the_old_text(self, client, doctor):
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "confirmed", soap_a="Острый бронхит")
        client.patch(f"/api/consultations/{c['id']}", headers=auth_headers(doctor),
                     json={"soap_a": "Пневмония"})
        vs = client.get(f"/api/consultations/{c['id']}/versions",
                        headers=auth_headers(doctor)).json()
        assert len(vs) == 1
        assert vs[0]["soap_a"] == "Острый бронхит"      # прежний текст цел
        now = client.get(f"/api/consultations/?patient_id={pid}",
                         headers=auth_headers(doctor)).json()[0]
        assert now["soap_a"] == "Пневмония"
        assert now["revisions"] == 1

    def test_editing_a_draft_leaves_no_version(self, client, doctor):
        """У черновика ещё нет содержания, за которое кто-то поручился."""
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "draft", soap_a="Первая мысль")
        client.patch(f"/api/consultations/{c['id']}", headers=auth_headers(doctor),
                     json={"soap_a": "Вторая мысль"})
        vs = client.get(f"/api/consultations/{c['id']}/versions",
                        headers=auth_headers(doctor)).json()
        assert vs == []

    def test_versions_are_numbered_in_order(self, client, doctor):
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "confirmed", soap_a="Первый")
        for txt in ("Второй", "Третий"):
            client.patch(f"/api/consultations/{c['id']}", headers=auth_headers(doctor),
                         json={"soap_a": txt})
        vs = client.get(f"/api/consultations/{c['id']}/versions",
                        headers=auth_headers(doctor)).json()
        assert [v["no"] for v in vs] == [1, 2]
        assert [v["soap_a"] for v in vs] == ["Первый", "Второй"]

    def test_a_save_that_changes_nothing_makes_no_version(self, client, doctor):
        """Иначе история заполнится пустыми правками и перестанет читаться."""
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "confirmed", soap_a="Бронхит")
        client.patch(f"/api/consultations/{c['id']}", headers=auth_headers(doctor),
                     json={"soap_a": "Бронхит"})
        vs = client.get(f"/api/consultations/{c['id']}/versions",
                        headers=auth_headers(doctor)).json()
        assert vs == []

    def test_confirmation_cannot_be_taken_back(self, client, doctor):
        """Подпись не отзывают: ошибку исправляют новой записью."""
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "confirmed")
        r = client.patch(f"/api/consultations/{c['id']}", headers=auth_headers(doctor),
                         json={"status": "draft"})
        assert r.status_code == 422

    def test_foreign_record_is_not_found(self, client, doctor, second_doctor):
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "confirmed")
        assert client.patch(f"/api/consultations/{c['id']}",
                            headers=auth_headers(second_doctor),
                            json={"soap_a": "чужая правка"}).status_code == 404
        assert client.get(f"/api/consultations/{c['id']}/versions",
                          headers=auth_headers(second_doctor)).status_code == 404


class TestPrintedCopy:
    def _text(self, client, doctor, cid):
        r = client.get(f"/api/consultations/{cid}/pdf", headers=auth_headers(doctor))
        assert r.status_code == 200
        from pdfminer.high_level import extract_text
        from io import BytesIO
        return extract_text(BytesIO(r.content))

    def test_draft_prints_without_a_signature_line(self, client, doctor):
        """Пустая линия под подпись — приглашение расписаться на том, что
        врач не заверил. Лист с такой линией подошьют в карту."""
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "draft")
        text = self._text(client, doctor, c["id"])
        assert "ЧЕРНОВИК" in text
        assert "подпись" not in text.lower()

    def test_confirmed_prints_with_a_signature(self, client, doctor):
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "confirmed")
        text = self._text(client, doctor, c["id"])
        assert "ЧЕРНОВИК" not in text
        assert "подпись" in text.lower()

    def test_a_corrected_document_says_so(self, client, doctor):
        """Иначе исправление выглядит как исходная запись."""
        pid = _patient(client, doctor)
        c = _mk(client, doctor, pid, "confirmed", soap_a="Бронхит")
        client.patch(f"/api/consultations/{c['id']}", headers=auth_headers(doctor),
                     json={"soap_a": "Пневмония"})
        text = self._text(client, doctor, c["id"])
        assert "Исправлено" in text
        assert "редакция 2" in text


class TestDoctorFacingContract:
    """Что врач видит и чем управляет."""

    def test_there_are_two_buttons_not_one(self, client):
        html = client.get("/app").text
        assert 'id="soapDraft"' in html
        assert 'id="soapConfirm"' in html
        js = client.get("/app.js").text
        assert "saveConsult(true)" in js

    def test_draft_keeps_the_exam_session_open(self, client):
        """Черновик — не конец осмотра: врач продолжит ту же запись, и она
        не должна уехать вторым документом в карту."""
        js = client.get("/app.js").text
        assert "if(!asDraft)examSaved()" in js.replace(" ", "")

    def test_confirming_a_saved_draft_edits_it(self, client):
        """Повторный POST с тем же ключом вернул бы прежний черновик, и
        «Подтвердить» не изменило бы ничего."""
        js = client.get("/app.js").text
        i = js.index("_exam.savedId")
        block = js[i:i + 900]
        assert 'method:"PATCH"' in block
        assert 'pbody.status="confirmed"' in block.replace(" ", "")

    def test_failed_edit_does_not_promise_a_queue(self, client):
        """Очередь хранит только создание записи. Обещать «отправим позже»
        для правки — врать врачу."""
        js = client.get("/app.js").text
        i = js.index("_exam.savedId")
        block = js[i:i + 1400]
        assert "t_patch_err" in block
        assert "t_save_queued" not in block

    def test_history_marks_drafts_and_corrections(self, client):
        js = client.get("/app.js").text
        assert "hist_draft" in js and "hist_revised" in js
        css = client.get("/styles.css").text
        assert ".hist-mark.draft{" in css and ".hist-mark.rev{" in css
