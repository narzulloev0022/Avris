# Дизайн-система Avris AI — Deep Navy + RAL 9016

Двойная тема, реализованная в `index.html` через CSS-переменные (`:root` для тёмной + `body[data-theme="light"]` override). Тёмная — Deep Navy (Session 1-2). Светлая — обновлена с RAL 9001 на **RAL 9016 Pure White (`#F1F0EA`)** в мае 2026.

---

## Палитра

### Светлая тема — RAL 9016 Pure White (официальный фон)

`#F1F0EA` выбран как основной фон вместо ранее использованного RAL 9001 Cream (`#E9E0D2`). Более холодный нейтральный белый, лучше сочетается с teal-акцентом.

| Роль | Значение | CSS-переменные |
|------|----------|----------------|
| Фон страницы | `#F1F0EA` | `--bg`, `--bg-base`, `--bg-light` |
| Карточки / поверхности | `#FFFFFF` | `--card`, `--bg-card`, `--bg-surface`, `--bg-input` |
| Sidebar / elevated | `#E8E7E2` | `--bg-elevated`, `--card2`, `--sidebar-light` |
| Панели inner (transcript, waveform, SOAP внутри) | `#F8F5F0` | (используется напрямую) |
| Diagnosis pills | `#F0EBE3` | `.pat-ctx-diag` |
| Текст основной | `#1a202c` | `--text`, `--text-primary` |
| Текст вторичный | `#4a5568` | `--text-secondary` |
| Текст приглушённый (тёплый, контрастен на `#F1F0EA`) | `#8a8275` | `--text-muted`, `--muted` |
| Граница | `rgba(0,0,0,0.08)` | `--border` |
| Граница hover | `rgba(0,0,0,0.12)` | `--border-hover` |
| Mobile «Обновлено» override | `#5C5C5C` | (специально под cream/white) |

### Тёмная тема — Deep Navy

| Токен | Значение | Назначение |
|-------|----------|------------|
| `--bg-base` | `#080e18` | Фон страницы |
| `--bg-surface` | `#0d1520` | Поверхности (sidebar, panels) |
| `--bg-card` | `#111e2e` | Карточки |
| `--bg-elevated` | `#162437` | Hover-состояния карточек |
| `--bg-input` | `#0d1a28` | Поля ввода |
| `--text-primary` | `#f0f4f8` | Основной текст |
| `--text-secondary` | `#94a3b8` | Вторичный текст |
| `--text-muted` | `#4a5568` | Приглушённый / лейблы |
| `--border` | `rgba(255,255,255,0.06)` | Обычная граница |
| `--border-hover` | `rgba(255,255,255,0.12)` | Hover-граница |

### Акцент (Teal) — единый в обеих темах

| Токен | Значение | Назначение |
|-------|----------|------------|
| `--accent` | `#4AA391` | Основной бренд-цвет |
| `--accent-bright` | `#5ab8a1` | Hover-состояние |
| `--accent-dim` | `#3d8a79` | Приглушённый акцент |
| `--accent-glow` | `rgba(74,163,145,0.15)` (dark) / `0.10` (light) | Свечение, фон кнопок |
| `--text-accent` | `#5eead4` (dark) / `#4AA391` (light) | Акцентный текст, теги |

### Статусы — единые в обеих темах

| Токен | Значение | Назначение |
|-------|----------|------------|
| `--danger` | `#ef4444` | Critical / ошибка |
| `--warning` | `#f59e0b` | Warning / наблюдение |
| `--success` / `--ok` | `#10b981` | Stable / успех |
| `--info` | `#2563eb` | Информационный |
| `--danger-bg` | `rgba(239,68,68,0.10)` (dark) / `0.08` (light) | Подложка |
| `--warning-bg` | `rgba(245,158,11,0.10)` (dark) / `0.08` (light) | Подложка |
| `--success-bg` | `rgba(16,185,129,0.10)` (dark) / `0.08` (light) | Подложка |

### Legacy-алиасы

```css
--brand: #4AA391          /* = --accent */
--accent-l: #5ab8a1       /* = --accent-bright */
--ok: #10b981             /* = --success */
--warn: #f59e0b           /* = --warning */
--ghost: rgba(74,163,145,.12)
--tag-bg: rgba(74,163,145,.15)
--tag-c: #5eead4
--safe-bg: rgba(16,185,129,.1)
--warn-bg: rgba(245,158,11,.1)
```

---

## Типографика

**Шрифт:** Inter (Google Fonts), веса 400 / 500 / 600 / 700 / 800

```css
--font: Inter, system-ui, sans-serif
```

### Шкала размеров

| Роль | Размер | Вес |
|------|--------|-----|
| Hero-заголовок | 1.8–2.6rem | 800 |
| Заголовок экрана | 1.05rem | 700 |
| Card-заголовок | 0.95rem | 700 |
| Основной текст | 0.88rem | 400 |
| Мелкий текст / детали | 0.82rem | 400–600 |
| Лейблы форм | 0.75rem | 600 / uppercase |
| Мета / время / подписи | 0.7–0.72rem | 400 |

**Числовые значения:** `font-variant-numeric: tabular-nums`

---

## Радиусы

```css
--radius-card: 12px      /* карточки, кнопки */
--radius-input: 8px      /* поля ввода, мелкие элементы */
--radius-pill: 20px      /* pill-badge, score-pill */
```

Legacy-алиасы: `--r: 12px`, `--r2: 16px`, `--r3: 8px`

---

## Тени

```css
--shadow: 0 0 0 1px rgba(0,0,0,0.5), 0 8px 32px rgba(0,0,0,0.4)
--shadow-glow: 0 0 20px rgba(13,148,136,0.3)
```

---

## Переходы

```css
--transition: 150ms cubic-bezier(0.4, 0, 0.2, 1)
```

---

## Размеры layout

```css
--sidebar: 260px
--topbar: 56px
```

---

## Micro-interactions

### Анимации

```css
@keyframes screenIn     /* появление экрана: opacity 0→1, translateY 12px→0, 300ms */
@keyframes critPulse    /* пульсация critical-стата: rgba danger фон, 1.5s infinite */
@keyframes recRing      /* пульсация кнопки записи: scale + shadow-glow, 1.5s infinite */
@keyframes skeletonPulse /* загрузка SOAP: opacity 0.6→1, 1.2s infinite */
@keyframes blink        /* REC-индикатор: opacity 1→0→1, 1s infinite */
@keyframes wv           /* waveform-бары при записи: scaleY 0.3→1, 0.6s infinite */
```

### Focus ring

```css
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
```

### Hover-состояния

- Карточки: `translateY(-2px)` + `--bg-elevated`
- Пациенты в списке: `--bg-elevated` + slide-in action button (`translateX(-8px → 0)`)
- Score bars: 6px → 8px height
- Кнопки: `scale(.97)` при active

### Toast-уведомления

Типизированные с цветными левыми бордерами:
- `.toast-ok` — `--success` border
- `.toast-err` — `--danger` border
- `.toast-info` — `--info` border

---

## Компоненты

### Кнопки

```html
<button class="btn primary">Сохранить</button>
<button class="btn ghost">Отмена</button>
<button class="btn danger">Удалить</button>
<button class="btn ghost sm">Мелкая</button>
<button class="icon-btn">☰</button>
```

### Badges

```html
<span class="badge safe">Стаб.</span>
<span class="badge warn">Набл.</span>
<span class="badge danger">Крит.</span>
<span class="badge info">6</span>
```

### Score Pill / Avatar / Card / Tag / Toast / Confirm / Modal

Компоненты без изменений — см. код `index.html`.

---

## Градиенты

### Основной градиент (stats bar, stat cards)
```css
background: linear-gradient(135deg, #1A4A3E 0%, #4AA391 100%);
```
Тёмный forest green → яркий teal. Всегда 135deg. Текст на градиентах — белый (`#fff`), подписи `rgba(255,255,255,0.8)`.

### Критический градиент (danger-карточка в stats bar)
```css
background: linear-gradient(135deg, #8B2020, #C53030);
```
На мобильных (480px) заменяется на `rgba(239,68,68,0.2)` с бордером `rgba(239,68,68,0.5)`.

### Палитра Avris Score

| Диапазон | Статус | Цвет | CSS-переменная |
|----------|--------|------|----------------|
| 0–39 | Критический | `#EF4444` | `--danger` |
| 40–59 | Наблюдение | `#F59E0B` | `--warning` |
| 60–79 | Умеренный | `#3B82F6` | `--info` |
| 80–100 | Стабильный | `#10B981` | `--success` |

---

## Мобильная адаптация — все breakpoints

| Breakpoint | Что меняется |
|------------|--------------|
| `≥1024px` | `.content-area` отступ слева под фиксированный sidebar; `.cg` дашборда → `1.4fr .6fr` |
| `≤1023px` | Sidebar становится drawer (overlay); `.consult-split` `flex-direction: column`, `height: auto` |
| `≤768px` | `.cmd-bar` 2-col grid + danger на всю ширину сверху, `.cmd-div` скрыты; consult-tabs горизонтальный скролл; lab-order-btn full-width; settings tabs горизонтальные с overflow-x:auto; labs-table в `.labs-table-wrap` со скроллом и `min-width: 380px`; pm-vitals → column |
| `≤640px` | Lab-modal grid из 2 колонок → 1 колонка; modal padding 18px |
| `≤480px` | `.cmd-bar` grid 1fr+1fr с danger `grid-column:1/-1` сверху, gradient cards; `.stat-row` 3 col компактные с ellipsis на label/sub; `.nr-grid` 1 col; `.icu-vg` 6 vital-боксов в **2 колонки** (3 ряда по 2); `.filter-bar` grid 2 col + reset full-width; settings `.set-nav` flex-wrap:nowrap + overflow-x; `.hist-entry:hover` margin -12px (под padding 14px) |

### Cmd-bar mobile spec (480px)
- Grid `1fr 1fr; gap: 10px`, без общего фона
- «КРИТИЧЕСКИХ» — `grid-column: 1/-1; order: -1`, красный градиент `linear-gradient(135deg, #8B2020, #C53030)`, без анимации pulse
- Остальные cmd-stat — зелёный градиент `linear-gradient(135deg, #1A4A3E, #4AA391)`, `border-radius: 12px`, `padding: 14px 16px`
- «Обновлено» — `grid-column: 1/-1`, центрированный, на light-теме `color: #5C5C5C; font-weight: 500`

### Stat cards mobile (480px)
- Остаются 3 колонки `repeat(3, 1fr)` с компактным `padding: 10px 6px`
- Label и sub получают `text-overflow: ellipsis; white-space: nowrap` чтобы не переносились на 145px ширине
- Иконка 36×36 в круге `rgba(255,255,255,0.15)`

### ICU mobile (480px)
- Карточки ICU-c в 1 колонку
- Vital-сетка `.icu-vg` — 2 колонки вместо 3, `font-size: 1.05rem` для значений (числа типа 118/72 для АД помещаются)

### Lab-modal mobile (640px и ниже)
- `.lab-group-tests` сетка с 2 колонок → 1 колонка
- `.lab-modal-card` padding 18px, width 96%

### Порядок медиа-запросов
Размещаются ПОСЛЕ базовых стилей компонентов (правила ниже выигрывают при равной специфичности):
1. `@media (min-width: 1024px)` — desktop sidebar
2. `@media (max-width: 1023px)` — tablet (consult-split column)
3. `@media (max-width: 768px)` — mobile primary
4. `@media (max-width: 640px)` — lab-modal column
5. `@media (max-width: 480px)` — small mobile (iPhone SE и уже)

---

## Подготовлено (CSS готов, JS не подключён)

- **Skeleton loading:** класс `.skeleton` с `skeletonPulse` для SOAP-текстарей
- **Word traceability:** `<span class="word" data-ts>` + `.highlight` для транскрипт→SOAP связи

---

## Связанные разделы

- [[../Product/README|Продукт]] — какие экраны используют эти компоненты
- [[../Development/README|Разработка]] — как токены реализованы в коде
