import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./avris.db")

# Railway/Heroku-style DATABASE_URL still uses the legacy "postgres://" scheme,
# but SQLAlchemy 2.x dropped that alias and only accepts "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    import models  # noqa: F401 — register models on Base
    Base.metadata.create_all(bind=engine)
    # Cross-DB lightweight column adds for the patients table — covers prod
    # Postgres where create_all() won't add new columns to an existing table.
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    # Согласия появились позже остальных таблиц; create_all их создаст,
    # а идемпотентность держится на индексе по (doctor_id, client_id).
    if "consultations" in insp.get_table_names():
        existing_c = {c["name"] for c in insp.get_columns("consultations")}
        with engine.begin() as conn:
            if "client_id" not in existing_c:
                conn.execute(text("ALTER TABLE consultations ADD COLUMN client_id VARCHAR(64)"))
            # Индекс создаётся отдельно от колонки: база, где колонка уже
            # появилась, а индекс — нет, осталась бы без защиты от дубля
            # осмотра, и заметить это было бы нечем.
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_consultations_doctor_client "
                "ON consultations (doctor_id, client_id)"
            ))
            # Записи, сделанные до появления черновиков, врач подтверждал
            # явным нажатием — они confirmed, а не draft.
            if "status" not in existing_c:
                conn.execute(text(
                    "ALTER TABLE consultations ADD COLUMN status VARCHAR(16) "
                    "NOT NULL DEFAULT 'confirmed'"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_consultations_status "
                    "ON consultations (status)"
                ))
            if "confirmed_at" not in existing_c:
                conn.execute(text("ALTER TABLE consultations ADD COLUMN confirmed_at DATETIME"))
                # Время подписи у прошлых записей неизвестно; берём момент
                # создания — он к истине ближе всего и не выдумывает данных.
                conn.execute(text(
                    "UPDATE consultations SET confirmed_at = created_at "
                    "WHERE confirmed_at IS NULL AND status = 'confirmed'"
                ))
            if "updated_at" not in existing_c:
                conn.execute(text("ALTER TABLE consultations ADD COLUMN updated_at DATETIME"))
    if "consents" in insp.get_table_names():
        existing_cs = {i["name"] for i in insp.get_indexes("consents")}
        if "ix_consents_doctor_client" not in existing_cs:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_consents_doctor_client "
                    "ON consents (doctor_id, client_id)"
                ))
    if "waitlist" in insp.get_table_names():
        existing_wl = {c["name"] for c in insp.get_columns("waitlist")}
        with engine.begin() as conn:
            if "full_name" not in existing_wl:
                conn.execute(text("ALTER TABLE waitlist ADD COLUMN full_name VARCHAR(120)"))
            if "phone" not in existing_wl:
                conn.execute(text("ALTER TABLE waitlist ADD COLUMN phone VARCHAR(32)"))
            if "plan" not in existing_wl:
                conn.execute(text("ALTER TABLE waitlist ADD COLUMN plan VARCHAR(16)"))
    if "health_alerts" in insp.get_table_names():
        existing_ha = {c["name"] for c in insp.get_columns("health_alerts")}
        with engine.begin() as conn:
            if "notified_at" not in existing_ha:
                conn.execute(text("ALTER TABLE health_alerts ADD COLUMN notified_at TIMESTAMP"))
    if "patients" in insp.get_table_names():
        existing = {c["name"] for c in insp.get_columns("patients")}
        with engine.begin() as conn:
            if "department" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN department VARCHAR"))
            if "status" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN status VARCHAR"))
            if "patient_type" not in existing:
                # Backfill existing rows as outpatient (safe default — no ward
                # required, no ICU/round side-effects).
                conn.execute(text("ALTER TABLE patients ADD COLUMN patient_type VARCHAR NOT NULL DEFAULT 'outpatient'"))
            # Медкарта-фундамент: ДР, № истории болезни, снимок поступления.
            if "date_of_birth" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN date_of_birth DATE"))
            if "record_number" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN record_number VARCHAR"))
            if "admission_date" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN admission_date TIMESTAMP"))
            if "admission_diagnosis" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN admission_diagnosis TEXT"))
            if "admission_status" not in existing:
                conn.execute(text("ALTER TABLE patients ADD COLUMN admission_status VARCHAR"))
    if "consultations" in insp.get_table_names():
        c_existing = {c["name"] for c in insp.get_columns("consultations")}
        with engine.begin() as conn:
            if "visit_type" not in c_existing:
                # Backfill: всё, что было до стационарного слоя — амбулаторный приём.
                conn.execute(text("ALTER TABLE consultations ADD COLUMN visit_type VARCHAR NOT NULL DEFAULT 'visit'"))
    # users table — accuracy counters added late, need cross-DB ALTER too.
    if "users" in insp.get_table_names():
        u_existing = {c["name"] for c in insp.get_columns("users")}
        with engine.begin() as conn:
            if "soap_accurate_count" not in u_existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN soap_accurate_count INTEGER NOT NULL DEFAULT 0"))
            if "soap_edited_count" not in u_existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN soap_edited_count INTEGER NOT NULL DEFAULT 0"))
            if "stt_consent" not in u_existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN stt_consent BOOLEAN NOT NULL DEFAULT FALSE"))
            if "hospital_phone" not in u_existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN hospital_phone VARCHAR(32)"))
            if "facility_type" not in u_existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN facility_type VARCHAR(16)"))
    # Заметка выросла с 300 до 1000 символов (интервью собирает структуру из
    # нескольких строк). В SQLite длина VARCHAR не проверяется, в Postgres —
    # проверяется, поэтому колонку надо расширить явно.
    if engine.dialect.name == "postgresql" and "patient_previsit_notes" in insp.get_table_names():
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE patient_previsit_notes ALTER COLUMN note_text TYPE VARCHAR(1000)"))
    # patient_accounts — consent_version added after the table shipped.
    if "patient_accounts" in insp.get_table_names():
        pa_existing = {c["name"] for c in insp.get_columns("patient_accounts")}
        with engine.begin() as conn:
            if "consent_version" not in pa_existing:
                conn.execute(text("ALTER TABLE patient_accounts ADD COLUMN consent_version VARCHAR(32)"))
            # Подписки: все существующие аккаунты — free (ядро бесплатно всегда).
            if "subscription_tier" not in pa_existing:
                conn.execute(text(
                    "ALTER TABLE patient_accounts ADD COLUMN subscription_tier "
                    "VARCHAR(16) NOT NULL DEFAULT 'free'"))
            if "subscription_expires_at" not in pa_existing:
                conn.execute(text("ALTER TABLE patient_accounts ADD COLUMN subscription_expires_at TIMESTAMP"))
            if "subscription_source" not in pa_existing:
                conn.execute(text("ALTER TABLE patient_accounts ADD COLUMN subscription_source VARCHAR(32)"))
    # patient_links — отзыв согласия гасит связь вместо удаления.
    if "patient_links" in insp.get_table_names():
        pl_existing = {c["name"] for c in insp.get_columns("patient_links")}
        with engine.begin() as conn:
            if "revoked_at" not in pl_existing:
                conn.execute(text("ALTER TABLE patient_links ADD COLUMN revoked_at TIMESTAMP"))
    # lab_orders — кэш платного AI-разбора для пациента.
    if "lab_orders" in insp.get_table_names():
        lo_existing = {c["name"] for c in insp.get_columns("lab_orders")}
        with engine.begin() as conn:
            if "patient_breakdown" not in lo_existing:
                conn.execute(text("ALTER TABLE lab_orders ADD COLUMN patient_breakdown TEXT"))
            if "patient_breakdown_key" not in lo_existing:
                conn.execute(text(
                    "ALTER TABLE lab_orders ADD COLUMN patient_breakdown_key VARCHAR(64)"))
    # visit_summaries — prescriptions block added after the table shipped.
    if "visit_summaries" in insp.get_table_names():
        vs_existing = {c["name"] for c in insp.get_columns("visit_summaries")}
        with engine.begin() as conn:
            if "prescriptions" not in vs_existing:
                conn.execute(text("ALTER TABLE visit_summaries ADD COLUMN prescriptions TEXT"))
    # Lightweight in-place migrations for sqlite (idempotent)
    if DATABASE_URL.startswith("sqlite"):
        from sqlalchemy import text, inspect
        insp = inspect(engine)
        if "users" in insp.get_table_names():
            existing = {c["name"] for c in insp.get_columns("users")}
            with engine.begin() as conn:
                added_columns = False
                if "is_admin" not in existing:
                    conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))
                    added_columns = True
                if "is_approved" not in existing:
                    conn.execute(text("ALTER TABLE users ADD COLUMN is_approved BOOLEAN NOT NULL DEFAULT 0"))
                    added_columns = True
                if "rejection_reason" not in existing:
                    conn.execute(text("ALTER TABLE users ADD COLUMN rejection_reason VARCHAR"))
                # Ensure there is always at least one admin
                admin_count = conn.execute(text("SELECT COUNT(*) FROM users WHERE is_admin=1")).scalar() or 0
                if admin_count == 0:
                    row = conn.execute(text("SELECT id FROM users WHERE is_verified=1 ORDER BY id ASC LIMIT 1")).first()
                    if row:
                        conn.execute(text("UPDATE users SET is_admin=1, is_approved=1 WHERE id=:i"), {"i": row[0]})
                # Backfill: if all verified users have is_approved=0, treat as a fresh migration and approve them
                # (so accounts created before the approval gate aren't locked out)
                verified_total = conn.execute(text("SELECT COUNT(*) FROM users WHERE is_verified=1")).scalar() or 0
                approved_total = conn.execute(text("SELECT COUNT(*) FROM users WHERE is_verified=1 AND is_approved=1")).scalar() or 0
                if verified_total > 0 and approved_total <= 1:
                    conn.execute(text("UPDATE users SET is_approved=1 WHERE is_verified=1"))
