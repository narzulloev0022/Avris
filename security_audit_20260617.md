# Security Audit — Avris AI · 2026-06-17

Аудит прода theavris.ai + кода (backend FastAPI, frontend SPA). Инструменты: bandit, safety, npm audit, semgrep, ручные проверки.

## Сводка
- **Критических: 0**
- **Высоких: 0** (в коде проекта; bandit High=0 на 4033 строках)
- **Средних: 3**
- **Низких: 6**

Базовая гигиена сильная: нет хардкод-секретов, bcrypt, rate-limiting везде, параметризованный SQL, аудио не хранится, ключи не логируются, HTTPS-редирект. Основной пробел — отсутствие security-заголовков.

---

## 🟠 Средние (исправить в ближайшее время)

### M1. Отсутствуют HTTP security-заголовки
Прод не отдаёт ни одного: нет `Content-Security-Policy`, `X-Frame-Options`, `Strict-Transport-Security` (HSTS), `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`.
**Риск:** clickjacking, MIME-sniffing, ослабленная защита от XSS. Для медданных — значимо.
**Фикс:** middleware в `backend/main.py` (добавить заголовки ко всем ответам) и/или правила в Cloudflare. HSTS — через Cloudflare.

### M2. JWT живёт 7 дней, без refresh
`backend/auth.py`: `ACCESS_TOKEN_EXPIRE_MINUTES = 60*24*7`. Долгоживущий токен без отзыва/refresh — украденный токен валиден неделю.
**Фикс:** сократить access-токен (15–30 мин) + refresh-токен, либо серверный allowlist/blacklist для отзыва. (SECURITY.md заявляет 30 мин — привести код в соответствие.)

### M3. Незапиненные зависимости
`safety`: 0 CVE в установленных, НО `reportlab`, `httpx`, `python-dotenv`, `slowapi` указаны без версий (`>=0`). В их диапазонах есть известные CVE (reportlab — 4, httpx — 1, python-dotenv — 1), которые могут подтянуться при пересборке.
**Фикс:** запинить версии в `backend/requirements.txt` (`==`) + Dependabot/renovate.

---

## 🟡 Низкие

- **L1.** `SECRET_KEY` fallback `"dev-secret-change-me"` (`auth.py:31`, `main.py:19`). В prod env задан (проверено), но добавить гард: падать при старте, если не задан в проде.
- **L2.** Error-логи STT/LLM пишут `r.text[:300]` тела ответа API (`stt.py:67`, `llm.py:100`) — может изредка содержать фрагменты PHI. Логировать только статус/код ошибки.
- **L3.** OTP-код пишется в лог при недоступном Resend (`email_service.py:34,44`). Не логировать коды.
- **L4.** `try/except/pass` ×5 (`auth.py`, `rate_limit.py`, bandit B110) — глушит ошибки, может скрывать сбои.
- **L5.** Фронт: 57 `innerHTML` против 36 `esc()` в `app.js`; 1 `document.write`. Нужен аудит, что ВСЕ динамические данные (имена/диагнозы пациентов) проходят через `esc()` — иначе риск stored XSS.
- **L6.** CORS-allowlist держит `localhost:8000/8080` и в проде (безвредно, но можно добавлять только в dev).

---

## ✅ Что уже защищено хорошо
- Нет хардкод-секретов — всё через `os.getenv` (bandit B105 — false-positives на URL-ах).
- Пароли: **bcrypt** (passlib CryptContext).
- **Rate limiting** на всех чувствительных эндпоинтах: auth 5–10/мин, STT 30/мин, LLM 60/мин, справочники 300/мин; ключ auth-aware.
- **SQL**: только статичные `text()` миграции + bind-параметры (`:i`), остальное — ORM. Инъекций нет.
- **CORS**: whitelist (не `*`), `allow_credentials=False`.
- **Аудио** не сохраняется на диск — память → Whisper, лимит размера.
- **HTTPS**: HTTP→HTTPS 301 (Cloudflare).
- **API-ключи** (Anthropic/OpenAI/Resend) не логируются.
- `.gitignore` закрывает `.env`, `*.env`, `client_secret*.json`, `*.db`, `__pycache__/`, `.venv/`.
- **npm audit: 0** уязвимостей; **safety: 0** CVE в установленных зависимостях.
- БД — PostgreSQL (данные персистентны), пароли хешируются.

---

## 🔧 Исправить немедленно
Нет «горящих» (критических/сломанных) пунктов. Самый ценный быстрый фикс — **M1 (security-заголовки)**: безопасно, не ломает функциональность, заметно поднимает защиту.

## 📋 Рекомендации на будущее
1. Security-заголовки (M1) — middleware + Cloudflare HSTS.
2. Запинить зависимости + Dependabot (M3).
3. JWT: короткий access + refresh (M2).
4. Гард `SECRET_KEY` при старте (L1).
5. PHI-safe логирование — не писать тела ответов/коды (L2, L3).
6. Фронт-аудит `innerHTML`/`esc()` (L5).
7. CI: `.github/workflows/security.yml` создан — bandit + safety + secret-scan + npm audit на каждый push/PR в main.
8. Периодически: semgrep, retire.js.

## Артефакты
- `security_report_bandit.txt` — полный отчёт bandit (по коду проекта, без .venv).
- `.github/workflows/security.yml` — авто-проверки в CI.
- `SECURITY.md` — политика безопасности.
