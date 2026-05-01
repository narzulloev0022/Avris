# Avris AI — Контекст проекта для Claude

## Что это за проект

**Avris AI** — голосовая платформа медицинской документации для врачей Центральной Азии (основной рынок — Таджикистан и СНГ). Продукт создаётся компанией **Hyperion Labs**.

Основная идея: врач говорит голосом во время осмотра пациента, система автоматически транскрибирует речь и генерирует структурированную медицинскую документацию в формате SOAP. Дополнительно — Lab Connect (направления через QR-код) и AI-комментарии к результатам анализов.

---

## Текущее состояние (май 2026)

Репозиторий содержит один файл — `index.html` — полностью рабочий frontend-прототип (SPA без сборки). Бэкенда нет, все данные захардкожены в памяти.

**Версия приложения:** v3.5 (по footer в Settings)
**Готовность:** Прототип готов к демонстрации инвесторам и клиническим пилотам. Все критичные UI-проблемы исправлены, полное покрытие RU/TJ/EN, accessibility (`role="tab"`, `aria-selected`, `aria-hidden` на SVG).

---

## Технологический стек

### Реализовано
- Чистый HTML/CSS/JS (без фреймворков, без бандлера)
- Шрифты: Inter (UI) + JetBrains Mono (числовые значения)
- ~26 inline SVG-иконок (без эмодзи в продакшен-UI)
- Полный data-layer для пациентов с RU/EN полями (`diag_en`, `current_en`, `hist_en`, `meds_en`, `allergy_en`, `ward_en`, `insight_en`, `gender_en`, `blood_en`)

### Запланировано / Интегрируется
- **STT (распознавание речи):** OpenAI Whisper (фиксировано)
- **LLM (генерация SOAP и анализ):** Claude Sonnet от Anthropic (фиксировано)
- **Аутентификация:** TBD
- **База данных:** TBD
- **Бэкенд:** TBD

---

## Структура приложения

```
index.html (~1300 строк)
├── <style>          — вся CSS (CSS-переменные, dark-first + light override)
├── <body>
│   ├── .login-screen    — экран входа (бренд AVRIS + teal dot)
│   └── .app-shell
│       ├── .sidebar     — навигация (5 пунктов)
│       ├── .topbar      — гамбургер, часы, lang-switcher, уведомления, тема
│       └── .main
│           ├── #dashboard     — дашборд (cmd-bar + 3 stat cards + список пациентов + Avris Score trend + Activity timeline)
│           ├── #consultation  — 3 вкладки: Осмотр / Анализы / История
│           ├── #nightRound    — ночной обход (6 палат, FAB голосового ввода)
│           ├── #history       — история записей с группировкой Сегодня/Вчера/Ранее
│           ├── #monitor       — реанимация (ОРИТ + пост-реанимация)
│           └── #settings      — настройки (5 табов: Профиль/Уведомления/Язык/Тема/О системе)
└── <script>         — вся логика (один IIFE, strict mode)
```

---

## Экраны и функциональность

### Дашборд
- **Time Saved Banner:** «Время сэкономлено сегодня: 1.4 часа» (градиент)
- **Command Bar:** активные / **критические** (красный градиент) / Avris Score / SOAP / Точность AI
- **3 Stat Cards:** SOAP-записи, Сессии, Точность AI (зелёный градиент)
- Список пациентов с поиском (debounced) и фильтрами по диагнозу/отделению
- **Avris Score** trend bars (горизонтальные)
- **Activity Timeline** — рендерится из `histData` (топ-3), обновляется при applyLang и после сохранений

### Осмотр (Consultation) — 3 вкладки
**Вкладка 1: Осмотр**
- Pat-context (имя, отделение, score, диагнозы, аллергии, кнопка «Направить на анализ»)
- 55/45 split: запись слева, SOAP справа
- Waveform-анимация, 3rem timer, REC-кнопка
- STT lang switcher (RU/TJ/EN, независим от UI-языка)
- Transcript с `Evidence Link`-фразами (клик → highlight соответствующей SOAP-карточки)
- 4 SOAP-textarea (S/O/A/P) c AI-бейджем «Claude Sonnet»

**Вкладка 2: Анализы**
- Карточка ОАК с пилюлей «Получено»
- Таблица показателей (Гемоглобин/Лейкоциты/Тромбоциты/СОЭ) с цветными флагами
- Lab Connect AI-комментарий (Claude Sonnet)

**Вкладка 3: История**
- Заглушка (пациент-специфическая история)

### Lab Connect модалка (Killer feature)
- Открывается из Pat-context через кнопку «Направить на анализ»
- QR-код направления (генерируется через SVG)
- **6 групп анализов** (~38 тестов): Клинические / Биохимия / Витамины / Инфекции / Гормоны / Инструментальные
- Поиск по тестам, счётчик «Выбрано: N», «Выбрать все в группе» (только видимые)
- 2 колонки на десктопе, 1 на mobile
- Состояние выбора (`labChecked`) сохраняется при смене языка

### Ночной обход
- Сетка 6 палат (статусы stable/warning), бейджи
- Клик → modal с textarea «Осмотр» + «План лечения»
- FAB-кнопка с микрофоном для авто-заполнения голосом
- Сохранение → запись в historyData + Activity timeline

### История
- Поиск + 4 фильтр-pill (Все / SOAP / Обход / Экспорт)
- Группировка по дням (Сегодня / Вчера / Ранее) с локализованными заголовками

### Реанимация (ICU Monitor)
- Status chips (Всего / Критических / Ср. SpO₂ / время обновления)
- ОРИТ: 3 пациента (структура `bedT:"icu",bedN:N`, рендерится через `t("bed_icu")`)
- Пост-реанимация: 2 пациента (`bedT:"post"`, в EN — «RW», в TJ — «ПП»)
- Карточки с витальными показателями (3 колонки, 2 на 480px), цветные алерты
- Кнопка «Вызвать врача» с SVG молнии для critical/warning

### Настройки
- 5 табов: Профиль / Уведомления / Язык / Тема / О системе
- В профиле: имя/специальность/email через `data-i18n-val` (локализуется)
- Тема (светлая/тёмная), язык интерфейса, язык STT (Whisper)

---

## Данные (всё захардкожено)

### Пациенты (6 штук) — теперь с EN-полями для каждого
| ID | Имя | RU отделение / EN ward_en | Score | Диагнозы (RU / EN) |
|----|-----|---------------------------|-------|---------------------|
| ivanova | Иванова А.М. | Кардиология A1 / Cardiology A1 | 34 | Гипертония, Диабет II / Hypertension, Diabetes II |
| omarov | Омаров Р.Б. | Пульмонология B3 / Pulmonology B3 | 51 | Пневмония, Аритмия / Pneumonia, Arrhythmia |
| nurlanov | Нурланов К.М. | Кардиология C2 / Cardiology C2 | 62 | ИБС, Гипертония III / CAD, Hypertension III |
| satybaldiev | Сатыбалдиев А.Р. | Терапия D1 / Therapy D1 | 71 | Бронхит / Bronchitis |
| bekmuratov | Бекмуратов Т.Д. | Пульмонология B1 / Pulmonology B1 | 58 | ХОБЛ / COPD |
| kadyrova | Кадырова Е.В. | Хирургия E2 / Surgery E2 | 87 | Послеоп. период / Post-op period |

У каждого: `diag/diag_en`, `current/current_en`, `hist/hist_en`, `meds/meds_en`, `allergy/allergy_en`, `insight/insight_en`, `ward/ward_en`. Демо: `gender:"Ж"/"М"` + `gender_en:"F"/"M"`, `blood:"O(I) Rh+"` + `blood_en:"O+"` (стандартная EN-нотация), height/weight как числа + `t("unit_cm")/t("unit_kg")`.

### ОРИТ-пациенты (3 + 2 пост-реанимация)
- Рахимов С.К. — bedT:"icu", bedN:1 — critical (ИМ, кардиогенный шок)
- Алиева Н.Р. — bedT:"icu", bedN:2 — warning (тяжёлая пневмония, ОРДС)
- Джураев А.Б. — bedT:"icu", bedN:4 — warning (перитонит)
- Муродов Б.Т. — bedT:"post", bedN:1 — stable (после ИМ)
- Хасанова Г.И. — bedT:"post", bedN:3 — stable (холецистэктомия)

Doctor field в данных без префикса; префикс «Др./Дкт./Dr.» добавляется через `t("dr_prefix")`.

### Lab Connect — 6 групп анализов в LAB_GROUPS
Clinical (4) / Biochem (8) / Vitamins (4) / Infectious (7) / Hormones (6) / Instrumental (9). См. полный список в коде или в `Product/LAB-CONNECT.md`.

---

## Дизайн-система

### Светлая тема — RAL 9016 Pure White
```css
--bg-light: #F1F0EA      /* RAL 9016, основной фон */
--card: #FFFFFF          /* белые карточки */
--sidebar-light: #E8E7E2 /* sidebar / elevated */
--text: #1a202c
--muted: #8a8275         /* тёплый, контрастирует с RAL 9016 */
--border: rgba(0,0,0,0.08)
```

### Тёмная тема — Deep Navy
```css
--bg-base: #080e18
--bg-card: #111e2e
--bg-elevated: #162437
--text-primary: #f0f4f8
```

### Бренд / акцент (одинаков в обеих темах)
```css
--accent: #4AA391       /* основной teal */
--accent-bright: #5ab8a1
--accent-dim: #3d8a79
--ok/success: #10b981
--warn/warning: #f59e0b
--danger: #ef4444
--info: #2563eb
```

### Типографика
- UI: Inter (400, 500, 600, 700, 800)
- Числовые значения: JetBrains Mono (`var(--font-data)`) с `tabular-nums`
- Заголовки: 800 weight
- Body: 14px / 0.88rem
- Muted labels: uppercase, letter-spacing, 0.72rem

### Градиенты
- **Основной:** `linear-gradient(135deg, #1A4A3E 0%, #4AA391 100%)` — применяется к `.cmd-bar`, `.stat-card`, `.time-saved-banner`, `.lab-ai-title`, `.ai-badge` (light theme)
- **Критический:** `linear-gradient(135deg, #8B2020, #C53030)` — `.cmd-stat.cmd-danger`
- **Hero:** `linear-gradient(135deg, #060d18, #2d6b5e 50%, #4AA391)` (для login или hero-блоков)
- Направление: всегда 135deg, тёмный → светлый
- Текст на градиентах: белый, подписи `rgba(255,255,255,0.8)`

### Палитра Avris Score
| Диапазон | Статус | Цвет |
|----------|--------|------|
| 0–39 | Критический | `#EF4444` |
| 40–59 | Наблюдение | `#F59E0B` |
| 60–79 | Умеренный | `#3B82F6` |
| 80–100 | Стабильный | `#10B981` |

### Мобильные breakpoints
| Breakpoint | Что меняется |
|------------|--------------|
| `≥1024px` | Sidebar постоянно открыт, content-area сдвинут |
| `≤1023px` | Sidebar drawer; consult-split → column |
| `≤768px` | Stats bar 2-col, lab-order-btn full-width, consult-tabs горизонтальный скролл, settings tabs горизонтальные, labs-table в скролл-контейнере |
| `≤480px` | Cmd-bar grid 1fr+1fr с danger на всю строку; stat-row 3 col компактные с ellipsis; nr-grid 1 col; icu-vg 2 col; filter-bar grid с full-width reset |

### Компоненты
`.btn` (primary/ghost/danger/sm), `.badge` (safe/warn/danger/info), `.card`, `.tag`, `.score-pill`, `.avatar`, `.modal`, `.toast` (типизированный ok/err/info с SVG иконкой), `.confirm-ov`, `.notif-panel`, `.sidebar`, `.topbar`, `.lab-modal-card`, `.pm-card`, `.nr-modal-card`.

---

## i18n

Полная поддержка 3 языков:
- **RU** — русский (default)
- **TJ** — тоҷикӣ (таджикский)
- **EN** — английский

**~200 ключей** в `TR` объекте. Атрибуты: `data-i18n` (textContent), `data-phk` (placeholder), `data-i18n-o` (option text), `data-i18n-val` (input value), `data-i18n-title` (title attribute).

`applyLang()` проходит по всем атрибутам, обновляет `recBadge` и перерисовывает все динамические экраны. EN-варианты данных пациентов выбираются в render-функциях по `lang === "en"`.

Дополнительные структуры с локализацией:
- `EV_TEMPLATES` / `EV_SOAP` — Evidence Link transcript для трёх языков
- `TR_LINES` — typewriter-симуляция для трёх языков
- `LAB_GROUPS` — структура с TR-ключами для 38 анализов
- `recBadgeMap` — «Whisper AI · Распознавание / Шинохт / Recognition»

---

## Доступность (a11y)

- `role="tablist"` + `role="tab"` + `aria-selected` на `.consult-tabs`, `.set-nav`, `.hist-pills`
- `aria-hidden="true"` на всех декоративных SVG (~30 мест)
- `aria-label` на icon-btn (close, theme, notifications, menu, FAB)
- `:focus-visible` outline через `var(--accent)` с `outline-offset: 2px`

---

## Что не работает (нужно реализовать для продакшена)

| Функция | Текущее состояние | Нужно |
|---------|-----------------|-------|
| Голосовая запись | Симуляция typewriter (TR_LINES) | OpenAI Whisper API |
| SOAP из транскрипта | Захардкоженный Evidence Link demo | Claude Sonnet (generate SOAP) |
| Аутентификация | Любые данные принимаются | Реальный бэкенд |
| Персистентность данных | In-memory, сброс при reload | localStorage → БД |
| Реальные пациенты | 6 захардкоженных | CRUD API |
| Realtime ОРИТ | Статичные цифры | WebSocket / polling |
| Lab Connect QR | SVG-генерация фейкового QR | Реальный QR с уникальным token + портал лаборатории |
| AI-комментарий к анализам | Захардкоженный текст | Claude Sonnet через API |

---

## Контекст для работы

- Всегда общаться на **русском языке**
- При добавлении кода — сохранять стиль (CSS-переменные, минифицированный JS не обязателен, но структуру `var $`/`esc`/IIFE соблюдать)
- STT = OpenAI Whisper, LLM = Claude Sonnet — фиксированный выбор стека
- Никаких эмодзи в продакшен-UI — только inline SVG (есть SVG_WARN, SVG_BOLT, SVG_CHECK, SVG_ALERT константы в JS)
- Любые новые data-driven строки — через TR-ключи (для RU/TJ/EN)
- Любые новые табы/pills — с `role="tab"` + `aria-selected` обработчиком в JS
- Декоративные SVG — с `aria-hidden="true"`
- Mobile-first для новых компонентов: проверяй на 480px
- Пользователь — основатель / разработчик проекта
