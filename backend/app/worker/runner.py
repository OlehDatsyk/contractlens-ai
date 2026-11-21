"""Background worker: document analysis, scheduled reminders, retention and housekeeping.

Run standalone with ``python -m app.worker`` or embedded in the API process (RUN_WORKER_IN_API=true).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import (
    Contract,
    ContractStatus,
    Deadline,
    DeadlineReminder,
    Job,
    JobStatus,
    PasswordResetToken,
    RefreshToken,
    SchedulerState,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services import audit, notifications
from app.services.analysis import AnalysisFailed, analyze_contract
from app.services.contracts import delete_blobs, delete_contract
from app.services.storage import StorageError, get_storage
from app.worker.queue import claim, recover_stale

log = logging.getLogger("contractlens.worker")


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


# --- Job handlers -------------------------------------------------------------------------------------------

def _run_analysis(db: Session, job: Job, isolate: bool) -> None:
    contract = db.get(Contract, job.contract_id) if job.contract_id else None
    if contract is None:
        return  # deleted in the meantime
    contract.status = ContractStatus.processing
    db.commit()
    analyze_contract(db, contract.id, isolate=isolate)


def _run_delete_blobs(db: Session, job: Job, isolate: bool) -> None:
    remaining = []
    for key in job.payload.get("keys", []):
        try:
            get_storage().delete(key)
        except StorageError:
            remaining.append(key)
    if remaining:
        job.payload = {"keys": remaining}
        raise RuntimeError(f"{len(remaining)} file(s) could not be deleted yet")


HANDLERS: dict[str, Callable[[Session, Job, bool], None]] = {
    "analyze_contract": _run_analysis,
    "delete_blobs": _run_delete_blobs,
}


def process_job(job_id: uuid.UUID, isolate: bool = True) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        handler = HANDLERS.get(job.kind)
        if handler is None:
            job.status, job.last_error, job.finished_at = JobStatus.failed, "Unknown job type", datetime.now(UTC)
            db.commit()
            return
        try:
            handler(db, job, isolate)
            job.status = JobStatus.succeeded
            job.finished_at = datetime.now(UTC)
            job.last_error = None
            db.commit()
        except AnalysisFailed as exc:
            # Deterministic failure (invalid document etc.): do not retry, keep no partial analysis.
            db.rollback()
            job = db.get(Job, job_id)
            if job is None:  # removed together with its contract
                db.commit()
                return
            job.status, job.finished_at, job.last_error = JobStatus.failed, datetime.now(UTC), str(exc)[:500]
            if job.contract_id and (c := db.get(Contract, job.contract_id)):
                c.status = ContractStatus.failed
                c.error_message = str(exc)[:500]
                if c.created_by:
                    notifications.notify(db, c.created_by, c.workspace_id, "analysis_failed", f"Analysis failed: {c.title}",
                                         str(exc)[:300], f"/app/contracts/{c.id}")
            db.commit()
        except Exception as exc:
            db.rollback()
            log.exception("Job %s (%s) failed", job_id, type(exc).__name__)
            job = db.get(Job, job_id)
            if job is None:  # removed together with its contract
                db.commit()
                return
            job.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            job.locked_at = job.locked_by = None
            contract = db.get(Contract, job.contract_id) if job.contract_id else None
            if job.attempts < job.max_attempts:
                job.status = JobStatus.queued
                job.run_after = datetime.now(UTC) + timedelta(seconds=min(600, 15 * 2 ** job.attempts))
                if contract:
                    contract.status = ContractStatus.queued
            else:
                job.status, job.finished_at = JobStatus.failed, datetime.now(UTC)
                if contract:
                    contract.status = ContractStatus.failed
                    contract.error_message = "Processing failed unexpectedly. Please try again later."
            db.commit()
    finally:
        db.close()


# --- Periodic tasks -----------------------------------------------------------------------------------------

def _due(db: Session, name: str, every: timedelta) -> bool:
    """Atomically claim a periodic task slot (safe with several worker processes)."""
    now = datetime.now(UTC)
    state = db.scalar(select(SchedulerState).where(SchedulerState.name == name).with_for_update(skip_locked=True))
    if state is None:
        db.add(SchedulerState(name=name, last_run_at=now))
        try:
            db.commit()
        except Exception:  # noqa: BLE001 - another worker inserted it first
            db.rollback()
            return False
        return True
    if now - state.last_run_at < every:
        db.rollback()
        return False
    state.last_run_at = now
    db.commit()
    return True


def send_reminders(db: Session, today: date | None = None) -> int:
    today = today or date.today()
    sent = 0
    horizon = today + timedelta(days=366)
    deadlines = list(db.scalars(select(Deadline).where(Deadline.status == "open", Deadline.due_date >= today,
                                                       Deadline.due_date <= horizon)))
    for dl in deadlines:
        offsets = sorted({int(d) for d in (dl.reminder_days or []) if 0 <= int(d) <= 365})
        applicable = [d for d in offsets if dl.due_date - timedelta(days=d) <= today]
        if not applicable:
            continue
        done = set(db.scalars(select(DeadlineReminder.days_before).where(DeadlineReminder.deadline_id == dl.id)))
        pending = [d for d in applicable if d not in done]
        if not pending:
            continue
        for d in pending:
            db.add(DeadlineReminder(deadline_id=dl.id, days_before=d))
        days_left = (dl.due_date - today).days
        title = f"Deadline in {days_left} day(s): {dl.title}" if days_left else f"Deadline today: {dl.title}"
        members = db.execute(select(User).join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
                             .where(WorkspaceMembership.workspace_id == dl.workspace_id, User.is_active.is_(True))).scalars()
        link = f"/app/contracts/{dl.contract_id}" if dl.contract_id else "/app/deadlines"
        for user in members:
            n = notifications.notify(db, user.id, dl.workspace_id, "deadline_reminder", title,
                                     f"Due on {dl.due_date.isoformat()}.", link)
            if user.email_notifications and notifications.send_email(
                user.email, f"[ContractLens] {title}",
                f"{title}\nDue date: {dl.due_date.isoformat()}\n\nOpen ContractLens to review: {get_settings().frontend_url}{link}\n",
            ):
                n.emailed = True
        sent += 1
        db.commit()
    return sent


def apply_retention(db: Session) -> int:
    removed = 0
    for ws in db.scalars(select(Workspace).where(Workspace.retention_days.is_not(None))).all():
        cutoff = datetime.now(UTC) - timedelta(days=int(ws.retention_days or 0))
        for contract in db.scalars(select(Contract).where(Contract.workspace_id == ws.id, Contract.created_at < cutoff)).all():
            keys = delete_contract(db, contract)
            audit.record(db, "contract.retention_delete", workspace_id=ws.id, target_type="contract", target_id=contract.id,
                         details={"retention_days": ws.retention_days})
            db.commit()
            delete_blobs(db, keys)
            db.commit()
            removed += 1
    return removed


def housekeeping(db: Session) -> None:
    now = datetime.now(UTC)
    db.execute(delete(RefreshToken).where(RefreshToken.expires_at < now - timedelta(days=1)))
    db.execute(delete(PasswordResetToken).where(PasswordResetToken.expires_at < now - timedelta(days=1)))
    db.execute(delete(Job).where(Job.status.in_([JobStatus.succeeded, JobStatus.failed]),
                                 Job.finished_at < now - timedelta(days=14)))
    db.commit()


def run_periodic(db: Session) -> None:
    s = get_settings()
    try:
        recover_stale(db)
        if _due(db, "reminders", timedelta(minutes=s.reminder_check_minutes)):
            send_reminders(db)
        if _due(db, "retention", timedelta(minutes=s.retention_check_minutes)):
            apply_retention(db)
        if _due(db, "housekeeping", timedelta(hours=12)):
            housekeeping(db)
    except Exception:
        db.rollback()
        log.exception("Periodic task failed")


# --- Main loop ----------------------------------------------------------------------------------------------

def run_once(isolate: bool = True) -> bool:
    """Process at most one job. Returns True when a job was processed."""
    db = SessionLocal()
    try:
        job = claim(db, worker_id())
        job_id = job.id if job else None
    finally:
        db.close()
    if job_id is None:
        return False
    process_job(job_id, isolate=isolate)
    return True


def run_forever(stop: threading.Event | None = None) -> None:
    s = get_settings()
    stop = stop or threading.Event()
    log.info("Worker started (%s)", worker_id())
    last_periodic = 0.0
    while not stop.is_set():
        try:
            if time.monotonic() - last_periodic > 60:
                db = SessionLocal()
                try:
                    run_periodic(db)
                finally:
                    db.close()
                last_periodic = time.monotonic()
            if not run_once():
                stop.wait(s.worker_poll_seconds)
        except Exception:
            log.exception("Worker loop error; retrying shortly")
            stop.wait(5)
    log.info("Worker stopped")
