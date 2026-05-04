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
