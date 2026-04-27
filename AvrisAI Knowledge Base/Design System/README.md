# Дизайн-система Avris AI — Deep Navy

Dark-first дизайн-система, реализованная в `index.html` через CSS-переменные (`:root`). Обновлена в Session 1-2 (апрель 2026).

---

## Палитра

### Фоны

| Токен | Значение | Назначение |
|-------|----------|------------|
| `--bg-base` | `#080e18` | Фон страницы |
| `--bg-surface` | `#0d1520` | Поверхности (sidebar, panels) |
| `--bg-card` | `#111e2e` | Карточки |
| `--bg-elevated` | `#162437` | Hover-состояния карточек |
| `--bg-input` | `#0d1a28` | Поля ввода |

### Акцент (Teal)

| Токен | Значение | Назначение |
|-------|----------|------------|
| `--accent` | `#0d9488` | Основной бренд-цвет |
| `--accent-bright` | `#14b8a6` | Hover-состояние |
| `--accent-dim` | `#0f766e` | Приглушённый акцент |
| `--accent-glow` | `rgba(13,148,136,0.15)` | Свечение, фон кнопок |

### Текст

| Токен | Значение | Назначение |
|-------|----------|------------|
| `--text-primary` | `#f0f4f8` | Основной текст |
| `--text-secondary` | `#94a3b8` | Вторичный текст |
| `--text-muted` | `#4a5568` | Приглушённый / лейблы |
| `--text-accent` | `#5eead4` | Акцентный текст (теги, ссылки) |

### Границы

| Токен | Значение | Назначение |
|-------|----------|------------|
| `--border` | `rgba(255,255,255,0.06)` | Обычная граница |
| `--border-hover` | `rgba(255,255,255,0.12)` | Hover-граница |

### Статусы

| Токен | Значение | Назначение |
|-------|----------|------------|
| `--danger` | `#ef4444` | Critical / ошибка |
| `--warning` | `#f59e0b` | Warning / наблюдение |
| `--success` | `#10b981` | Stable / успех |
| `--info` | `#2563eb` | Информационный |

---

## Legacy-алиасы

Для обратной совместимости с существующими CSS-правилами:

```css
--brand: #0d9488          /* = --accent */
--accent-l: #14b8a6       /* = --accent-bright */
--ok: #10b981             /* = --success */
--warn: #f59e0b           /* = --warning */
--bg: #080e18             /* = --bg-base */
--bg2: #0d1520            /* = --bg-surface */
--card: #111e2e           /* = --bg-card */
--card2: #162437          /* = --bg-elevated */
--text: #f0f4f8           /* = --text-primary */
--muted: #94a3b8          /* = --text-secondary */
--ghost: rgba(13,148,136,.12)
--tag-bg: rgba(13,148,136,.15)
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

## Подготовлено (CSS готов, JS не подключён)

- **Skeleton loading:** класс `.skeleton` с `skeletonPulse` для SOAP-текстарей
- **Word traceability:** `<span class="word" data-ts>` + `.highlight` для транскрипт→SOAP связи

---

## Связанные разделы

- [[../Product/README|Продукт]] — какие экраны используют эти компоненты
- [[../Development/README|Разработка]] — как токены реализованы в коде
