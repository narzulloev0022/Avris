"""Что видит человек и что видит программа, когда адрес не найден.

Врач, промахнувшийся мимо адреса, должен получить страницу с дорогой назад,
а клиент API — прежний JSON: по нему написан весь фронтенд и обработка ошибок
в приложении пациента.
"""
import pytest


class TestNotFound:
    def test_unknown_page_returns_branded_html(self, client):
        r = client.get("/nonexistent-page")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("text/html")
        assert "404" in r.text
        # Тупик без выхода — худшее, что можно показать врачу в смену.
        assert "/app" in r.text

    def test_unknown_api_path_stays_json(self, client):
        r = client.get("/api/definitely-not-a-route")
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}

    def test_api_error_bodies_are_untouched(self, client):
        """Обработчик 404 не должен перехватывать чужие коды."""
        r = client.get("/api/patients/")
        assert r.status_code in (401, 403)
        assert "detail" in r.json()


class TestPricing:
    def test_pricing_page_is_public(self, client):
        """Ссылку на тарифы мы кладём в письма клиникам: она не должна
        зависеть ни от авторизации, ни от переключателя лендинга."""
        r = client.get("/pricing")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "$39" in r.text and "$79" in r.text and "$199" in r.text

    def test_pricing_has_a_russian_alias(self, client):
        assert client.get("/tarify").status_code == 200

    def test_request_form_posts_to_the_existing_intake(self, client):
        """Форма не заводит свой эндпоинт: заявки идут туда же, куда с
        лендинга, вместе с тарифом, который человек смотрел."""
        html = client.get("/pricing").text
        assert "/api/waitlist" in html
        assert '"plan"' in html or "plan:" in html


class TestDeepLinks:
    @pytest.mark.parametrize("path", ["/app", "/app/", "/app/consultation", "/app/patients/7"])
    def test_any_app_subpath_serves_the_shell(self, client, path):
        """Ссылку на экран можно переслать коллеге и открыть по F5 —
        маршрутизация внутри SPA своя, серверу хватает оболочки."""
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "appShell" in r.text

    def test_app_shell_carries_the_connection_bar(self, client):
        """Полоса состояния связи должна быть в разметке, а не появляться
        динамически: её показывают ровно тогда, когда сети уже нет."""
        r = client.get("/app")
        assert 'id="netBar"' in r.text
        assert 'id="netBarRetry"' in r.text

    def test_work_in_progress_survives_a_failed_generation(self, client):
        """Кнопка генерации чистит поля под скелетон. Если запрос не дошёл,
        текст врача обязан вернуться на место, а не остаться стёртым."""
        r = client.get("/app.js")
        assert "_prevSoap" in r.text
        assert "_restoreSoap" in r.text
        assert "t_soap_kept" in r.text

    def test_referral_qr_is_never_faked(self, client):
        """Без ответа сервера QR рисовался с выдуманным токеном: лаборатория
        сканирует, упирается в «не найдено», а пациента уже отпустили."""
        r = client.get("/app.js")
        assert "lab_qr_offline" in r.text
        js = r.text
        i = js.find("function buildQR(")
        assert i != -1
        # заглушка стоит раньше ветки с демо-токеном
        assert js.index("labQrOff", i) < js.index("demoToken", i)

    def test_shell_ships_the_offline_audio_store(self, client):
        """Аудио, не доехавшее до распознавания, должно переживать перезагрузку:
        врач жмёт F5, надеясь «починить», и не должен терять наговорённое."""
        r = client.get("/app.js")
        assert "avris-audio" in r.text
        assert "restorePendingAudio" in r.text
        # Голос пациента на диске — с ограничением по сроку и по владельцу.
        assert "AUDIO_TTL" in r.text

    def test_login_screen_has_its_own_bar(self, client):
        """Врач, который не смог войти из-за сети, должен видеть причину —
        а не гадать над «Ошибка сети» под кнопкой."""
        r = client.get("/app")
        assert 'id="loginNetBar"' in r.text
        assert 'id="loginNetBarRetry"' in r.text
