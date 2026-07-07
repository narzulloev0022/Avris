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
import re
import secrets
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

import email_service
from database import get_db
from models import WaitlistEntry
from rate_limit import limiter

log = logging.getLogger("avris.waitlist")

router = APIRouter(prefix="/api/waitlist", tags=["waitlist"])

ROLES = {"doctor", "clinic", "investor"}


PHONE_RE = re.compile(r"^\+?[\d\s\-()]{7,20}$")


class WaitlistIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=32)
    role: str = Field(default="doctor", max_length=16)
    lang: str = Field(default="ru", max_length=4)
    website: str = Field(default="", max_length=255)  # honeypot


@router.post("")
@limiter.limit("5/minute")
def join_waitlist(request: Request, payload: WaitlistIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if payload.website:
        # Honeypot tripped — pretend success so bots learn nothing.
        return {"ok": True}
    email = payload.email.lower().strip()
    full_name = " ".join(payload.full_name.split())[:120]
    phone = payload.phone.strip()
    if len(full_name) < 2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "full_name is too short")
    if not PHONE_RE.match(phone):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "phone looks invalid")
    role = payload.role if payload.role in ROLES else "doctor"
    lang = payload.lang if payload.lang in ("ru", "en", "tj") else "ru"

    # Deliberately indistinguishable from a fresh signup — the endpoint must
    # not double as an "is this email on the list?" oracle. A repeat signup
    # still refreshes the contact details.
    existing = db.query(WaitlistEntry).filter(WaitlistEntry.email == email).first()
    if existing:
        existing.full_name = full_name
        existing.phone = phone
        db.commit()
        return {"ok": True}

    db.add(WaitlistEntry(email=email, full_name=full_name, phone=phone, role=role, lang=lang))
    db.commit()
    # Log the domain only — full addresses stay out of the log stream.
    log.info("waitlist: +1 %s (@%s, %s)", role, email.split("@")[-1], lang)

    # Owner alert — best-effort, after the response, never blocks the signup.
    notify = os.getenv("WAITLIST_NOTIFY_EMAIL", "")
    if notify:
        background_tasks.add_task(email_service.send_waitlist_alert, notify, email, role, lang,
                                  full_name, phone)
    return {"ok": True}


@router.get("/export")
def export_waitlist(
    x_admin_reset_key: str = Header(default=""),
    db: Session = Depends(get_db),
):
    admin_key: Optional[str] = os.getenv("ADMIN_RESET_KEY")
    if not admin_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Export disabled: ADMIN_RESET_KEY not set")
    if not secrets.compare_digest(x_admin_reset_key or "", admin_key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bad admin key")
    rows = db.query(WaitlistEntry).order_by(WaitlistEntry.created_at.desc()).all()
    return {
        "total": len(rows),
        "entries": [
            {"email": r.email, "full_name": r.full_name or "", "phone": r.phone or "",
             "role": r.role, "lang": r.lang, "created_at": r.created_at.isoformat()}
            for r in rows
        ],
    }
