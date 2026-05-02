# Avris AI

> **Voice-Powered Medical Documentation Platform**
> Built by **Hyperion Labs** for clinicians in Central Asia.

Avris AI lets a doctor narrate an exam, a night-round, or a lab referral — and within seconds gets back a structured SOAP note, a fillable consultation record, or a printable lab order. The interface is built mobile-first for hospital tablets and phones, supports Russian / Tajik / English natively, and ships with a fully working **Demo Mode** that runs without a backend.

---

## ✨ Features

| Domain | What it does |
|---|---|
| **Voice SOAP** | One-tap recording → Whisper transcript → Claude Sonnet generates S/O/A/P with Evidence Link highlighting |
| **Voice Night Round** | Doctor narrates "Палата A1, Иванова, пульс 78…", AI auto-extracts ward, patient, vitals, status, notes — ward gets ✓ Осмотрен |
| **Lab Connect** | QR-based referral with 38 tests / 6 groups; lab-tech portal at `/lab.html` enters results without auth; AI commentary on receipt |
| **ICU Monitor** | 5 critical-care patients with live vitals, alert pills, "Вызвать врача" CTA; collapsible Post-ICU on mobile |
| **Patient CRUD** | 6 demo patients seeded per doctor; create / edit / soft-delete from dashboard |
| **PDF Export** | Reportlab-rendered consultation and lab-order PDFs with Avris branding |
| **OAuth + Email** | Google, Mail.ru, plus email/password with 6-digit verification codes (Resend) |
| **3 themes** | Light, Dark, **System** (auto via `prefers-color-scheme`) |
| **3 languages** | RU / TJ / EN with full coverage of UI, errors, AI prompts |
| **Demo Mode** | Frontend works fully offline — typewriter STT, mock SOAP, local CRUD; orange `DEMO` badge in topbar |

---

## 🛠 Tech Stack

| Layer | Stack |
|---|---|
| **Frontend** | Single-file SPA (HTML + CSS + vanilla JS, IIFE strict mode), Inter + JetBrains Mono via Google Fonts, ~30 inline SVG icons |
| **Backend** | FastAPI · SQLAlchemy · Pydantic v2 · python-jose (JWT) · passlib + bcrypt · python-multipart |
| **AI** | OpenAI Whisper (STT) · Anthropic Claude Sonnet (SOAP / lab commentary) |
| **Email** | Resend (verification codes, password reset) |
| **PDF** | reportlab (pure Python, auto-discovers system Cyrillic TTF) |
| **Database** | SQLite (dev) → PostgreSQL (prod) |
| **OAuth** | Google · Mail.ru (Apple Sign In stub — coming soon) |
| **Testing** | Puppeteer headless screenshot + flow scripts |
| **Lab portal** | Standalone `lab.html` served at `/lab` |

---

## 🚀 Getting Started

### Demo (frontend only, no Python required)

```bash
cd Avris
python3 -m http.server 8080
open http://localhost:8080/index.html
```

The page auto-detects that no backend is running and enters Demo Mode. The orange `DEMO` badge appears in the top-right.

### Full stack (backend + frontend)

```bash
cd Avris/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in real keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, RESEND_API_KEY, GOOGLE_*, MAILRU_*)
python main.py
```

The backend serves both the API and the frontend at **http://localhost:8000/**. The lab tech portal is at **http://localhost:8000/lab**.

### First registration

1. Click **Зарегистрироваться** on the login screen.
2. Enter email + password (no name needed at this step).
3. Check your inbox (or backend stdout if `RESEND_API_KEY` is empty) for the 6-digit code.
4. Enter the code → you'll land on the **Заполните профиль врача** screen.
5. Required fields: Фамилия, Имя, Специальность. Click **Сохранить профиль** → you're in.

---

## 📁 Project Structure

```
Avris/
├── index.html                  # Single-page frontend (SPA, ~3000 lines)
├── lab.html                    # Standalone lab-tech portal
├── README.md                   # This file
├── .gitignore
└── backend/
    ├── main.py                 # FastAPI app, mounts all routers, serves SPA + /lab
    ├── database.py             # SQLAlchemy engine, Base, get_db()
    ├── models.py               # User, Patient, Consultation, LabOrder, NightRound
    ├── schemas.py              # Pydantic request/response models
    ├── auth.py                 # JWT, register/verify-email/login, OAuth, profile, avatar
    ├── patients.py             # CRUD + 6-patient demo seed
    ├── consultations.py        # SOAP CRUD + PDF export
    ├── lab_orders.py           # Order CRUD + public lab-tech endpoints + PDF
    ├── night_rounds.py         # Voice round persistence
    ├── stt.py                  # Whisper proxy
    ├── llm.py                  # Claude proxy (SOAP + lab commentary)
    ├── pdf_export.py           # reportlab renderers
    ├── email_service.py        # Resend integration
    ├── requirements.txt
    └── .env.example
```

---

## 🗺 Roadmap

| Status | Milestone |
|---|---|
| ✅ Done | Frontend SPA · 7 screens · 3 themes · 3 languages · DEMO mode |
| ✅ Done | Backend auth (register / verify / login / forgot / profile / avatar) |
| ✅ Done | Patient CRUD · Consultation CRUD · Lab Orders + portal |
| ✅ Done | Night-round voice workflow with AI parser |
| ✅ Done | PDF export (consultations + lab orders) |
| ✅ Done | OAuth (Google · Mail.ru) |
| 🔜 Next | Real Whisper API integration (currently 503 without key) |
| 🔜 Next | Real Claude SOAP integration (currently 503 without key) |
| 🔜 Next | Apple Sign In (waiting on Apple Developer enrollment) |
| 🔜 Next | Migrate to PostgreSQL · Alembic migrations |
| 🔜 Next | S3 / object storage for avatars (currently base64 in DB) |
| 🔜 Next | Realtime ICU updates (WebSocket) |
| 🔜 Next | Native iOS / Android wrappers |

---

## 📜 License

**Proprietary · Hyperion Labs © 2026.** All rights reserved.

Avris AI is closed-source pilot software for licensed clinical partners. Contact narzulloev0022@mail.ru for licensing or pilot inquiries.
