"""Dashboard metrics, contract comparison, deadlines/calendar, reports and notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import case, func, select

from app.api.deps import DB, AnalystCtx, CurrentUser, ViewerCtx, get_contract_or_404
from app.core.config import get_settings
from app.core.security import sign_download
from app.models import (
    Contract,
    ContractStatus,
    Deadline,
    Notification,
    Report,
    RiskLevel,
    Role,
)
from app.schemas import (
    ContractOut,
    DeadlineIn,
    DeadlineOut,
    DeadlineUpdate,
    DownloadLinkOut,
    NotificationOut,
    ReportIn,
    ReportOut,
)
from app.services import audit, comparison, reports
from app.services.storage import StorageError, get_storage, new_key

router = APIRouter(tags=["insights"])


# --- Dashboard ----------------------------------------------------------------------------------------------

@router.get("/workspaces/{workspace_id}/dashboard")
def dashboard(ctx: ViewerCtx, db: DB) -> dict[str, object]:
    ws = ctx.id
    row = db.execute(select(
        func.count(Contract.id),
        func.sum(case((Contract.status == ContractStatus.analyzed, 1), else_=0)),
        func.sum(case((Contract.risk_level.in_([RiskLevel.critical, RiskLevel.high]), 1), else_=0)),
        func.sum(case((Contract.risk_level == RiskLevel.medium, 1), else_=0)),
        func.sum(case((Contract.risk_level.in_([RiskLevel.low, RiskLevel.informational]), 1), else_=0)),
        func.sum(case((Contract.status.in_([ContractStatus.queued, ContractStatus.processing]), 1), else_=0)),
        func.sum(case((Contract.status == ContractStatus.failed, 1), else_=0)),
    ).where(Contract.workspace_id == ws)).one()
    total, analyzed, high, medium, low, processing, failed = (int(x or 0) for x in row)
    today = date.today()
    upcoming = db.execute(select(Deadline, Contract.title).outerjoin(Contract, Contract.id == Deadline.contract_id)
                          .where(Deadline.workspace_id == ws, Deadline.status == "open", Deadline.due_date >= today,
                                 Deadline.due_date <= today + timedelta(days=90))
                          .order_by(Deadline.due_date).limit(8)).all()
    recent = list(db.scalars(select(Contract).where(Contract.workspace_id == ws, Contract.status == ContractStatus.analyzed)
                             .order_by(Contract.analyzed_at.desc()).limit(6)))
    since = datetime.now(UTC) - timedelta(days=89)
    uploads = db.execute(select(Contract.created_at).where(Contract.workspace_id == ws, Contract.created_at >= since)).scalars().all()
    analyses = db.execute(select(Contract.analyzed_at).where(Contract.workspace_id == ws, Contract.analyzed_at.is_not(None),
                                                             Contract.analyzed_at >= since)).scalars().all()
    weeks: dict[str, dict[str, int]] = {}
    for i in range(13):
        start = (today - timedelta(days=today.weekday())) - timedelta(weeks=12 - i)
        weeks[start.isoformat()] = {"uploaded": 0, "analyzed": 0}

    def bucket(dt: datetime) -> str:
        d = dt.date()
        return (d - timedelta(days=d.weekday())).isoformat()

    for dt in uploads:
        if (k := bucket(dt)) in weeks:
            weeks[k]["uploaded"] += 1
    for adt in analyses:
        if adt and (k := bucket(adt)) in weeks:
            weeks[k]["analyzed"] += 1
    dist: dict[RiskLevel | None, int] = dict(db.execute(select(Contract.risk_level, func.count()).where(Contract.workspace_id == ws, Contract.risk_level.is_not(None))
                           .group_by(Contract.risk_level)).tuples().all())
    return {
        "totals": {"contracts": total, "analyzed": analyzed, "high_risk": high, "medium_risk": medium, "low_risk": low,
                   "processing": processing, "failed": failed,
                   "upcoming_deadlines": db.scalar(select(func.count()).select_from(Deadline).where(
                       Deadline.workspace_id == ws, Deadline.status == "open", Deadline.due_date >= today,
                       Deadline.due_date <= today + timedelta(days=30))) or 0},
        "risk_distribution": [{"level": lvl.value, "count": int(dist.get(lvl, 0))} for lvl in
                              (RiskLevel.critical, RiskLevel.high, RiskLevel.medium, RiskLevel.low)],
        "activity": [{"week": k, **v} for k, v in weeks.items()],
        "upcoming_deadlines": [{"id": str(d.id), "title": d.title, "due_date": d.due_date.isoformat(), "type": d.deadline_type,
                                "contract_id": str(d.contract_id) if d.contract_id else None, "contract_title": t}
                               for d, t in upcoming],
        "recent": [ContractOut.model_validate(c).model_dump(mode="json") for c in recent],
    }


# --- Comparison ---------------------------------------------------------------------------------------------

@router.get("/workspaces/{workspace_id}/compare")
def compare_contracts(ctx: ViewerCtx, db: DB, a: uuid.UUID, b: uuid.UUID) -> dict[str, object]:
    if a == b:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose two different contracts")
    ca, cb = get_contract_or_404(db, ctx, a), get_contract_or_404(db, ctx, b)
    if ca.status != ContractStatus.analyzed or cb.status != ContractStatus.analyzed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Both contracts must be analysed before they can be compared")
    return comparison.compare(db, ctx.id, ca, cb)


# --- Deadlines ----------------------------------------------------------------------------------------------

def _deadline_out(d: Deadline, title: str | None) -> DeadlineOut:
    out = DeadlineOut.model_validate(d)
    out.contract_title = title
    return out


@router.get("/workspaces/{workspace_id}/deadlines", response_model=list[DeadlineOut])
def list_deadlines(ctx: ViewerCtx, db: DB, start: date | None = None, end: date | None = None,
                   status_: str | None = Query(None, alias="status", pattern="^(open|done|dismissed)$"),
                   contract_id: uuid.UUID | None = None) -> list[DeadlineOut]:
    q = (select(Deadline, Contract.title).outerjoin(Contract, Contract.id == Deadline.contract_id)
         .where(Deadline.workspace_id == ctx.id))
    if start:
        q = q.where(Deadline.due_date >= start)
    if end:
        q = q.where(Deadline.due_date <= end)
    if status_:
        q = q.where(Deadline.status == status_)
    if contract_id:
        q = q.where(Deadline.contract_id == contract_id)
    return [_deadline_out(d, t) for d, t in db.execute(q.order_by(Deadline.due_date).limit(1000)).all()]


@router.post("/workspaces/{workspace_id}/deadlines", response_model=DeadlineOut, status_code=201)
def create_deadline(body: DeadlineIn, ctx: AnalystCtx, db: DB) -> DeadlineOut:
    title = None
    if body.contract_id:
        title = get_contract_or_404(db, ctx, body.contract_id).title
    d = Deadline(workspace_id=ctx.id, contract_id=body.contract_id, title=body.title.strip(), deadline_type=body.deadline_type,
                 due_date=body.due_date, description=body.description, reminder_days=body.reminder_days, source="manual",
                 created_by=ctx.user.id)
    db.add(d)
    audit.record(db, "deadline.create", user_id=ctx.user.id, workspace_id=ctx.id, target_type="deadline", ip=ctx.ip)
    db.commit()
    return _deadline_out(d, title)


def _deadline_or_404(db: DB, ctx: ViewerCtx, deadline_id: uuid.UUID) -> Deadline:
    d = db.scalar(select(Deadline).where(Deadline.id == deadline_id, Deadline.workspace_id == ctx.id))
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deadline not found")
    return d


@router.patch("/workspaces/{workspace_id}/deadlines/{deadline_id}", response_model=DeadlineOut)
def update_deadline(deadline_id: uuid.UUID, body: DeadlineUpdate, ctx: AnalystCtx, db: DB) -> DeadlineOut:
    d = _deadline_or_404(db, ctx, deadline_id)
    for field in ("title", "deadline_type", "due_date", "description", "status", "reminder_days"):
        value = getattr(body, field)
        if value is not None:
            setattr(d, field, value)
    db.commit()
    title = db.scalar(select(Contract.title).where(Contract.id == d.contract_id)) if d.contract_id else None
    return _deadline_out(d, title)


@router.delete("/workspaces/{workspace_id}/deadlines/{deadline_id}", status_code=204)
def delete_deadline(deadline_id: uuid.UUID, ctx: AnalystCtx, db: DB) -> Response:
    d = _deadline_or_404(db, ctx, deadline_id)
    db.delete(d)
    audit.record(db, "deadline.delete", user_id=ctx.user.id, workspace_id=ctx.id, target_type="deadline",
                 target_id=deadline_id, ip=ctx.ip)
    db.commit()
    return Response(status_code=204)


# --- Reports ------------------------------------------------------------------------------------------------

@router.get("/workspaces/{workspace_id}/reports", response_model=list[ReportOut])
def list_reports(ctx: ViewerCtx, db: DB, contract_id: uuid.UUID | None = None) -> list[ReportOut]:
    q = select(Report, Contract.title).join(Contract, Contract.id == Report.contract_id).where(Report.workspace_id == ctx.id)
    if contract_id:
        q = q.where(Report.contract_id == contract_id)
    out = []
    for r, t in db.execute(q.order_by(Report.created_at.desc()).limit(200)).all():
        item = ReportOut.model_validate(r)
        item.contract_title = t
        out.append(item)
    return out


@router.post("/workspaces/{workspace_id}/reports", response_model=ReportOut, status_code=201)
def create_report(body: ReportIn, ctx: AnalystCtx, db: DB) -> ReportOut:
    contract = get_contract_or_404(db, ctx, body.contract_id)
    if contract.status != ContractStatus.analyzed:
        raise HTTPException(status.HTTP_409_CONFLICT, "The contract must be analysed before a report can be generated")
    data = reports.build_report_data(db, ctx.id, contract)
    content = {"pdf": reports.render_pdf, "csv": reports.render_csv, "json": reports.render_json}[body.format](data)
    key = new_key("reports", ctx.id)
    ctype = {"pdf": "application/pdf", "csv": "text/csv", "json": "application/json"}[body.format]
    try:
        get_storage().put(key, content, ctype)
    except StorageError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Report storage is unavailable") from exc
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in contract.title)[:120].strip() or "contract"
    report = Report(workspace_id=ctx.id, contract_id=contract.id, created_by=ctx.user.id,
                    title=f"{safe_title} - analysis report {stamp}", format=body.format, storage_key=key, size_bytes=len(content))
    db.add(report)
    audit.record(db, "report.create", user_id=ctx.user.id, workspace_id=ctx.id, target_type="report", ip=ctx.ip,
                 details={"format": body.format, "contract_id": str(contract.id)})
    db.commit()
    out = ReportOut.model_validate(report)
    out.contract_title = contract.title
    return out


def _report_or_404(db: DB, ctx: ViewerCtx, report_id: uuid.UUID) -> Report:
    r = db.scalar(select(Report).where(Report.id == report_id, Report.workspace_id == ctx.id))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return r


@router.post("/workspaces/{workspace_id}/reports/{report_id}/download-link", response_model=DownloadLinkOut)
def report_download_link(report_id: uuid.UUID, ctx: ViewerCtx, db: DB) -> DownloadLinkOut:
    r = _report_or_404(db, ctx, report_id)
    return DownloadLinkOut(url=f"/api/downloads/{sign_download('report', r.id, ctx.user.id, ctx.id)}",
                           expires_in=get_settings().download_link_seconds)


@router.delete("/workspaces/{workspace_id}/reports/{report_id}", status_code=204)
def delete_report(report_id: uuid.UUID, ctx: AnalystCtx, db: DB) -> Response:
    r = _report_or_404(db, ctx, report_id)
    if not ctx.can(Role.admin) and r.created_by != ctx.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Analysts can only delete reports they created")
    key = r.storage_key
    db.delete(r)
    db.commit()
    from app.services.contracts import delete_blobs

    delete_blobs(db, [key])
    db.commit()
    return Response(status_code=204)


# --- Notifications ------------------------------------------------------------------------------------------

@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(user: CurrentUser, db: DB, unread_only: bool = False, limit: int = Query(30, ge=1, le=100)) -> list[Notification]:
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    return list(db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)))


@router.post("/notifications/read", status_code=204)
def mark_read(user: CurrentUser, db: DB, ids: list[uuid.UUID] | None = None) -> Response:
    q = select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if ids:
        q = q.where(Notification.id.in_(ids[:200]))
    for n in db.scalars(q):
        n.read_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=204)
