"""Contract lifecycle helpers shared by the API and the worker (secure deletion)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contract, Document, Report
from app.services.storage import StorageError, get_storage

log = logging.getLogger(__name__)


def storage_keys_for(db: Session, contract: Contract) -> list[str]:
    keys = list(db.scalars(select(Document.storage_key).where(Document.contract_id == contract.id)))
    keys += list(db.scalars(select(Report.storage_key).where(Report.contract_id == contract.id)))
    return keys


def delete_blobs(db: Session, keys: list[str]) -> None:
    """Delete stored files; anything that fails is retried by a background job."""
    from app.worker.queue import enqueue

    failed = []
    for key in keys:
        try:
            get_storage().delete(key)
        except StorageError:
            failed.append(key)
    if failed:
        log.warning("Deferring deletion of %d stored file(s) to the worker", len(failed))
        enqueue(db, "delete_blobs", payload={"keys": failed}, max_attempts=10, delay_seconds=30)


def delete_contract(db: Session, contract: Contract) -> list[str]:
    """Delete the contract and all derived data (cascades). Returns storage keys to remove after commit."""
    keys = storage_keys_for(db, contract)
    db.delete(contract)
    db.flush()
    return keys
