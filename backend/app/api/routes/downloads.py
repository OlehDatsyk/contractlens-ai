"""Signed, short-lived download links for documents and reports.

The token binds (object, user, workspace) and expires quickly. On use, the server re-checks that the user is
still an active member of the workspace and that the object still belongs to it.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import DB
from app.core.rate_limit import client_ip
from app.core.security import verify_download
from app.models import Document, Report, User, WorkspaceMembership
from app.services import audit
from app.services.storage import StorageError, get_storage

router = APIRouter(tags=["downloads"])
REPORT_TYPES = {"pdf": "application/pdf", "csv": "text/csv; charset=utf-8", "json": "application/json"}


def _disposition(kind: str, filename: str) -> str:
    ascii_name = "".join(c if c.isascii() and c.isprintable() and c not in '"\\;' else "_" for c in filename) or "download"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/downloads/{token}")
def download(token: str, request: Request, db: DB, inline: bool = Query(False)) -> Response:
    parsed = verify_download(token)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, "This download link is invalid or has expired")
    if parsed is None:
        raise not_found
    kind, object_id, user_id, workspace_id = parsed
    user = db.get(User, user_id)
    member = db.scalar(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace_id,
                                                         WorkspaceMembership.user_id == user_id))
    if user is None or not user.is_active or member is None:
        raise not_found
    if kind == "doc":
        doc = db.scalar(select(Document).where(Document.id == object_id, Document.workspace_id == workspace_id))
        if doc is None:
            raise not_found
        key, filename, ctype = doc.storage_key, doc.original_filename, doc.mime_type
        target_type = "document"
        allow_inline = doc.file_type in ("pdf", "txt")
    elif kind == "report":
        rep = db.scalar(select(Report).where(Report.id == object_id, Report.workspace_id == workspace_id))
        if rep is None:
            raise not_found
        key, filename, ctype = rep.storage_key, f"{rep.title}.{rep.format}", REPORT_TYPES[rep.format]
        target_type = "report"
        allow_inline = rep.format == "pdf"
    else:
        raise not_found
    try:
        data = get_storage().get(key)
    except StorageError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The file could not be retrieved from storage") from exc
    audit.record(db, f"{target_type}.download", user_id=user_id, workspace_id=workspace_id, target_type=target_type,
                 target_id=object_id, ip=client_ip(request))
    db.commit()
    if ctype == "text/plain":
        ctype = "text/plain; charset=utf-8"
    return Response(
        content=data, media_type=ctype,
        headers={
            "Content-Disposition": _disposition("inline" if inline and allow_inline else "attachment", filename),
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'; plugin-types application/pdf",
            "Referrer-Policy": "no-referrer",
        },
    )
