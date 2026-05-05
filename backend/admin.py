import logging
import os
import secrets

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from database import Base, engine, init_db
import models  # noqa: F401 — register tables on Base.metadata

router = APIRouter(prefix="/api/admin", tags=["admin"])
log = logging.getLogger(__name__)


class ResetRequest(BaseModel):
    confirm: str


def _admin_key() -> str | None:
    """Prefer a dedicated reset key so we don't have to expose SECRET_KEY,
    but accept SECRET_KEY if no ADMIN_RESET_KEY is set."""
    return os.getenv("ADMIN_RESET_KEY") or os.getenv("SECRET_KEY")


@router.post("/reset-db")
def reset_db(
    payload: ResetRequest,
    x_admin_reset_key: str = Header(default=""),
):
    """Drop every table SQLAlchemy knows about and recreate the schema empty.

    Auth: header `X-Admin-Reset-Key` must match ADMIN_RESET_KEY (or SECRET_KEY).
    Safety: body must be `{"confirm": "DROP_ALL_DATA"}` to prevent fat-fingering.
    """
    expected = _admin_key()
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Reset endpoint disabled — neither ADMIN_RESET_KEY nor SECRET_KEY set",
        )
    if not secrets.compare_digest(x_admin_reset_key or "", expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid reset key")
    if payload.confirm != "DROP_ALL_DATA":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            'Missing confirmation: send {"confirm": "DROP_ALL_DATA"}',
        )

    tables = sorted(Base.metadata.tables.keys())
    log.warning("admin/reset-db invoked, dropping tables: %s", tables)

    Base.metadata.drop_all(bind=engine)
    init_db()  # recreates schema + runs the lightweight migrations

    return {
        "status": "ok",
        "dropped": tables,
        "recreated": sorted(Base.metadata.tables.keys()),
    }
