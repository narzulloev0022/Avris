"""Public waitlist endpoint for the marketing page (/waitlist).

No auth — anyone may join. Abuse guards: slowapi per-IP limits plus a
honeypot field (the form ships an invisible "website" input that humans
never fill; bots that do are silently accepted and dropped). Re-joining
with a known email is idempotent: the visitor still sees success, no new
row is written.

Export: GET /api/waitlist/export is owner-only, gated by the same
ADMIN_RESET_KEY env var as the destructive admin endpoints (503 when unset).
"""
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from database import get_db
from models import WaitlistEntry
from rate_limit import limiter

log = logging.getLogger("avris.waitlist")

router = APIRouter(prefix="/api/waitlist", tags=["waitlist"])

ROLES = {"doctor", "clinic", "investor"}


class WaitlistIn(BaseModel):
    email: EmailStr
    role: str = Field(default="doctor", max_length=16)
    lang: str = Field(default="ru", max_length=4)
    website: str = Field(default="", max_length=255)  # honeypot


@router.post("")
@limiter.limit("5/minute")
def join_waitlist(request: Request, payload: WaitlistIn, db: Session = Depends(get_db)):
    if payload.website:
        # Honeypot tripped — pretend success so bots learn nothing.
        return {"ok": True}
    email = payload.email.lower().strip()
    role = payload.role if payload.role in ROLES else "doctor"
    lang = payload.lang if payload.lang in ("ru", "en", "tj") else "ru"

    if db.query(WaitlistEntry).filter(WaitlistEntry.email == email).first():
        return {"ok": True, "already": True}

    db.add(WaitlistEntry(email=email, role=role, lang=lang))
    db.commit()
    # Log the domain only — full addresses stay out of the log stream.
    log.info("waitlist: +1 %s (@%s, %s)", role, email.split("@")[-1], lang)
    return {"ok": True}


@router.get("/export")
def export_waitlist(
    x_admin_reset_key: str = Header(default=""),
    db: Session = Depends(get_db),
):
    admin_key: Optional[str] = os.getenv("ADMIN_RESET_KEY")
    if not admin_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Export disabled: ADMIN_RESET_KEY not set")
    if x_admin_reset_key != admin_key:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bad admin key")
    rows = db.query(WaitlistEntry).order_by(WaitlistEntry.created_at.desc()).all()
    return {
        "total": len(rows),
        "entries": [
            {"email": r.email, "role": r.role, "lang": r.lang, "created_at": r.created_at.isoformat()}
            for r in rows
        ],
    }
