"""Health checks and capability status (no secrets are ever returned)."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import DB, ViewerCtx
from app.core.config import get_settings
from app.services.ai.config import ai_status
from app.services.extraction import ocr_available
from app.services.storage import get_storage

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: DB, response: Response) -> dict[str, object]:
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        checks["database"] = "ok"
        checks["migrations"] = str(version) if version else "missing"
    except Exception:  # noqa: BLE001 - health probe
        checks["database"] = "unavailable"
    try:
        checks["storage"] = "ok" if get_storage().healthy() else "unavailable"
    except Exception:  # noqa: BLE001
        checks["storage"] = "unavailable"
    healthy = checks.get("database") == "ok" and checks.get("storage") == "ok" and checks.get("migrations") not in (None, "missing")
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "checks": checks}


@router.get("/system/config")
def public_config() -> dict[str, object]:
    s = get_settings()
    return {
        "app_name": s.app_name, "registration_enabled": s.registration_enabled, "environment": s.environment,
        "max_upload_mb": s.max_upload_mb, "max_pdf_pages": s.max_pdf_pages, "allowed_types": ["pdf", "docx", "txt"],
        "email_configured": s.email_configured,
    }


@router.get("/workspaces/{workspace_id}/capabilities")
def capabilities(ctx: ViewerCtx) -> dict[str, object]:
    s = get_settings()
    return {"ai": ai_status(ctx.workspace).as_dict(), "ocr": ocr_available(), "email": s.email_configured,
            "storage": s.storage_backend, "max_upload_mb": s.max_upload_mb, "max_pdf_pages": s.max_pdf_pages}
