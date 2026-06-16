# Security Policy — Avris AI

## Защищённые данные
- Медицинские записи пациентов (SOAP)
- Аудио записи консультаций (транзитом, на диск не сохраняются)
- Персональные данные врачей
- Лабораторные результаты

## Меры защиты
- JWT токены (HS256). **Текущий срок: 7 дней** (план хардернинга: сократить до 30 мин + refresh-токен)
- Bcrypt хеширование паролей (passlib CryptContext)
- Rate limiting на все auth/STT/LLM эндпоинты (slowapi, auth-aware)
- OTP верификация email (Resend, 6 цифр, TTL ограничен)
- CORS whitelist (theavris.ai), `allow_credentials=False`
- PostgreSQL, параметризованные запросы (SQLAlchemy ORM + bind-параметры)
- HTTPS everywhere (Cloudflare, HTTP→HTTPS 301)
- Аудио не персистится: читается в память, форвардится в Whisper, лимит размера

## Что делать при обнаружении уязвимости
Email: security@theavris.ai
Ответ в течение 24 часов.

## Проверки безопасности
- bandit: еженедельно (`bandit -r backend/ -x backend/.venv -ll`)
- safety check: при каждом деплое (`safety check -r backend/requirements.txt`)
- npm audit: при каждом деплое (`npm audit --audit-level=moderate`)
- semgrep / retire.js: периодически

## Открытые рекомендации (см. security_audit_*.md)
- [ ] Добавить security-заголовки (CSP, X-Frame-Options, HSTS, X-Content-Type-Options, Referrer-Policy)
- [ ] Запинить версии зависимостей в `backend/requirements.txt`
- [ ] Сократить срок JWT и добавить refresh-токен
- [ ] Заменить fallback `SECRET_KEY="dev-secret-change-me"` на гард при старте (падать, если env не задан в prod)
- [ ] Аудит innerHTML на фронте — убедиться, что все динамические данные проходят через `esc()`
