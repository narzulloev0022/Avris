"""Атрибут hidden обязан скрывать.

Браузер задаёт hidden через display:none в своей таблице стилей, и любое
авторское правило с display её перебивает. Элемент, который код пометил
скрытым, остаётся на экране — молча, без ошибки. Так висела кнопка
«Отправить исправления» поверх пустой области записи.
"""
import re


def test_rule_exists(client):
    css = client.get("/styles.css").text
    assert "[hidden]{display:none!important}" in css.replace(" ", "")


def test_it_comes_after_the_components(client):
    """Без !important правило проигрывало бы любому более позднему классу;
    с ним — важен ещё и порядок относительно печати."""
    css = client.get("/styles.css").text
    rule = css.index("[hidden]{display:none!important}")
    assert rule > css.index(".fb-submit-btn{")
    assert rule > css.index(".btn{")


def test_print_sheet_still_wins(client):
    """У печатного бланка правило важное и специфичнее — иначе бланк с
    атрибутом hidden не напечатался бы вовсе."""
    css = client.get("/styles.css").text
    assert "body>.print-sheet{display:block!important}" in css.replace(" ", "")


def test_elements_marked_hidden_are_the_ones_we_expect(client):
    """Проверка от обратного: если кто-то снова напишет display в классе
    элемента, который прячут через hidden, тест не поймает — но поймает
    исчезновение самого правила."""
    html = client.get("/app").text
    marked = re.findall(r'id="(\w+)"[^>]*\bhidden\b', html)
    for expected in ("fbSubmit", "netBar", "patCtxConsent"):
        assert expected in marked, f"{expected} больше не помечен hidden"
