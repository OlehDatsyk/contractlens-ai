"""Audit logging. Never records document contents - only actions, identifiers and small metadata."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record(db: Session, action: str, *, user_id: uuid.UUID | None = None, workspace_id: uuid.UUID | None = None,
           target_type: str | None = None, target_id: object | None = None, ip: str | None = None,
           details: dict[str, Any] | None = None) -> None:
    db.add(AuditEvent(
        action=action, user_id=user_id, workspace_id=workspace_id, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None, ip_address=(ip or "")[:64] or None,
        details={k: v for k, v in (details or {}).items() if k not in {"text", "content", "password", "api_key"}},
    ))
