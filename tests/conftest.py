"""Pytest bootstrap for backend API tests.

The backend uses flat imports (``from database import Base``) because its
runtime WORKDIR is ``backend/`` — so tests must put that directory on
``sys.path`` and point ``DATABASE_URL`` at a throwaway SQLite file BEFORE
importing any backend module (``database.py`` reads the env at import time;
``load_dotenv()`` does not override variables that are already set).
"""
import os
import sys
import tempfile

_BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="avris-test-"), "test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DB}")

import pytest  # noqa: E402


@pytest.fixture()
def db_session():
    """A fresh initialized schema + session per test."""
    from database import Base, engine, SessionLocal, init_db

    Base.metadata.drop_all(bind=engine)
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
