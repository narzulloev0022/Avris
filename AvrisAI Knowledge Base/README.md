# Avris AI — База знаний

Центральная база знаний проекта Avris AI. Организована как Obsidian-вольт.

**Продукт:** Голосовая AI-платформа медицинской документации для врачей Центральной Азии  
**Компания:** Hyperion Labs  
**Версия:** v3.5  
**Стек:** HTML/CSS/JS → OpenAI Whisper (STT) + Claude Sonnet (LLM)  
**Репозиторий:** `git@github.com:narzulloev0022/Avris.git`

---

## Структура вольта

| Раздел | Содержание |
|--------|-----------|
| [[Project Overview/README\|Обзор проекта]] | Бизнес-модель, техстек, юнит-экономика, конкуренты |
| [[Product/README\|Продукт]] | Агент 1, Агент 2, фичи, роадмап |
| [[Design System/README\|Дизайн-система]] | Цвета, типографика, компоненты |
| [[Investor Materials/README\|Материалы для инвесторов]] | Питч, FAQ, due diligence |
| [[Development/README\|Разработка]] | API-спецификации, архитектура, деплой |

---

## Быстрые ссылки

- [[Product/README#Роадмап|Роадмап продукта]]
- [[Development/README#Что нужно сделать|Что нужно сделать]]
- [[Development/Session1-2-Progress|Session 1-2 Progress]]
- [[Development/Session3-Progress|Session 3 — Cream theme + Mobile + i18n]]
- [[Development/i18n-Reference|i18n Reference (RU / TJ / EN)]]
- [[Design System/Cream-Theme-Tokens|Cream Theme Tokens]]
- [[Investor Materials/README#FAQ для инвесторов|FAQ для инвесторов]]
- [[Development/README#API-спецификации (планируемые)|API-спецификации]]

---

## Текущий статус (Май 2026) — готово к демо

| Слой | Статус |
|------|--------|
| Design System (Deep Navy + RAL 9016 light) | ✅ Готово |
| Все 6 экранов (Login / Dashboard / Consultation / Night Round / History / ICU / Settings) | ✅ Готово |
| Lab Connect (38 тестов в 6 группах) | ✅ Готово |
| Evidence Link, Time Saved Banner, Activity Timeline | ✅ Готово |
| Мобильная адаптация (480px) | ✅ Готово |
| RU/TJ/EN с локализацией data-layer (ward_en, diag_en, hist_en, meds_en, allergy_en, gender_en, blood_en) | ✅ Готово |
| A11y (role="tab", aria-selected, aria-hidden SVGs, aria-label) | ✅ Готово |
| Все эмодзи → SVG (~30 мест) | ✅ Готово |
| Whisper STT | 🔧 V2 (Q3 2026) |
| Claude Sonnet SOAP | 🔧 V2 (Q3 2026) |
| Аутентификация / БД / Бэкенд | ❌ V2 (Q3 2026) |
| Realtime ОРИТ / DICOM viewer / PWA | ❌ V3 (Q4 2026) |

---

## Соглашения вольта

- Даты: `ГГГГ-ММ-ДД` в названии файлов (например `2026-04-25 Встреча с инвестором.md`)
- Статус: `#черновик`, `#утверждено`, `#архив`
- Ссылки: `[[Имя заметки]]` — связывайте заметки между собой
- Эта страница — домашняя: установить как «Открывать при запуске» в настройках Obsidian
