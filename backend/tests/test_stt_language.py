"""Язык приёма должен доходить до распознавания.

Врач выбирает язык в интерфейсе, но выбор до модели не доходил — она угадывала
сама. На таджикском это ломалось наглядно: язык определялся как «latin», а речь
возвращалась транслитом («si afta bos» вместо «се ҳафта боз»). Читать такую
запись врач не может, а исправлять — дольше, чем написать заново.
"""
import stt


class TestLanguageMapping:
    def test_interface_code_becomes_iso(self):
        """В интерфейсе таджикский — «tj», у распознавания — «tg»."""
        assert stt.STT_LANG["tj"] == "tg"
        assert stt.STT_LANG["ru"] == "ru"
        assert stt.STT_LANG["en"] == "en"

    def test_unknown_code_falls_back_to_guessing(self):
        """Чужой код лучше не подсовывать модели: пусть определяет сама,
        чем декодирует в заведомо неверный язык."""
        assert stt.STT_LANG.get("de") is None
        assert stt.STT_LANG.get("") is None


class TestPrompts:
    def test_every_supported_language_has_its_own_prompt(self):
        for iso in set(stt.STT_LANG.values()):
            assert iso in stt.WHISPER_PROMPTS, iso

    def test_cyrillic_languages_get_cyrillic_prompts(self):
        """Подсказка задаёт не только регистр речи, но и письменность —
        английская подсказка тянула таджикский вывод в латиницу."""
        for iso in ("ru", "tg"):
            prompt = stt.WHISPER_PROMPTS[iso]
            assert any("Ѐ" <= ch <= "ӿ" for ch in prompt), iso

    def test_tajik_prompt_is_tajik_not_russian(self):
        """Таджикская подсказка должна быть на таджикском: русская вернёт
        таджикскую речь русскими словами."""
        assert stt.WHISPER_PROMPTS["tg"] != stt.WHISPER_PROMPTS["ru"]
        assert any(ch in stt.WHISPER_PROMPTS["tg"] for ch in "ҳқӯӣғҷҲҚӮӢҒҶ")
