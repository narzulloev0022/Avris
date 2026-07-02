"""Append-only audit trail helper.

Call audit() AFTER the endpoint's own commit — the helper commits its own row
and must never break the request that triggered it (best-effort by design).
meta must stay PHI-free: field names, statuses, routes — never values.
"""
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from models import AuditLog

log = logging.getLogger("avris.audit")


def audit(db: Session, *, action: str, entity: str,
          user_id: Optional[int] = None,
          entity_id: Optional[Any] = None,
          meta: Optional[dict] = None) -> None:
    try:
        db.add(AuditLog(
            user_id=user_id,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            meta=meta,
        ))
        db.commit()
    except Exception:
        log.warning("audit write failed: %s %s", action, entity, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
