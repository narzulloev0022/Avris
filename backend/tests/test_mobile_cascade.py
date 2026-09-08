"""Мобильные переопределения должны стоять после своих базовых правил.

Медиазапрос не повышает специфичность: правило, объявленное раньше базового,
проигрывает ему при равных селекторах и молча ничего не делает. Так в проекте
не работали семь мобильных правил — и заметить это было нечем, потому что
CSS не падает и не жалуется.
"""
import re

import pytest

# селектор → свойства, которые переопределяются на узком экране
OVERRIDES = {
    ".pat-ctx": ("margin", "padding"),
    ".pat-ctx-top": ("gap",),
    ".pat-ctx-bottom": ("gap",),
    ".rec-lang-btn": ("padding", "font-size"),
    ".consult-tabs": ("gap",),
    ".soap-c textarea": ("font-size",),
    ".nr-modal-card": ("width", "padding"),
}


@pytest.fixture(scope="module")
def css(client):
    return client.get("/styles.css").text


@pytest.mark.parametrize("sel", sorted(OVERRIDES))
def test_override_comes_after_the_base_rule(css, sel):
    base = re.search(r"(?m)^" + re.escape(sel) + r"\{", css)
    assert base, f"базовое правило {sel} исчезло"
    override = css.index("@media(max-width:480px){" + sel + "{")
    assert override > base.start(), (
        f"{sel}: мобильное правило стоит до базового и потому не работает"
    )


@pytest.mark.parametrize("sel,props", sorted(OVERRIDES.items()))
def test_override_still_carries_its_declarations(css, sel, props):
    i = css.index("@media(max-width:480px){" + sel + "{")
    block = css[i:css.index("}}", i)]
    for p in props:
        assert p + ":" in block, f"{sel}: потеряно свойство {p}"


DELETED = ["cmd-bar", "cmd-stat", "cmd-v", "stat-card", "stat-v", "stat-icon",
           "tsb-num", "trend-bars", "time-saved-banner"]


@pytest.mark.parametrize("cls", DELETED)
def test_components_removed_in_v2_have_no_css_left(css, cls):
    """Их разметку убрали при редизайне V2, а стили остались и создавали
    видимость работы: половина «мёртвых» мобильных правил целилась в них."""
    assert "." + cls not in css
