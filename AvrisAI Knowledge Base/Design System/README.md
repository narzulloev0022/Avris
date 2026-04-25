# Дизайн-система Avris AI

Вся дизайн-система реализована в `index.html` через CSS-переменные (`:root` и `body[data-theme=dark]`).

---

## Цвета

### Токены (light mode)

```css
--accent: #2aa99e         /* основной бренд-цвет — teal */
--accent-rgb: 42,169,158  /* для rgba() */
--accent-l: #38bdb1       /* hover-состояние */

--ok: #22c55e             /* зелёный — stable / success */
--warn: #f59e0b           /* янтарный — warning / watch */
--danger: #ef4444         /* красный — critical / error */

--bg: #f0f8f7             /* фон страницы */
--bg2: #f8fefd            /* вторичный фон */
--card: #fff              /* фон карточек */
--card2: #f2fcfb          /* вторичный фон карточек */

--text: #0f1f1e           /* основной текст */
--muted: #5e7876          /* приглушённый текст, лейблы */
--border: #d9ecea         /* границы */

--ghost: rgba(42,169,158,.07)     /* hover-фон элементов */
--tag-bg: rgba(42,169,158,.11)    /* фон тегов */
--tag-c: #1a8078                  /* текст тегов */

--safe-bg: rgba(34,197,94,.11)    /* фон safe-badge */
--warn-bg: rgba(245,158,11,.14)   /* фон warn-badge */
--danger-bg: rgba(239,68,68,.11)  /* фон danger-badge */

--hero: linear-gradient(135deg,#0d4f4a,#0d9488 55%,#2dd4bf)
```

### Токены (dark mode) — `body[data-theme=dark]`

```css
--bg: #0a0f1a
--bg2: #0f1629
--card: #141b2d
--card2: #1a2340
--text: #e8ecf4
--muted: #8892a8
--border: #1e293b
--hero: linear-gradient(135deg,#0a1628,#152040 50%,#1e3a6e)
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

**Числовые значения:** `font-variant-numeric: tabular-nums` — для часов, таймеров, витальных показателей

---

## Пространство и скруглення

```css
--r: 12px    /* карточки, кнопки */
--r2: 16px   /* крупные карточки, модалки */
--r3: 8px    /* мелкие элементы: badge, tag, icon-btn */

--sidebar: 260px
--topbar: 64px
```

### Тени
```css
--sh: 0 2px 8px rgba(0,0,0,.06)    /* лёгкая */
--sh2: 0 4px 16px rgba(0,0,0,.08)  /* средняя */
```

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

- Высота: `42px` (default), `34px` (.sm)
- Активное состояние: `transform: scale(.97)`
- Disabled: `opacity: .5`

### Badges

```html
<span class="badge safe">Стаб.</span>
<span class="badge warn">Набл.</span>
<span class="badge danger">Крит.</span>
<span class="badge info">6</span>
```

### Score Pill

```html
<div class="score-pill safe">87 Стаб.</div>
<div class="score-pill warn">51 Набл.</div>
<div class="score-pill danger">34 Крит.</div>
```

### Avatar

```html
<div class="avatar">ДА</div>
```
Градиент `--accent → --accent-l`, 40×40px, border-radius 50%.

### Card

```html
<div class="card">
  <div class="card-h"><h3>Заголовок</h3><span class="badge info">6</span></div>
  <!-- контент -->
</div>
```

### Tag

```html
<span class="tag">Гипертония</span>
<span class="tag danger">Пенициллин</span>
```

### Toast

Программный вызов: `toast("✅ Сохранено", 3000)`

Появляется снизу по центру, исчезает через delay мс.

### Confirm Dialog

```js
confirm2("Заголовок", "Текст").then(ok => { if (ok) { ... } })
```

### Modal

```js
openModal(patientId)  // открыть модалку пациента
closeModal()
```

---

## Анимации

```css
@keyframes fadeIn   — появление экранов (opacity + translateY)
@keyframes blink    — REC-индикатор
@keyframes rip      — пульсация микрофона во время записи
@keyframes wv       — waveform во время записи
@keyframes dp       — пульсация статус-точки (ОРИТ warning/critical)
```

---

## Responsive

- **≥1024px:** sidebar фиксирован слева, двухколоночный layout для consultation и dashboard
- **<1024px:** sidebar скрыт, открывается по гамбургер-кнопке с overlay
- **≤640px:** SOAP-форма в одну колонку, hero уменьшен

---

## Связанные разделы

- [[../Product/README|Продукт]] — какие экраны используют эти компоненты
- [[../Development/README|Разработка]] — как токены реализованы в коде
