"""Seed ONE demo doctor for manually testing the patient QR-link loop live.

Idempotent. Writes into whatever DATABASE_URL points at — set it to the SAME DB
the running server uses so the doctor is visible to the API. Example:

    cd backend
    DATABASE_URL="sqlite:////abs/path/demo-live.db" .venv/bin/python seed_demo_doctor.py

Re-running just re-ensures verified+approved and prints a fresh access token you
can paste as `Authorization: Bearer <token>` to confirm patient links.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth import create_access_token, hash_password  # noqa: E402
from database import SessionLocal, init_db  # noqa: E402
from models import User  # noqa: E402

EMAIL = "demo.doctor@avris.local"
PASSWORD = "demo1234"
FULL_NAME = "Др. Демо Каримов"


def main() -> None:
    init_db()  # idempotent — creates the schema if the DB is fresh
    db = SessionLocal()
    try:
        doc = db.query(User).filter(User.email == EMAIL).first()
        if doc is None:
            doc = User(
                email=EMAIL,
                password_hash=hash_password(PASSWORD),
                full_name=FULL_NAME,
                is_verified=True,
                is_approved=True,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            state = "created"
        else:
            doc.is_verified = True
            doc.is_approved = True
            db.commit()
            state = "exists "
        token = create_access_token(doc.id)
        print(f"[{state}] doctor id={doc.id}  login: {EMAIL} / {PASSWORD}")
        print(f"access_token: {token}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
