# Разработка

## Прогресс дизайна

| Этап | Статус | Что сделано |
|------|--------|-------------|
| Session 1: Design System + Login | ✅ Готово | Deep Navy палитра, dark-first CSS variables, Login redesign (centered form, AVRIS + teal dot) |
| Session 2: Dashboard + Consultation | ✅ Готово | Metrics bar, stat cards, patient list с hover-slide, Consultation 55/45 split-screen, recording surface, SOAP panel |
| Session 3: Cream theme + Mobile + i18n | ✅ Готово | RAL 9001 Cream light theme, mobile dashboard (480px), SVG icons, Settings tab redesign, unified Consultation light/dark, STT lang switcher (RU/TJ/EN), full RU/TJ/EN translation. См. [[Session3-Progress]] и [[i18n-Reference]] |

---

## Архитектура

### Текущее состояние

```
index.html (~750 строк)
├── <head>
│   ├── Google Fonts (Inter)
│   └── <style> — вся CSS (Deep Navy design system, CSS-переменные)
├── <body>
│   ├── .login-screen    — экран входа (Session 1 redesign)
│   ├── .app-shell       — основное приложение
│   │   ├── .sidebar     — фиксированная левая навигация
│   │   ├── .topbar      — шапка (sticky)
│   │   └── .main        — 6 screen-секций
│   │       ├── #dashboard     — Session 2 redesign
│   │       ├── #consultation  — Session 2 split-screen
│   │       ├── #nightRound    — 🔄 Session 3
│   │       ├── #history       — 🔄 Session 3
│   │       ├── #monitor       — 🔄 Session 3
│   │       └── #settings      — 🔄 Session 3
│   ├── .notif-panel     — панель уведомлений (drawer)
│   ├── #patModal        — модальное окно пациента (🔄 Session 3)
│   ├── .toast           — уведомления (typed: ok/err/info)
│   └── .confirm-ov      — диалог подтверждения
└── <script>             — весь JS (~80 строк, IIFE, strict mode)
```

### Принципы архитектуры JS
- Один IIFE — `(function(){ "use strict"; ... })()`
- Всё в памяти — нет localStorage, нет API-вызовов
- `var $ = function(id){ return document.getElementById(id) }` — шорткат
- `var esc = function(s){ ... }` — XSS-защита через textContent
- Данные — простые массивы объектов (`patients[]`, `icuPats[]` и т.д.)

---

## API-спецификации (планируемые)

### OpenAI Whisper — Транскрипция

**Endpoint:** `POST https://api.openai.com/v1/audio/transcriptions`

```json
{
  "file": "<audio blob>",
  "model": "whisper-1",
  "language": "ru",
  "response_format": "text"
}
```

**Интеграция в приложение:**
- Захватить аудио через `MediaRecorder API`
- Отправить chunk или полный файл после остановки записи
- Отобразить транскрипт в `#transcriptText`

**Поддерживаемые языки:** ru, tg (таджикский), uz (узбекский), en

### Claude Sonnet — Генерация SOAP

**Endpoint:** `POST https://api.anthropic.com/v1/messages`

**Промпт (черновик):**
```
Ты медицинский ассистент. На основе транскрипта голосового осмотра врача
составь структурированную SOAP-документацию на русском языке.

Транскрипт:
{transcript}

Верни JSON:
{
  "subjective": "...",
  "objective": "...",
  "assessment": "...",
  "plan": "..."
}
```

**Модель:** `claude-sonnet-4-6` (или актуальная версия)

---

## Структура данных (текущие модели)

### Patient

```js
{
  id: "ivanova",           // уникальный идентификатор
  name: "Иванова А.М.",    // полное имя
  ini: "ИА",               // инициалы для avatar
  ward: "Кардиология A1",  // отделение
  score: 34,               // Avris Score (0–100)
  diag: ["Гипертония", "Диабет II"],   // активные диагнозы
  current: ["Гипертонический криз"],   // текущее состояние
  hist: ["ИБС", "ХБП I"],             // анамнез
  allergy: ["Метформин"],             // аллергии
  meds: ["Амлодипин 10мг"],           // назначения
  insight: "Ночное давление >150...", // AI-инсайт
  demo: {
    age: 63, gender: "Ж",
    blood: "O(I) Rh+", height: "165см", weight: "78кг", bmi: "28.7"
  },
  vitals: {
    "АД":   [152,148,150,147,151,149,150],  // 7 дней
    "ЧСС":  [88,85,92,90,87,89,91],
    "T°C":  [37.5,37.4,37.3,37.4,37.5,37.2,37.3],
    "SpO₂": [94,92,93,90,94,95,93]
  }
}
```

### ICUPatient

```js
{
  name: "Рахимов С.К.",
  ini: "РС",
  bed: "ОРИТ-1",
  age: 72, gender: "М",
  diag: "Инфаркт миокарда, кардиогенный шок",
  status: "critical",  // "stable" | "warning" | "critical"
  doctor: "Др. Назаров",
  days: 3,
  vitals: { hr: 112, sys: 88, dia: 55, spo2: 89, resp: 24, temp: 37.8 },
  notes: "ИВЛ, вазопрессоры...",
  upd: "14:20"
}
```

### HistoryEntry

```js
{
  time: "08:40",
  patient: "Иванова А.М.",
  type: "soap",   // "soap" | "round"
  summary: "Запись экспортирована."
}
```

---

## Деплой

### Текущее состояние
- Статичный `index.html` — открывается прямо в браузере
- Никакого сервера не требуется

### Планируемый стек деплоя
*(TBD)*
- Frontend: Vercel / Netlify
- Backend: TBD (Node.js / Python / Go)
- БД: TBD (PostgreSQL / Supabase)
- Файлы аудио: S3-совместимое хранилище

---

## Среды

| Среда | URL | Статус |
|-------|-----|--------|
| Local | `file:///Users/shahzod/Avris/index.html` | ✅ Работает |
| Staging | TBD | ❌ |
| Production | TBD | ❌ |

---

## Ключевые функции в JS

| Функция | Что делает |
|---------|-----------|
| `init()` | Инициализация всего приложения |
| `applyLang()` | Перерисовка всех i18n-атрибутов |
| `buildPatList()` | Рендер списка пациентов |
| `applyFilters()` | Фильтрация списка по поиску/диагнозу/отделению |
| `updateDash()` | Пересчёт статистики дашборда |
| `openModal(id)` | Открыть модалку пациента по ID |
| `drawChart(canvas, data, height)` | Нарисовать vitals-чарт на canvas |
| `startRec()` / `stopRec()` | Управление записью |
| `startTypewriter()` | Симуляция транскрипта (удалить после Whisper) |
| `renderICU()` | Рендер ОРИТ-доски |
| `renderWards()` | Рендер сетки палат |
| `toast(msg, delay, type)` | Показать toast-уведомление (type: ok/err/info) |
| `initWave()` | Инициализация 16-bar waveform для записи |
| `confirm2(title, text)` | Promise-based confirm dialog |

---

## Что нужно сделать

### Фаза 1 — Голос
- [ ] `MediaRecorder API` — запись аудио в браузере
- [ ] Интеграция `OpenAI Whisper API`
- [ ] Стриминг транскрипта
- [ ] Заменить `startTypewriter()` на реальный Whisper

### Фаза 2 — AI
- [ ] Интеграция `Claude Sonnet API`
- [ ] Промпт для генерации SOAP
- [ ] Авто-заполнение SOAP-формы из транскрипта
- [ ] Промпт для генерации Avris Score (будущее)

### Фаза 3 — Бэкенд
- [ ] Аутентификация (JWT)
- [ ] API для пациентов (CRUD)
- [ ] Сохранение SOAP-записей в БД
- [ ] API для истории

### Фаза 4 — Продакшн
- [ ] Разбить `index.html` на модули (Vite)
- [ ] PWA + service worker
- [ ] WebSocket для ОРИТ-мониторинга
- [ ] Экспорт в PDF

---

## Связанные разделы

- [[../Product/README|Продукт]] — что именно реализуем
- [[../Design System/README|Дизайн-система]] — CSS-переменные и компоненты
- [[../Project Overview/README|Обзор проекта]] — технологический стек
