"""Durable job queue on top of the application database.

* Jobs survive restarts (they are rows, not in-memory tasks).
* ``SELECT ... FOR UPDATE SKIP LOCKED`` lets several workers run safely on PostgreSQL.
* A partial unique index on ``dedupe_key`` guarantees at most one queued/running analysis per contract.
* Jobs whose worker died are recovered after ``JOB_LOCK_TIMEOUT_SECONDS``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Contract, ContractStatus, Job, JobStatus


def enqueue(db: Session, kind: str, *, workspace_id: uuid.UUID | None = None, contract_id: uuid.UUID | None = None,
            payload: dict[str, Any] | None = None, dedupe_key: str | None = None, max_attempts: int = 3,
            delay_seconds: int = 0) -> Job:
    if dedupe_key:
        existing = db.scalar(select(Job).where(Job.dedupe_key == dedupe_key,
                                               Job.status.in_([JobStatus.queued, JobStatus.running])))
        if existing:
            return existing
    job = Job(kind=kind, workspace_id=workspace_id, contract_id=contract_id, payload=payload or {},
              dedupe_key=dedupe_key, max_attempts=max_attempts,
              run_after=datetime.now(UTC) + timedelta(seconds=delay_seconds))
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(Job).where(Job.dedupe_key == dedupe_key,
                                               Job.status.in_([JobStatus.queued, JobStatus.running])))
        if existing is None:
            raise
        return existing
    return job


def enqueue_analysis(db: Session, contract: Contract) -> Job:
    job = enqueue(db, "analyze_contract", workspace_id=contract.workspace_id, contract_id=contract.id,
                  dedupe_key=f"analyze:{contract.id}", max_attempts=3)
    if contract.status != ContractStatus.processing:
        contract.status = ContractStatus.queued
        contract.error_message = None
    return job


def claim(db: Session, worker_id: str) -> Job | None:
    now = datetime.now(UTC)
    job = db.scalar(
        select(Job).where(Job.status == JobStatus.queued, Job.run_after <= now)
        .order_by(Job.run_after, Job.created_at).limit(1).with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = JobStatus.running
    job.locked_at = now
    job.locked_by = worker_id[:80]
    job.attempts += 1
    db.commit()
    return job


def recover_stale(db: Session) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=get_settings().job_lock_timeout_seconds)
    stale = list(db.scalars(select(Job).where(Job.status == JobStatus.running, Job.locked_at < cutoff).with_for_update(skip_locked=True)))
    for job in stale:
        if job.attempts < job.max_attempts:
            job.status = JobStatus.queued
            job.locked_at = None
            job.locked_by = None
            job.last_error = "Recovered after worker interruption"
            if job.contract_id and (c := db.get(Contract, job.contract_id)) and c.status == ContractStatus.processing:
                c.status = ContractStatus.queued
        else:
            job.status = JobStatus.failed
            job.finished_at = datetime.now(UTC)
            job.last_error = "Worker interrupted too many times"
            if job.contract_id and (c := db.get(Contract, job.contract_id)):
                c.status = ContractStatus.failed
                c.error_message = "Processing was interrupted repeatedly. Try analysing the contract again."
    db.commit()
    return len(stale)
