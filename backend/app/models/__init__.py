"""SQLAlchemy ORM models.

Every workspace-owned table carries a ``workspace_id`` column so that queries can always be scoped to the
caller's workspace (defence in depth against IDOR even when a row is reached through a relationship).
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every backend (SQLite returns naive values otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, native_enum=False, length=32, validate_strings=True,
                values_callable=lambda x: [m.value for m in x])


class Role(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    analyst = "analyst"
    viewer = "viewer"


ROLE_RANK = {Role.viewer: 0, Role.analyst: 1, Role.admin: 2, Role.owner: 3}


class ContractStatus(enum.StrEnum):
    uploaded = "uploaded"
    queued = "queued"
    processing = "processing"
    analyzed = "analyzed"
    failed = "failed"


class RiskLevel(enum.StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    informational = "informational"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _ws_fk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)


def _contract_fk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)


# --- Identity -------------------------------------------------------------------------------------------------

class User(Timestamped, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    memberships: Mapped[list[WorkspaceMembership]] = relationship(back_populates="user", cascade="all, delete-orphan")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    user_agent: Mapped[str] = mapped_column(String(255), default="", nullable=False)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


# --- Workspaces -----------------------------------------------------------------------------------------------

class Workspace(Timestamped, Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    retention_days: Mapped[int | None] = mapped_column(Integer)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ai_provider: Mapped[str | None] = mapped_column(String(20))  # overrides the server default
    ai_model: Mapped[str | None] = mapped_column(String(100))
    ai_api_key_encrypted: Mapped[str | None] = mapped_column(Text)

    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    __table_args__ = (CheckConstraint("retention_days IS NULL OR retention_days >= 1", name="ck_ws_retention"),)


class WorkspaceMembership(Timestamped, Base):
    __tablename__ = "workspace_memberships"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "role"), nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_membership"),)


class WorkspaceInvitation(Base):
    __tablename__ = "workspace_invitations"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "role"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class Folder(Timestamped, Base):
    __tablename__ = "folders"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_folder_name"),)


# --- Contracts & documents ------------------------------------------------------------------------------------

class Contract(Timestamped, Base):
    __tablename__ = "contracts"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    folder_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("folders.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[ContractStatus] = mapped_column(
        _enum(ContractStatus, "contract_status"), default=ContractStatus.uploaded, nullable=False
    )
    contract_type: Mapped[str | None] = mapped_column(String(80))
    risk_level: Mapped[RiskLevel | None] = mapped_column(_enum(RiskLevel, "risk_level"))
    risk_score: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(String(500))
    analyzed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expiration_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))

    document: Mapped[Document | None] = relationship(back_populates="contract", uselist=False, cascade="all, delete-orphan")
    folder: Mapped[Folder | None] = relationship()
    __table_args__ = (
        Index("ix_contracts_ws_status", "workspace_id", "status"),
        Index("ix_contracts_ws_risk", "workspace_id", "risk_level"),
        Index("ix_contracts_ws_created", "workspace_id", "created_at"),
        CheckConstraint("risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)", name="ck_contract_score"),
    )


class Document(Timestamped, Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    contract_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("contracts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)  # pdf | docx | txt
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    char_count: Mapped[int | None] = mapped_column(Integer)
    has_page_numbers: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extraction_method: Mapped[str | None] = mapped_column(String(20))  # text | ocr | mixed
    extraction_warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    contract: Mapped[Contract] = relationship(back_populates="document")


class StoredBlob(Base):
    """File bytes for STORAGE_BACKEND=database (private, persistent, transactional)."""

    __tablename__ = "stored_blobs"
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class DocumentChunk(Base):
    """Retrieval units for the document-grounded assistant."""

    __tablename__ = "document_chunks"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    contract_id: Mapped[uuid.UUID] = _contract_fk()
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_ref: Mapped[str | None] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("contract_id", "chunk_index", name="uq_chunk_index"),)


class ExtractedClause(Base):
    __tablename__ = "extracted_clauses"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    contract_id: Mapped[uuid.UUID] = _contract_fk()
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    section_ref: Mapped[str | None] = mapped_column(String(120))
    heading: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)


class AnalysisResult(Timestamped, Base):
    __tablename__ = "analysis_results"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    contract_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("contracts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    engine: Mapped[str] = mapped_column(String(120), nullable=False)  # e.g. "rules" or "rules+openai:gpt-4.1-mini"
    ai_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_error: Mapped[str | None] = mapped_column(String(500))
    input_truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)

    contract_title: Mapped[str | None] = mapped_column(String(300))
    contract_type: Mapped[str | None] = mapped_column(String(80))
    parties: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date)
    expiration_date: Mapped[date | None] = mapped_column(Date)
    term_description: Mapped[str | None] = mapped_column(Text)
    renewal_terms: Mapped[str | None] = mapped_column(Text)
    auto_renewal: Mapped[bool | None] = mapped_column(Boolean)
    notice_period_days: Mapped[int | None] = mapped_column(Integer)
    governing_law: Mapped[str | None] = mapped_column(String(200))
    jurisdiction: Mapped[str | None] = mapped_column(String(200))

    payment_terms: Mapped[str | None] = mapped_column(Text)
    payment_deadlines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    late_payment_penalties: Mapped[str | None] = mapped_column(Text)
    fees: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    financial_obligations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    price_adjustment: Mapped[str | None] = mapped_column(Text)

    clause_overview: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    field_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    risk_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(_enum(RiskLevel, "risk_level"), nullable=False)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class RiskFinding(Base):
    __tablename__ = "risk_findings"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    contract_id: Mapped[uuid.UUID] = _contract_fk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[RiskLevel] = mapped_column(_enum(RiskLevel, "risk_level"), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_ref: Mapped[str | None] = mapped_column(String(120))
    excerpt: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggested_actions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # rule | ai
    rule_id: Mapped[str | None] = mapped_column(String(60))
    evidence_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_findings_ws_severity", "workspace_id", "severity"),)


class Obligation(Base):
    __tablename__ = "obligations"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    contract_id: Mapped[uuid.UUID] = _contract_fk()
    party: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    obligation_type: Mapped[str] = mapped_column(String(40), nullable=False, default="general")
    due_description: Mapped[str | None] = mapped_column(String(300))
    trigger_condition: Mapped[str | None] = mapped_column(Text)
    consequence: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_ref: Mapped[str | None] = mapped_column(String(120))
    excerpt: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    evidence_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Deadline(Timestamped, Base):
    __tablename__ = "deadlines"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("contracts.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    deadline_type: Mapped[str] = mapped_column(String(20), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="open", nullable=False)
    reminder_days: Mapped[list[int]] = mapped_column(JSON, default=lambda: [30, 7, 1], nullable=False)
    source: Mapped[str] = mapped_column(String(10), default="manual", nullable=False)  # manual | auto
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    __table_args__ = (
        Index("ix_deadlines_ws_due", "workspace_id", "due_date"),
        CheckConstraint("status IN ('open','done','dismissed')", name="ck_deadline_status"),
    )


class DeadlineReminder(Base):
    """Records reminders already delivered so that each (deadline, offset) fires once."""

    __tablename__ = "deadline_reminders"
    id: Mapped[uuid.UUID] = _pk()
    deadline_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("deadlines.id", ondelete="CASCADE"), index=True)
    days_before: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("deadline_id", "days_before", name="uq_reminder_once"),)


# --- Assistant ------------------------------------------------------------------------------------------------

class Conversation(Timestamped, Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    contract_id: Mapped[uuid.UUID] = _contract_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    interpretation: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    answer_found: Mapped[bool | None] = mapped_column(Boolean)
    mode: Mapped[str | None] = mapped_column(String(20))  # ai | extractive
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),)


# --- Reports, notifications, audit, jobs ----------------------------------------------------------------------

class Report(Base):
    __tablename__ = "reports"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    contract_id: Mapped[uuid.UUID] = _contract_fk()
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (CheckConstraint("format IN ('pdf','csv','json')", name="ck_report_format"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    link: Mapped[str | None] = mapped_column(String(300))
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    emailed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False, index=True)


class Job(Base):
    """Durable, database-backed job queue (survives restarts; no Redis required)."""

    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("contracts.id", ondelete="CASCADE"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), default=JobStatus.queued, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(120))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    locked_by: Mapped[str | None] = mapped_column(String(80))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (
        Index("ix_jobs_pick", "status", "run_after"),
        # At most one queued/running job per dedupe key (prevents duplicate analysis of a contract).
        Index(
            "uq_jobs_active_dedupe", "dedupe_key", unique=True,
            postgresql_where=text("status IN ('queued','running') AND dedupe_key IS NOT NULL"),
            sqlite_where=text("status IN ('queued','running') AND dedupe_key IS NOT NULL"),
        ),
    )


class SchedulerState(Base):
    """Last-run timestamps for periodic tasks, shared by all worker processes."""

    __tablename__ = "scheduler_state"
    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    last_run_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


__all__ = [
    "ROLE_RANK",
    "AnalysisResult",
    "AuditEvent",
    "Base",
    "Contract",
    "ContractStatus",
    "Conversation",
    "ConversationMessage",
    "Deadline",
    "DeadlineReminder",
    "Document",
    "DocumentChunk",
    "ExtractedClause",
    "Folder",
    "Job",
    "JobStatus",
    "Notification",
    "Obligation",
    "PasswordResetToken",
    "RefreshToken",
    "Report",
    "RiskFinding",
    "RiskLevel",
    "Role",
    "SchedulerState",
    "StoredBlob",
    "User",
    "Workspace",
    "WorkspaceInvitation",
    "WorkspaceMembership",
    "utcnow",
]
