# Session 1-2 + Polish (Апрель–Май 2026)

> Этот файл задумывался как лог Session 1-2, но к маю 2026 был расширен и стал общим snapshot всего сделанного на frontend-уровне. Полные детали Session 3 — в `Session3-Progress.md`. Свежие правки — внизу.

---

## Session 1: Design System + Login

- **Design System (Deep Navy):** dark-first CSS-переменные. Новые токены: `--bg-base`, `--bg-surface`, `--bg-card`, `--bg-elevated`, `--bg-input`. Акцент teal (`#4AA391`). Текст: primary/secondary/muted/accent. Legacy-алиасы сохранены.
- **Login screen:** центрированная форма, бренд «AVRIS» + teal dot, tagline, email/password, submit, forgot password, footer. Lang-switcher и theme-switcher в правом верхнем углу.

## Session 2: Dashboard + Consultation

- **Dashboard:** command bar, stat cards с hover, список пациентов с slide-in action button, score bars.
- **Consultation:** 55/45 split-screen, recording surface (16-bar waveform, 3rem timer, 52px record button), transcript-area (`--bg-input`), SOAP panel (4 textarea с зелёными left-borders).
- **Micro-interactions:** `screenIn`, focus ring, типизированный toast, `initWave()` с `.06s` delay increments.
- Удалены дубликаты `@keyframes blink` и `.screen` rule, мёртвый CSS (hero/metrics-row).

## Session 3: Cream theme + Mobile + i18n

См. отдельный файл `Session3-Progress.md`. Кратко:
- Mobile dashboard 480px (2×2 stats grid с red critical row)
- Light theme на RAL 9001 Cream (`#E9E0D2`)
- Topbar SVG icons, settings горизонтальные tabs, унифицированная Consultation light/dark
- STT lang switcher (RU/TJ/EN), полное покрытие i18n

## Session 4: Lab Connect + Time Saved Banner + Evidence Link

- **Time Saved Banner** на дашборде: «Время сэкономлено сегодня: 1.4 часа» с зелёным градиентом
- **Evidence Link** — кликабельные фразы в transcript подсвечивают соответствующие SOAP-карточки (`.evidence-phrase` с tooltip и `data-link`)
- **Lab Connect MVP:**
  - Кнопка «Направить на анализ» в pat-context
  - Lab-modal с QR-кодом (SVG-генерация), список тестов с чекбоксами
  - Вкладка «Анализы» в Consultation: карточка ОАК со статусом «Получено», таблица показателей с цветными флагами, AI-комментарий от Claude Sonnet
  - Notif-dot на колокольчике с переходом на анализы при клике
- 3 вкладки в Consultation: Осмотр / Анализы / История

---

## Polish session (Май 2026) — финальная подготовка к демо

### P1: Все эмодзи → SVG (~26 мест)
- ☰ ✕ ▾ ⚠ ✦ 🎤 ⚡ 🔬 📄 ✅ 🔴 ⚠️ заменены на inline SVG
- В JS добавлены константы `SVG_WARN`, `SVG_BOLT`, `SVG_CHECK`, `SVG_ALERT` для шаблонов (updatePatCtx, renderICUGrid, openModal)
- `toast()` теперь использует SVG-иконку по типу (ok/err/info), эмодзи удалены из i18n-строк (`t_soap`, `t_filled`, и т.д.)

### P2: Критичные баги
- `recBadge` теперь обновляется при смене глобального языка через `applyLang()`
- `renderEvidenceTranscript` локализован — добавлены `EV_TEMPLATES` и `EV_SOAP` для RU/TJ/EN
- Удалён дубликат `hist_yest`/`hist_earlier` в TR.tj
- Удалены мёртвые ключи (`login_desc`, `login_f1..f3`, `login_title`, `login_sub`, `btn_vfill`)
- `.filter-bar` reset-button получил собственное мобильное правило (full-width)
- `.hist-entry:hover` отрицательный margin исправлен с -16px на -12px (под padding `.main` на 480px)

### P3: Локализация захардкоженных RU-строк
- 14 новых TR-ключей: `years_short`, `ph_nr_exam`, `ph_nr_plan`, `vfill_exam`, `vfill_plan`, `unit_gl`, `unit_109l`, `unit_mmh`, `lab_date`, `none`, `login_footer`, `vital_hr_unit`, `vital_bp_unit`, `norm_hr/bp/spo2`
- `trLines` (typewriter) → `TR_LINES` per language
- Суффикс «г» в `pat-ctx-meta`/`pmMeta` → `t("years_short")`
- nrFab voice fill → `t("vfill_exam"/"vfill_plan")`
- vKeys в pmVitals — units и norms через `t()`
- Lab-card date и units — обёрнуты в `data-i18n`

### P4: Светлая тема
- `.consult-left/right` scrollbar → `var(--border)` (был невидимый белый на светлом)
- `.transcript` border-radius унифицирован (12px в обеих темах)
- Light overrides для модальных оверлеев (.modal, .confirm-ov, .sidebar-overlay, .lab-modal-ov)
- `.icu-c.critical` градиент усилен в светлой теме

### P5: Mobile 480px
- Stat-cards: ellipsis на label/sub, padding 10px 6px
- ICU-vg: 6 боксов в 2 колонки (вместо 3)
- Labs-table обёрнут в `.labs-table-wrap` со скроллом и `min-width: 380px`

### Локализация data-layer (расширенная)

#### Settings inputs
- Новые ключи `doctor_name`/`doctor_spec` + новый атрибут `data-i18n-val`
- `applyLang()` теперь обрабатывает `[data-i18n-val]` и устанавливает `el.value`

#### Patient data
- Все 6 пациентов получили поля `_en`: `ward_en`, `diag_en`, `current_en`, `hist_en`, `meds_en`, `allergy_en`, `insight_en`
- Демо: `gender_en` («F»/«M»), `blood_en` (стандартная EN-нотация «O+», «A-», «AB+»)
- `height` и `weight` стали числами; рендерятся через `t("unit_cm")/t("unit_kg")`
- `updatePatCtx`, `openModal`, `buildPatCtxDropdown`, `buildPatList`, `renderWards` выбирают `_en` поля при `lang === "en"`

#### ICU/post-ICU
- `bed: "ОРИТ-1"` рефакторен в `bedT: "icu", bedN: 1`
- `doctor: "Др. Назаров"` → `doctor: "Назаров"` (префикс через `t("dr_prefix")` если нужен)
- TR-ключи: `dr_prefix`, `bed_icu` (RU:ОРИТ / TJ:ОРИТ / EN:ICU), `bed_post` (RU:ПР / TJ:ПП / EN:RW)

#### Activity timeline на дашборде
- Перенесён со статичного HTML на динамический рендер через `renderActivity()`
- Тянет топ-3 из `histData`, типы (SOAP/Round/Export) и time labels локализованы
- Обновляется при `init()`, `applyLang()`, после `soapConfirm`/`nrModalSave`

### A11y
- `role="tablist"` + `role="tab"` + `aria-selected` на `.consult-tabs`, `.set-nav`, `.hist-pills`
- Click-handlers (`switchConsultTab`, set-nav-item.onclick, hist-pill.onclick) синхронизируют `aria-selected` с `.active`
- Все ~30 inline SVG получили `aria-hidden="true"` (включая SVG-константы в JS-шаблонах и pm-sparkline)

### Lab Connect — расширение до production-ready

- Переводы `lab_oak`/`lab_oam` обновлены: RU:«ОАК», TJ:«ТУХ (Таҳлили умумии хун)», EN:«CBC (Complete Blood Count)» (аналогично для ОАМ)
- 41 новый TR-ключ для групп и тестов
- Структура `LAB_GROUPS` с 6 группами и ~38 тестами:
  - Clinical (ОАК, ОАМ, группа крови, коагулограмма)
  - Biochem (8 тестов: глюкоза, белок, АЛТ/АСТ, креатинин, холестерин, билирубин, железо/ферритин, HbA1c)
  - Vitamins (D, B12, фолиевая, Mg/K/Ca)
  - Infectious (ВИЧ, гепатит B/C, малярия, сифилис, COVID-19, туберкулёз)
  - Hormones (ТТГ, T3/T4, кортизол, тестостерон/эстрадиол, ПСА, ХГЧ)
  - Instrumental (ЭКГ, рентген, УЗИ, ЭхоКГ, МРТ, КТ, ФГДС, спирометрия, Холтер)
- UI: поиск с realtime-фильтром, счётчик «Выбрано: N», кнопка «Выбрать все в группе» (только видимые), 2 колонки на десктопе
- Состояние выбора (`labChecked`) сохраняется при смене языка/перерисовке
- Modal расширена до 680px max-width, с overflow-y:auto

---

## Текущий статус проекта

| Компонент | Статус |
|-----------|--------|
| Design System (Deep Navy + RAL 9016 light) | ✅ |
| Все 6 экранов redesign | ✅ |
| Mobile 480px на всех экранах | ✅ |
| Полное покрытие RU/TJ/EN UI и data | ✅ |
| Patient data — EN-варианты | ✅ |
| ICU-data — структурированные bed/doctor | ✅ |
| Lab Connect (38 тестов в 6 группах) | ✅ |
| Evidence Link | ✅ |
| Time Saved Banner | ✅ |
| Activity Timeline (динамический) | ✅ |
| A11y (tabs, aria-selected, aria-hidden) | ✅ |
| Все эмодзи → SVG | ✅ |
| Whisper STT | 🔧 Запланировано |
| Claude Sonnet SOAP-генерация | 🔧 Запланировано |
| Бэкенд / БД / Auth | ❌ Не начато |
| Persistance (localStorage → БД) | ❌ |
| Realtime ОРИТ (WebSocket) | ❌ |

---

## Git

- **Репозиторий:** `github.com:narzulloev0022/Avris.git`
- **Ветка:** `main`
- Последние ключевые коммиты polish-сессии (от старых к новым):
  - `42275e9` Replace all emojis with inline SVG icons
  - `6a69138` Fix 6 critical bugs from audit
  - `04358ff` Localize hardcoded RU strings to RU/TJ/EN
  - `1a850db` Fix light theme inconsistencies
  - `b8e2669` Improve mobile adaptation at 480px
  - `fcb684b` Localize patient/ICU data layer + a11y for tabs and SVGs
  - `fb4f151` Localize patient data layer for EN: ward, hist/meds/allergy, gender, blood
  - `0a989cb` Expand Lab Connect: 6 grouped categories, ~38 tests, search & counter

---

## Связанные разделы

- [[../Design System/README|Дизайн-система]] — Deep Navy + RAL 9016 палитра, breakpoints, градиенты
- [[../Development/README|Разработка]] — архитектура и API-спецификации
- [[Session3-Progress|Session 3 Progress]] — Cream theme, mobile, i18n
- [[i18n-Reference|i18n Reference]] — все ключи переводов
- [[../Product/LAB-CONNECT|Lab Connect spec]]
