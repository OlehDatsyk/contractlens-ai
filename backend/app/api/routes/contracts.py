"""Contract upload, listing/search, detail, analysis results, rename/organise, delete and download links."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, time, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import DB, AnalystCtx, ViewerCtx, get_contract_or_404
from app.core.config import get_settings
from app.core.security import sign_download
from app.models import (
    AnalysisResult,
    Contract,
    ContractStatus,
    Document,
    DocumentChunk,
    ExtractedClause,
    Folder,
    Obligation,
    RiskFinding,
    RiskLevel,
    Role,
)
from app.schemas import (
    AnalysisOut,
    ClauseOut,
    ContractOut,
    ContractPage,
    ContractUpdate,
    DownloadLinkOut,
    FindingOut,
    ObligationOut,
)
from app.services import audit
from app.services.contracts import delete_blobs, delete_contract
from app.services.file_validation import FileValidationError, title_from_filename, validate_upload
from app.services.rules import SEVERITY_ORDER
from app.services.storage import StorageError, get_storage, new_key
from app.worker.queue import enqueue_analysis

router = APIRouter(prefix="/workspaces/{workspace_id}/contracts", tags=["contracts"])


def _folder_or_404(db: DB, ctx: ViewerCtx, folder_id: uuid.UUID) -> Folder:
    folder = db.scalar(select(Folder).where(Folder.id == folder_id, Folder.workspace_id == ctx.id))
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    return folder


@router.post("", response_model=ContractOut, status_code=201)
async def upload_contract(
    ctx: AnalystCtx, db: DB,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(max_length=200)] = None,
    folder_id: Annotated[uuid.UUID | None, Form()] = None,
    analyze: Annotated[bool, Form()] = True,
) -> Contract:
    s = get_settings()
    data = await file.read(s.max_upload_bytes + 1)
    await file.close()
    try:
        validated = validate_upload(file.filename, file.content_type, data)
    except FileValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if folder_id:
        _folder_or_404(db, ctx, folder_id)

    key = new_key("documents", ctx.id)
    try:
        get_storage().put(key, data, validated.mime_type)
    except StorageError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Document storage is unavailable. Please try again.") from exc
    try:
        clean_title = " ".join((title or "").split()) or title_from_filename(validated.safe_name)
        contract = Contract(workspace_id=ctx.id, title=clean_title[:200], folder_id=folder_id, created_by=ctx.user.id,
                            status=ContractStatus.uploaded)
        db.add(contract)
        db.flush()
        db.add(Document(workspace_id=ctx.id, contract_id=contract.id, original_filename=validated.safe_name,
                        file_type=validated.file_type, mime_type=validated.mime_type, size_bytes=len(data),
                        sha256=hashlib.sha256(data).hexdigest(), storage_key=key,
                        extraction_warnings=list(validated.warnings)))
        if analyze:
            enqueue_analysis(db, contract)
        audit.record(db, "contract.upload", user_id=ctx.user.id, workspace_id=ctx.id, target_type="contract",
                     target_id=contract.id, ip=ctx.ip, details={"file_type": validated.file_type, "size": len(data)})
        db.commit()
    except Exception:
        db.rollback()
        delete_blobs(db, [key])  # never leave orphaned confidential files behind
        db.commit()
        raise
    db.refresh(contract)
    return contract


SortKey = Literal["created_desc", "created_asc", "title", "risk", "expiration"]


@router.get("", response_model=ContractPage)
def list_contracts(
    ctx: ViewerCtx, db: DB,
    q: Annotated[str | None, Query(max_length=200)] = None,
    search_content: bool = False,
    status_: Annotated[list[ContractStatus] | None, Query(alias="status")] = None,
    risk: Annotated[list[RiskLevel] | None, Query()] = None,
    folder_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: SortKey = "created_desc",
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ContractPage:
    stmt = select(Contract).where(Contract.workspace_id == ctx.id)
    if q:
        like = f"%{q.strip().replace('%', '').replace('_', '')}%"
        conds = [Contract.title.ilike(like), Contract.contract_type.ilike(like),
                 exists().where(Document.contract_id == Contract.id, Document.original_filename.ilike(like))]
        if search_content:
            conds.append(exists().where(DocumentChunk.contract_id == Contract.id, DocumentChunk.workspace_id == ctx.id,
                                        DocumentChunk.text.ilike(like)))
        stmt = stmt.where(or_(*conds))
    if status_:
        stmt = stmt.where(Contract.status.in_(status_))
    if risk:
        stmt = stmt.where(Contract.risk_level.in_(risk))
    if folder_id:
        stmt = stmt.where(Contract.folder_id == folder_id)
    if date_from:
        stmt = stmt.where(Contract.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        stmt = stmt.where(Contract.created_at <= datetime.combine(date_to, time.max, tzinfo=timezone.utc))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    orders: dict[str, list[Any]] = {
        "created_desc": [Contract.created_at.desc()], "created_asc": [Contract.created_at.asc()],
        "title": [func.lower(Contract.title)], "risk": [Contract.risk_score.desc().nulls_last(), Contract.created_at.desc()],
        "expiration": [Contract.expiration_date.asc().nulls_last()],
    }
    order = orders[sort]
    items = list(db.scalars(stmt.options(selectinload(Contract.document)).order_by(*order)
                            .offset((page - 1) * page_size).limit(page_size)))
    return ContractPage(items=[ContractOut.model_validate(c) for c in items], total=total, page=page, page_size=page_size)


@router.get("/{contract_id}", response_model=ContractOut)
def get_contract(contract_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> Contract:
    return get_contract_or_404(db, ctx, contract_id)


@router.patch("/{contract_id}", response_model=ContractOut)
def update_contract(contract_id: uuid.UUID, body: ContractUpdate, ctx: AnalystCtx, db: DB) -> Contract:
    contract = get_contract_or_404(db, ctx, contract_id)
    if body.title is not None:
        contract.title = " ".join(body.title.split())[:200] or contract.title
    if body.clear_folder:
        contract.folder_id = None
    elif body.folder_id is not None:
        contract.folder_id = _folder_or_404(db, ctx, body.folder_id).id
    audit.record(db, "contract.update", user_id=ctx.user.id, workspace_id=ctx.id, target_type="contract",
                 target_id=contract.id, ip=ctx.ip)
    db.commit()
    db.refresh(contract)
    return contract


@router.delete("/{contract_id}", status_code=204)
def remove_contract(contract_id: uuid.UUID, ctx: AnalystCtx, db: DB) -> Response:
    contract = get_contract_or_404(db, ctx, contract_id)
    if not ctx.can(Role.admin) and contract.created_by != ctx.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Analysts can only delete contracts they uploaded")
    audit.record(db, "contract.delete", user_id=ctx.user.id, workspace_id=ctx.id, target_type="contract",
                 target_id=contract.id, ip=ctx.ip)
    keys = delete_contract(db, contract)
    db.commit()
    delete_blobs(db, keys)
    db.commit()
    return Response(status_code=204)


@router.post("/{contract_id}/analyze", response_model=ContractOut, status_code=202)
def reanalyze(contract_id: uuid.UUID, ctx: AnalystCtx, db: DB) -> Contract:
    contract = get_contract_or_404(db, ctx, contract_id)
    enqueue_analysis(db, contract)
    audit.record(db, "contract.analyze", user_id=ctx.user.id, workspace_id=ctx.id, target_type="contract",
                 target_id=contract.id, ip=ctx.ip)
    db.commit()
    db.refresh(contract)
    return contract


@router.post("/{contract_id}/download-link", response_model=DownloadLinkOut)
def download_link(contract_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> DownloadLinkOut:
    contract = get_contract_or_404(db, ctx, contract_id)
    doc = db.scalar(select(Document).where(Document.contract_id == contract.id, Document.workspace_id == ctx.id))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    token = sign_download("doc", doc.id, ctx.user.id, ctx.id)
    return DownloadLinkOut(url=f"/api/downloads/{token}", expires_in=get_settings().download_link_seconds)


@router.get("/{contract_id}/analysis", response_model=AnalysisOut | None)
def get_analysis(contract_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> AnalysisResult | None:
    contract = get_contract_or_404(db, ctx, contract_id)
    return db.scalar(select(AnalysisResult).where(AnalysisResult.contract_id == contract.id, AnalysisResult.workspace_id == ctx.id))


@router.get("/{contract_id}/clauses", response_model=list[ClauseOut])
def get_clauses(contract_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> list[ExtractedClause]:
    contract = get_contract_or_404(db, ctx, contract_id)
    return list(db.scalars(select(ExtractedClause).where(ExtractedClause.contract_id == contract.id,
                                                         ExtractedClause.workspace_id == ctx.id).order_by(ExtractedClause.position)))


@router.get("/{contract_id}/findings", response_model=list[FindingOut])
def get_findings(contract_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> list[RiskFinding]:
    contract = get_contract_or_404(db, ctx, contract_id)
    rows = list(db.scalars(select(RiskFinding).where(RiskFinding.contract_id == contract.id, RiskFinding.workspace_id == ctx.id)))
    rows.sort(key=lambda f: (SEVERITY_ORDER.index(f.severity.value), f.source != "rule", f.page_number or 0))
    return rows


@router.get("/{contract_id}/obligations", response_model=list[ObligationOut])
def get_obligations(contract_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> list[Obligation]:
    contract = get_contract_or_404(db, ctx, contract_id)
    return list(db.scalars(select(Obligation).where(Obligation.contract_id == contract.id, Obligation.workspace_id == ctx.id)
                           .order_by(Obligation.page_number.nulls_last())))
